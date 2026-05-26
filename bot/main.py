import discord
import os
import json
import sqlite3
import boto3
from dotenv import load_dotenv

load_dotenv('../.env')

TOKEN = os.getenv('TOKEN')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
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
        "SELECT role, content FROM messages WHERE channel_id = ? ORDER BY created_at DESC LIMIT 20",
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


def ask_claude(user_id, channel_id, prompt):
    recent, user_msgs = get_context(user_id, channel_id)

    system_parts = []
    if SERIFU:
        system_parts.append(f"以下のセリフを参考にして、そのキャラクターになりきって返答してください。\n\n{SERIFU}")
    if user_msgs:
        system_parts.append(f"このユーザーの過去の発言一覧:\n" + "\n".join(user_msgs[-50:]))

    messages = [{"role": r, "content": c} for r, c in recent]
    if not messages or messages[-1]["content"] != prompt:
        messages.append({"role": "user", "content": prompt})

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": messages
    }
    if system_parts:
        body["system"] = "\n\n---\n\n".join(system_parts)

    response = bedrock.invoke_model(
        modelId='anthropic.claude-3-5-sonnet-20241022-v2:0',
        body=json.dumps(body)
    )
    result = json.loads(response['body'].read())
    return result['content'][0]['text']


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

    user_id = str(message.author.id)
    channel_id = str(message.channel.id)

    save_message(user_id, 'user', message.content, channel_id)

    async with message.channel.typing():
        reply = ask_claude(user_id, channel_id, message.content)

    save_message(user_id, 'assistant', reply, channel_id)

    for i in range(0, len(reply), 2000):
        await message.reply(reply[i:i+2000])

client.run(TOKEN)
