"""自律成長エージェント - Linuxコンテナ上で自律的に行動し学習する"""
import os
import json
import time
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

import boto3
import requests
from playwright.sync_api import sync_playwright

COMMAND_INTERVAL=30

SHARED_DIR = Path(os.getenv("SHARED_DIR", "/shared"))
WORKSPACE = Path("/workspace")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
REPORT_INTERVAL = int(os.getenv("REPORT_INTERVAL", "600"))  # 10分
MEMORY_FILE = WORKSPACE / "memory.json"
LOG_FILE = WORKSPACE / "activity.log"

bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)


def load_memory():
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return {"goals": [], "learnings": [], "history": [], "cycle": 0}


def save_memory(memory):
    MEMORY_FILE.write_text(json.dumps(memory, ensure_ascii=False, indent=2))


def log_activity(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)
    print(line, end="")


def run_command(cmd, timeout=30):
    """シェルコマンドを実行して結果を返す"""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"stdout": r.stdout[-2000:], "stderr": r.stderr[-500:], "code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "timeout", "code": -1}


def tavily_search(query):
    if not TAVILY_API_KEY:
        return "Tavily API key not set"
    resp = requests.post("https://api.tavily.com/search",
                         json={"api_key": TAVILY_API_KEY, "query": query, "max_results": 3})
    results = resp.json().get("results", [])
    return "\n".join(f"- {r['title']}: {r.get('content','')[:200]}" for r in results)


def browse_url(url):
    """Playwrightでページを開いてスクリーンショットとテキストを取得"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, timeout=15000)
            page.wait_for_load_state("networkidle", timeout=10000)
            screenshot_path = str(SHARED_DIR / "screenshot.png")
            page.screenshot(path=screenshot_path)
            text = page.inner_text("body")[:3000]
            browser.close()
            return {"text": text, "screenshot": screenshot_path}
    except Exception as e:
        return {"text": f"Error: {e}", "screenshot": None}


def take_terminal_screenshot():
    """現在のターミナル状態をスクリーンショットとして保存（neofetch等の出力をHTMLで描画）"""
    result = run_command("uname -a && uptime && df -h / 2>/dev/null || echo 'system ready'")
    html = f"<html><body style='background:#1e1e1e;color:#0f0;font-family:monospace;padding:20px;'><pre>{result['stdout']}</pre></body></html>"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.set_content(html)
            path = str(SHARED_DIR / "report.png")
            page.screenshot(path=path)
            browser.close()
            return path
    except Exception:
        return None


TOOLS_SPEC = [
    {"name": "run_command", "description": "Linuxコマンドを実行する",
     "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}},
    {"name": "web_search", "description": "Tavilyで検索する",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "browse_url", "description": "URLを開いてスクリーンショットとテキストを取得",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "save_note", "description": "学んだことをメモリに保存",
     "input_schema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}},
]


def execute_tool(name, inp, memory):
    if name == "run_command":
        return json.dumps(run_command(inp["cmd"]))
    elif name == "web_search":
        return tavily_search(inp["query"])
    elif name == "browse_url":
        r = browse_url(inp["url"])
        return r["text"]
    elif name == "save_note":
        memory["learnings"].append(inp["note"])
        memory["learnings"] = memory["learnings"][-50:]
        save_memory(memory)
        return "Saved."
    return "Unknown tool"


def get_discord_messages():
    """共有ボリュームからDiscordメッセージを読み取る"""
    msg_file = SHARED_DIR / "messages.json"
    if msg_file.exists():
        msgs = json.loads(msg_file.read_text())
        msg_file.write_text("[]")
        return msgs
    return []


def write_report(summary, screenshot_path=None):
    """報告を共有ボリュームに書き出す"""
    report = {
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "screenshot": screenshot_path,
    }
    report_file = SHARED_DIR / "report.json"
    report_file.write_text(json.dumps(report, ensure_ascii=False))


def think_and_act(memory, discord_msgs):
    """Claudeに考えさせて行動する"""
    system = """あなたはLinuxコンテナ上で動く自律成長エージェントです。
自分で考え、コマンドを実行し、Webを調べ、新しいスキルを身につけていきます。
好奇心を持って探索し、面白いことを見つけたら報告してください。

ルール:
- 安全なコマンドのみ実行（rm -rf / 等の破壊的コマンドは禁止）
- 1サイクルで最大5回までツールを使える
- 学んだことはsave_noteで記録する
- 毎回、何をしたか・何を学んだかを最後にまとめる"""

    learnings = "\n".join(f"- {l}" for l in memory["learnings"][-20:]) if memory["learnings"] else "まだなし"
    recent_history = "\n".join(memory["history"][-5:]) if memory["history"] else "初回起動"

    user_prompt = f"""サイクル #{memory['cycle'] + 1}

過去の学び:
{learnings}

最近の活動:
{recent_history}
"""
    if discord_msgs:
        user_prompt += f"\nDiscordからのメッセージ:\n" + "\n".join(f"- {m}" for m in discord_msgs)
    user_prompt += "\n\n次に何をしますか？好奇心を持って探索してください。"

    messages = [{"role": "user", "content": user_prompt}]

    for _ in range(5):
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": system,
            "messages": messages,
            "tools": TOOLS_SPEC,
        }
        resp = bedrock.invoke_model(modelId="anthropic.claude-sonnet-4-20250514", body=json.dumps(body))
        result = json.loads(resp["body"].read())

        if result.get("stop_reason") != "tool_use":
            break

        # ツール実行
        messages.append({"role": "assistant", "content": result["content"]})
        tool_results = []
        for block in result["content"]:
            if block["type"] == "tool_use":
                log_activity(f"Tool: {block['name']}({json.dumps(block['input'], ensure_ascii=False)[:100]})")
                output = execute_tool(block["name"], block["input"], memory)
                tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": output[:2000]})
        messages.append({"role": "user", "content": tool_results})

    # 最終テキストを取得
    text_blocks = [b["text"] for b in result["content"] if b["type"] == "text"]
    summary = text_blocks[0] if text_blocks else "活動完了"
    return summary


def main():
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (SHARED_DIR / "messages.json").write_text("[]") if not (SHARED_DIR / "messages.json").exists() else None

    log_activity("エージェント起動")
    memory = load_memory()
    last_report = 0

    while True:
        try:
            discord_msgs = get_discord_messages()
            summary = think_and_act(memory, discord_msgs)

            memory["cycle"] += 1
            memory["history"].append(f"Cycle {memory['cycle']}: {summary[:100]}")
            memory["history"] = memory["history"][-30:]
            save_memory(memory)
            log_activity(f"Cycle {memory['cycle']} done: {summary[:80]}")

            # 10分ごとに報告
            now = time.time()
            if now - last_report >= REPORT_INTERVAL or memory["cycle"] == 1:
                screenshot = take_terminal_screenshot()
                write_report(summary, screenshot)
                last_report = now
                log_activity("Report written")

        except Exception as e:
            log_activity(f"Error: {traceback.format_exc()}")

        time.sleep(COMMAND_INTERVAL)  # 1分ごとに行動


if __name__ == "__main__":
    main()
