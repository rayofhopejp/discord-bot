import discord
import os
import json
import re
import sqlite3
import traceback
import base64
import boto3
import requests
from dotenv import load_dotenv

load_dotenv('../.env')

TOKEN = os.getenv('TOKEN')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
TAVILY_API_KEY = os.getenv('TAVILY_API_KEY', '')
SERIFU_PATH = '/usr/src/serifu.txt'
SERIFU = open(SERIFU_PATH).read().strip() if os.path.exists(SERIFU_PATH) else ''
ALLOWED_CHANNELS = [ch.strip() for ch in os.getenv('ALLOWED_CHANNELS', '').split(',') if ch.strip()]
DB_PATH = os.getenv('DB_PATH', '/usr/src/app/data/messages.db')

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

bedrock = boto3.client('bedrock-runtime', region_name=AWS_REGION)


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def save_message(user_id, role, content, channel_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO messages (user_id, role, content, channel_id) VALUES (?, ?, ?, ?)",
        (user_id, role, content, channel_id)
    )
    conn.commit()
    conn.close()


def get_context(user_id, channel_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT role, content, user_id FROM messages WHERE channel_id = ? ORDER BY created_at DESC LIMIT 50",
        (channel_id,)
    )
    recent = cur.fetchall()[::-1]
    cur.execute(
        "SELECT content FROM messages WHERE user_id = ? AND role = 'user' ORDER BY created_at",
        (user_id,)
    )
    user_msgs = [r[0] for r in cur.fetchall()]
    conn.close()
    return recent, user_msgs


def tavily_search(query):
    resp = requests.post(
        "https://api.tavily.com/search",
        json={"api_key": TAVILY_API_KEY, "query": query, "max_results": 5}
    )
    results = resp.json().get("results", [])
    return "\n\n".join(f"[{r['title']}]({r['url']})\n{r.get('content','')}" for r in results)


def fetch_urls(urls):
    """Tavily Extract APIでURL先のコンテンツを取得"""
    if not TAVILY_API_KEY or not urls:
        return ""
    try:
        resp = requests.post(
            "https://api.tavily.com/extract",
            json={"api_key": TAVILY_API_KEY, "urls": urls}
        )
        results = resp.json().get("results", [])
        parts = []
        for r in results:
            text = r.get("raw_content", "") or r.get("text", "")
            parts.append(f"[{r.get('url','')}]\n{text[:3000]}")
        return "\n\n---\n\n".join(parts)
    except Exception:
        return ""


TOOLS = [
    {
        "name": "web_search",
        "description": "インターネットで最新情報を検索します。最新のニュース、事実確認、知らない情報を調べる時に使ってください。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "検索クエリ"}
            },
            "required": ["query"]
        }
    }
]


def merge_consecutive_roles(messages):
    """同じroleが連続するメッセージをマージしてClaude APIの交互配置要件を満たす"""
    if not messages:
        return messages
    merged = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == merged[-1]["role"]:
            # 同じroleが連続 → contentをマージ
            prev = merged[-1]["content"]
            curr = msg["content"]
            if isinstance(prev, str) and isinstance(curr, str):
                merged[-1]["content"] = prev + "\n" + curr
            else:
                # リスト形式の場合
                prev_list = prev if isinstance(prev, list) else [{"type": "text", "text": prev}]
                curr_list = curr if isinstance(curr, list) else [{"type": "text", "text": curr}]
                merged[-1]["content"] = prev_list + curr_list
        else:
            merged.append(msg)
    return merged


