import discord
import os
import json
import asyncio
import re
import sqlite3
import traceback
import base64
import zlib
import boto3
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
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
        "SELECT content FROM messages WHERE user_id = ? AND role = 'user' ORDER BY created_at DESC LIMIT 100",
        (user_id,)
    )
    user_msgs = [r[0] for r in cur.fetchall()][::-1]
    conn.close()
    return recent, user_msgs


def tavily_search(query):
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_API_KEY, "query": query, "max_results": 5},
            timeout=10
        )
        results = resp.json().get("results", [])
        return "\n\n".join(f"[{r['title']}]({r['url']})\n{r.get('content','')}" for r in results) or "検索結果が見つかりませんでした。"
    except Exception as e:
        print(f"Tavily検索エラー: {e}")
        return "検索に失敗しました。知っている情報で回答してください。"


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


def upload_to_excalidraw(elements_json):
    """Excalidraw elements JSONをexcalidraw.comにアップロードしてシェアリンクを返す"""
    scene = json.dumps({
        "type": "excalidraw",
        "version": 2,
        "elements": json.loads(elements_json),
        "appState": {"viewBackgroundColor": "#ffffff"}
    })
    data_bytes = scene.encode()
    file_meta = json.dumps({}).encode()

    # concatBuffers: [version=1 (4B)] [len (4B)] [data] ...
    def concat(*bufs):
        total = 4 + sum(4 + len(b) for b in bufs)
        out = bytearray(total)
        import struct
        struct.pack_into('<I', out, 0, 1)
        offset = 4
        for b in bufs:
            struct.pack_into('<I', out, offset, len(b))
            offset += 4
            out[offset:offset+len(b)] = b
            offset += len(b)
        return bytes(out)

    inner = concat(file_meta, data_bytes)
    compressed = zlib.compress(inner)

    # AES-GCM 128-bit encryption
    key = os.urandom(16)
    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    encrypted = aesgcm.encrypt(iv, compressed, None)

    encoding_meta = json.dumps({"version": 2, "compression": "pako@1", "encryption": "AES-GCM"}).encode()
    payload = concat(encoding_meta, iv, encrypted)

    resp = requests.post("https://json.excalidraw.com/api/v2/post/", data=payload, timeout=15)
    resp.raise_for_status()
    file_id = resp.json()["id"]

    # base64url encode the key (no padding)
    key_b64 = base64.urlsafe_b64encode(key).rstrip(b'=').decode()
    return f"https://excalidraw.com/#json={file_id},{key_b64}"


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
    },
    {
        "name": "draw_diagram",
        "description": "Excalidrawで手書き風の図やダイアグラムを描きます。アーキテクチャ図、フローチャート、シーケンス図、概念図など、視覚的な説明が必要な時に使ってください。elementsはExcalidraw elements形式のJSON配列文字列です。必ずcameraUpdateを最初に入れてください。",
        "input_schema": {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "string",
                    "description": "Excalidraw elements JSON配列文字列。例: [{\"type\":\"cameraUpdate\",\"width\":800,\"height\":600,\"x\":0,\"y\":0},{\"type\":\"rectangle\",\"id\":\"r1\",\"x\":100,\"y\":100,\"width\":200,\"height\":100,\"label\":{\"text\":\"Hello\",\"fontSize\":20}}]"
                }
            },
            "required": ["elements"]
        }
    }
]


def merge_consecutive_roles(messages):
    """同じroleが連続する場合にダミーメッセージを挟んでClaude APIの交互配置要件を満たす"""
    if not messages:
        return messages
    merged = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == merged[-1]["role"]:
            dummy_role = "assistant" if msg["role"] == "user" else "user"
            merged.append({"role": dummy_role, "content": "--"})
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

    def _invoke(msgs, use_tools=True):
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": msgs,
        }
        if use_tools:
            body["tools"] = TOOLS
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
        excalidraw_urls = []

        # Handle tool use (最大2回まで)
        for _ in range(2):
            if result.get("stop_reason") != "tool_use":
                break
            tool_block = next(b for b in result["content"] if b["type"] == "tool_use")
            tool_name = tool_block["name"]

            if tool_name == "web_search":
                tool_result = tavily_search(tool_block["input"]["query"])
            elif tool_name == "draw_diagram":
                try:
                    url = upload_to_excalidraw(tool_block["input"]["elements"])
                    excalidraw_urls.append(url)
                    tool_result = f"図を作成しました: {url}"
                except Exception as e:
                    tool_result = f"図の作成に失敗しました: {e}"
            else:
                tool_result = "Unknown tool"

            messages.append({"role": "assistant", "content": result["content"]})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_block["id"], "content": tool_result}
            ]})

            result = _invoke(messages)
    except Exception as e:
        if 'ValidationException' in str(type(e).__name__):
            # 履歴を捨ててtools無しで再試行（tool_useループを避ける）
            result = _invoke([{"role": "user", "content": user_content}], use_tools=False)
            excalidraw_urls = []
        else:
            raise

    text_blocks = [b["text"] for b in result["content"] if b["type"] == "text"]
    reply = text_blocks[0] if text_blocks else ""
    if excalidraw_urls:
        reply = (reply + "\n" + "\n".join(excalidraw_urls)).strip()
    return reply or "難しいこと聞きすぎ！！わかんないや😂"


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
        emoji = await asyncio.to_thread(pick_reaction, message.content)
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
            reply = await asyncio.to_thread(ask_claude, user_id, channel_id, prompt, message.author.display_name, images or None, message.content)

        save_message(user_id, 'assistant', reply, channel_id)

        for i in range(0, len(reply), 2000):
            await message.channel.send(reply[i:i+2000])
    except Exception as e:
        print(f"エラー: {traceback.format_exc()}")

client.run(TOKEN)
