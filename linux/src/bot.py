"""Discord Bot - エージェントの報告送信 + APIプロキシ（秘密情報はこちらのみ保持）"""
import os
import json
import asyncio
from pathlib import Path
from datetime import datetime

import boto3
import requests
import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("LINUX_BOT_TOKEN") or os.getenv("TOKEN")
REPORT_CHANNEL = os.getenv("REPORT_CHANNEL", "")
SHARED_DIR = Path(os.getenv("SHARED_DIR", "/shared"))
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
CHECK_INTERVAL = 5

SERIFU_PATH = os.getenv("SERIFU_PATH", "/app/serif.txt")
SERIFU = open(SERIFU_PATH).read().strip() if os.path.exists(SERIFU_PATH) else ""

API_REQUEST_FILE = SHARED_DIR / "api_request.json"
API_RESPONSE_FILE = SHARED_DIR / "api_response.json"

bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)


# --- APIプロキシ ---

def handle_llm_request(req):
    """LLMリクエストを処理"""
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "system": req.get("system", ""),
        "messages": req["messages"],
    }
    if req.get("tools"):
        body["tools"] = req["tools"]
    try:
        resp = bedrock.invoke_model(modelId="global.anthropic.claude-sonnet-4-6", body=json.dumps(body))
        return json.loads(resp["body"].read())
    except Exception as e:
        return {"error": str(e), "content": [], "stop_reason": "error"}


def handle_tavily_request(req):
    """Tavily検索リクエストを処理"""
    if not TAVILY_API_KEY:
        return {"result": "Tavily API key not configured"}
    try:
        resp = requests.post("https://api.tavily.com/search",
                             json={"api_key": TAVILY_API_KEY, "query": req["query"], "max_results": 3})
        results = resp.json().get("results", [])
        text = "\n".join(f"- {r['title']}: {r.get('content','')[:200]}" for r in results)
        return {"result": text}
    except Exception as e:
        return {"result": f"Search error: {e}"}


def process_api_request():
    """共有ボリュームのAPIリクエストを処理"""
    if not API_REQUEST_FILE.exists():
        return
    try:
        req = json.loads(API_REQUEST_FILE.read_text())
        API_REQUEST_FILE.unlink()
    except (json.JSONDecodeError, OSError):
        return

    if req.get("type") == "llm":
        result = handle_llm_request(req)
    elif req.get("type") == "tavily_search":
        result = handle_tavily_request(req)
    else:
        result = {"error": "Unknown request type"}

    API_RESPONSE_FILE.write_text(json.dumps(result, ensure_ascii=False))


# --- Discord ---

def rewrite_in_character(text):
    if not SERIFU:
        return text
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "system": f"以下のセリフを参考に、この人物の口調・性格になりきって文章を書き換えてください。内容は変えず要約して口調だけ変えること。短めに。\n\nセリフ集:\n{SERIFU[:3000]}",
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
    report_file = SHARED_DIR / "report.json"
    if not report_file.exists():
        return None
    try:
        return json.loads(report_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def push_message(content):
    msg_file = SHARED_DIR / "messages.json"
    try:
        msgs = json.loads(msg_file.read_text()) if msg_file.exists() else []
    except (json.JSONDecodeError, OSError):
        msgs = []
    msgs.append(content)
    msgs = msgs[-20:]
    msg_file.write_text(json.dumps(msgs, ensure_ascii=False))


async def api_proxy_loop():
    """APIリクエストを0.3秒ごとにポーリング処理"""
    while not client.is_closed():
        try:
            process_api_request()
        except Exception as e:
            print(f"API proxy error: {e}")
        await asyncio.sleep(0.3)


async def report_loop():
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
    client.loop.create_task(api_proxy_loop())


@client.event
async def on_message(message):
    if message.author.bot:
        return
    if REPORT_CHANNEL and str(message.channel.id) != REPORT_CHANNEL:
        return
    push_message(f"{message.author.display_name}: {message.content}")
    await message.add_reaction("👀")


client.run(TOKEN)