def ask_claude(user_id, channel_id, prompt, username, images=None, message_content=None):
    recent, user_msgs = get_context(user_id, channel_id)
    message_content = message_content or prompt

    system_parts = [f"現在の発言者のユーザーID: {user_id}, ユーザー名: {username}"]
    if user_msgs:
        system_parts.append(f"このユーザーの過去の発言一覧:\n" + "\n".join(user_msgs[-50:]))
    if SERIFU:
        system_parts.append(f"以下の「うさねこらーじ」のセリフを参考にして、うさねこらーじになりきって返答してください。ただし、1-2行に収まるくらい短く返すこと。\n\n{SERIFU}")
    
    messages = [{"role": r, "content": f"[user_id:{uid}] {c}" if r == "user" else c} for r, c, uid in recent]

    # Build user content with images
    user_content = []
    if images:
        for img_data, media_type in images:
            user_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": img_data}
            })
    user_content.append({"type": "text", "text": prompt or "この画像に対して適切な返しをしてください"})

    # 最後のメッセージが今回の発言ならDB履歴から既に含まれているので差し替え、そうでなければ追加
    if recent and recent[-1][0] == "user" and recent[-1][1] == message_content:
        messages[-1] = {"role": "user", "content": user_content}
    else:
        messages.append({"role": "user", "content": user_content})

    # 同じroleの連続をマージし、最初のメッセージがuserであることを保証
    messages = merge_consecutive_roles(messages)
    if messages and messages[0]["role"] != "user":
        messages = messages[1:]

    def _invoke(msgs):
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": msgs,
            "tools": TOOLS if TAVILY_API_KEY else []
        }
        if system_parts:
            body["system"] = "\n\n---\n\n".join(system_parts)
        resp = bedrock.invoke_model(
            modelId='global.anthropic.claude-sonnet-4-6',
            body=json.dumps(body)
        )
        return json.loads(resp['body'].read())

    # ValidationException時は履歴を削って再試行
    try:
        result = _invoke(messages)

        # Handle tool use (最大2回まで)
        for _ in range(2):
            if result.get("stop_reason") != "tool_use":
                break
            tool_block = next(b for b in result["content"] if b["type"] == "tool_use")
            search_result = tavily_search(tool_block["input"]["query"])

            messages.append({"role": "assistant", "content": result["content"]})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_block["id"], "content": search_result}
            ]})

            result = _invoke(messages)
    except Exception as e:
        if 'ValidationException' in str(type(e).__name__):
            # 履歴を捨てて最後のuserメッセージだけで再試行
            messages = [messages[-1]] if messages else [{"role": "user", "content": prompt}]
            result = _invoke(messages)
        else:
            raise

    text_blocks = [b["text"] for b in result["content"] if b["type"] == "text"]
    return text_blocks[0] if text_blocks else "難しいこと聞きすぎ！！わかんないや😂"


def pick_reaction(text):
    """メッセージに適切な絵文字リアクションを選ぶ"""
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 20,
        "messages": [{"role": "user", "content": text}],
        "system": "このメッセージに合うUnicode絵文字を1つだけ返せ。絵文字以外は一切書くな。"
    }
    try:
        response = bedrock.invoke_model(
            modelId='global.anthropic.claude-sonnet-4-6',
            body=json.dumps(body)
        )
        result = json.loads(response['body'].read())
        emoji = result['content'][0]['text'].strip()
        print(f"リアクション候補: {emoji} (len={len(emoji)})")
        if len(emoji) <= 10:
            return emoji
    except Exception as e:
        print(f"リアクション取得エラー: {e}")
    return None


@client.event
async def on_ready():
    init_db()
    print('ログインしました')


@client.event
async def on_message(message):
    if message.author.bot:
        return
    if ALLOWED_CHANNELS and str(message.channel.id) not in ALLOWED_CHANNELS:
        return

    print(f"メッセージ受信: {message.content[:50]}")

    try:
        user_id = str(message.author.id)
        channel_id = str(message.channel.id)

        save_message(user_id, 'user', message.content, channel_id)

        # Add emoji reaction
        emoji = pick_reaction(message.content)
        if emoji:
            try:
                await message.add_reaction(emoji)
            except Exception:
                pass

        # Download image attachments
        images = []
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                img_bytes = await att.read()
                images.append((base64.b64encode(img_bytes).decode(), att.content_type))

        # Fetch URL content
        urls = re.findall(r'https?://[^\s<>]+', message.content)
        url_content = fetch_urls(urls[:3]) if urls else ""
        prompt = message.content
        if url_content:
            prompt += f"\n\n以下はリンク先の内容です:\n{url_content}"

        async with message.channel.typing():
            reply = ask_claude(user_id, channel_id, prompt, message.author.display_name, images or None, message.content)

        save_message(user_id, 'assistant', reply, channel_id)

        for i in range(0, len(reply), 2000):
            await message.channel.send(reply[i:i+2000])
    except Exception as e:
        print(f"エラー: {traceback.format_exc()}")

client.run(TOKEN)
