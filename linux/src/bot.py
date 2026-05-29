"""Discord Bot - 自律エージェントの報告をDiscordに送信し、メッセージをエージェントに渡す"""
import os
import json
import asyncio
from pathlib import Path
from datetime import datetime

import boto3
import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("LINUX_BOT_TOKEN") or os.getenv("TOKEN")
REPORT_CHANNEL = os.getenv("REPORT_CHANNEL", "")
SHARED_DIR = Path(os.getenv("SHARED_DIR", "/shared"))
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
CHECK_INTERVAL = 10

SERIFU_PATH = os.getenv("SERIFU_PATH", "/app/serif.txt")
SERIFU = open(SERIFU_PATH).read().strip() if os.path.exists(SERIFU_PATH) else ""

bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)


def rewrite_in_character(text):
    """報告文をserif.txtの口調に変換"""
    if not SERIFU:
        return text
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "system": f"以下のセリフを参考に、この人物の口調・性格になりきって文章を書き換えてください。内容は変えず口調だけ変えること。短めに。\n\nセリフ集:\n{SERIFU[:3000]}",
        "messages": [{"role": "user", "content": f"以下の報告を口調変換して:\n{text}"}],
    }
    try:
        resp = bedrock.invoke_model(modelId="global.anthropic.claude-sonnet-4-6", body=json.dumps(body))
        result = json.loads(resp["body"].read())
        return result["content"][0]["text"]
    except Exception as e:
        print(f"Rewrite error: {e}")
        return text

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

last_report_time = None


def read_report():
    """共有ボリュームから報告を読み取る"""
    report_file = SHARED_DIR / "report.json"
    if not report_file.exists():
        return None
    try:
        return json.loads(report_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def push_message(content):
    """Discordメッセージをエージェントに渡す"""
    msg_file = SHARED_DIR / "messages.json"
    try:
        msgs = json.loads(msg_file.read_text()) if msg_file.exists() else []
    except (json.JSONDecodeError, OSError):
        msgs = []
    msgs.append(content)
    msgs = msgs[-20:]  # 最新20件のみ保持
    msg_file.write_text(json.dumps(msgs, ensure_ascii=False))


async def report_loop():
    """定期的に報告をチェックしてDiscordに送信"""
    global last_report_time
    await client.wait_until_ready()

    if not REPORT_CHANNEL:
        print("REPORT_CHANNEL not set, report loop disabled")
        return

    channel = client.get_channel(int(REPORT_CHANNEL))
    if not channel:
        print(f"Channel {REPORT_CHANNEL} not found")
        return

    while not client.is_closed():
        try:
            report = read_report()
            if report and report.get("timestamp") != last_report_time:
                last_report_time = report["timestamp"]
                summary = report.get("summary", "活動報告")
                summary = rewrite_in_character(summary)
                ts = datetime.fromisoformat(report["timestamp"]).strftime("%H:%M")
                text = f"\n{summary[:1900]}"

                screenshot = report.get("screenshot")
                if screenshot and Path(screenshot).exists():
                    await channel.send(text, file=discord.File(screenshot))
                else:
                    await channel.send(text)
        except Exception as e:
            print(f"Report loop error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


@client.event
async def on_ready():
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Linux Bot ready: {client.user}")
    client.loop.create_task(report_loop())


@client.event
async def on_message(message):
    if message.author.bot:
        return
    if REPORT_CHANNEL and str(message.channel.id) != REPORT_CHANNEL:
        return

    push_message(f"{message.author.display_name}: {message.content}")
    await message.add_reaction("👀")


client.run(TOKEN)
