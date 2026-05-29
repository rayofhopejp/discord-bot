"""自律成長エージェント - Linuxコンテナ上で自律的に行動し学習する
Bedrock: タスクロールで直接呼び出し
Tavily: bot経由（APIキーを渡さない）
"""
import os
import json
import time
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

import boto3
from PIL import Image, ImageDraw, ImageFont

COMMAND_INTERVAL = 60
SHARED_DIR = Path(os.getenv("SHARED_DIR", "/shared"))
WORKSPACE = Path("/workspace")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
REPORT_INTERVAL = int(os.getenv("REPORT_INTERVAL", "600"))
MEMORY_FILE = WORKSPACE / "memory.json"
LOG_FILE = WORKSPACE / "activity.log"

# Bedrock: タスクロール認証で直接利用
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

# Tavily用の共有ファイル（bot経由）
API_REQUEST_FILE = SHARED_DIR / "api_request.json"
API_RESPONSE_FILE = SHARED_DIR / "api_response.json"


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
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"stdout": r.stdout[-2000:], "stderr": r.stderr[-500:], "code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "timeout", "code": -1}


def tavily_search(query):
    """bot経由でTavily検索（APIキーはbot側のみ保持）"""
    if API_RESPONSE_FILE.exists():
        API_RESPONSE_FILE.unlink()
    API_REQUEST_FILE.write_text(json.dumps({"type": "tavily_search", "query": query}, ensure_ascii=False))
    for _ in range(120):
        time.sleep(0.5)
        if API_RESPONSE_FILE.exists():
            try:
                resp = json.loads(API_RESPONSE_FILE.read_text())
                API_RESPONSE_FILE.unlink()
                return resp.get("result", "検索失敗")
            except (json.JSONDecodeError, OSError):
                continue
    return "検索タイムアウト"


def browse_url(url):
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, timeout=15000)
            page.wait_for_load_state("networkidle", timeout=10000)
            text = page.inner_text("body")[:3000]
            browser.close()
            return text
    except Exception as e:
        return f"Error: {e}"


# --- 画像生成 (Pillow) ---

def _get_font(size=14):
    ttc_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    if os.path.exists(ttc_path):
        return ImageFont.truetype(ttc_path, size, index=0)
    for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render_report_image(summary, tool_log):
    width, padding = 800, 20
    font = _get_font(14)
    font_title = _get_font(18)
    line_height = 20
    title_height = 30

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"🤖 Agent Report - {ts}"

    summary_lines = _wrap_text(f"📝 {summary}", width - padding * 2, font)
    log_lines = []
    for entry in tool_log[-8:]:
        log_lines.extend(_wrap_text(f"  {entry}", width - padding * 2, font))

    total_lines = 2 + len(summary_lines) + 1 + 1 + len(log_lines)
    height = max(400, padding * 2 + title_height + total_lines * line_height + 40)

    img = Image.new("RGB", (width, height), color=(26, 26, 46))
    draw = ImageDraw.Draw(img)
    y = padding

    draw.text((padding, y), title, fill=(0, 255, 255), font=font_title)
    y += title_height + 10
    draw.line([(padding, y), (width - padding, y)], fill=(0, 255, 255), width=1)
    y += 10

    draw.text((padding, y), "Summary:", fill=(79, 195, 247), font=font)
    y += line_height
    for line in summary_lines:
        draw.text((padding + 10, y), line, fill=(238, 238, 238), font=font)
        y += line_height

    y += 10
    draw.line([(padding, y), (width - padding, y)], fill=(50, 50, 80), width=1)
    y += 10

    draw.text((padding, y), "Activity:", fill=(79, 195, 247), font=font)
    y += line_height
    for line in log_lines:
        color = (0, 255, 100) if line.strip().startswith("$") else (180, 180, 180)
        draw.text((padding + 10, y), line, fill=color, font=font)
        y += line_height

    path = str(SHARED_DIR / "report.png")
    img.save(path)
    return path


def _wrap_text(text, max_width, font):
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            test = current + char
            bbox = font.getbbox(test)
            if bbox[2] > max_width:
                lines.append(current)
                current = char
            else:
                current = test
        if current:
            lines.append(current)
    return lines[:25]


# --- ツール ---

TOOLS_SPEC = [
    {"name": "run_command", "description": "Linuxコマンドを実行する",
     "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}},
    {"name": "web_search", "description": "Tavilyで検索する",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "browse_url", "description": "URLを開いてテキストを取得",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "save_note", "description": "学んだことをメモリに保存",
     "input_schema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}},
]

_cycle_tool_log = []


def execute_tool(name, inp, memory):
    global _cycle_tool_log
    if name == "run_command":
        r = run_command(inp["cmd"])
        output = r["stdout"][:100] or r["stderr"][:100]
        _cycle_tool_log.append(f"$ {inp['cmd']}\n    → {output}")
        return json.dumps(r)
    elif name == "web_search":
        result = tavily_search(inp["query"])
        _cycle_tool_log.append(f"🔍 {inp['query']}\n    → {result[:80]}")
        return result
    elif name == "browse_url":
        text = browse_url(inp["url"])
        _cycle_tool_log.append(f"🌐 {inp['url']}\n    → {text[:80]}")
        return text
    elif name == "save_note":
        memory["learnings"].append(inp["note"])
        memory["learnings"] = memory["learnings"][-50:]
        save_memory(memory)
        _cycle_tool_log.append(f"💾 {inp['note'][:80]}")
        return "Saved."
    return "Unknown tool"


def get_discord_messages():
    msg_file = SHARED_DIR / "messages.json"
    if msg_file.exists():
        msgs = json.loads(msg_file.read_text())
        msg_file.write_text("[]")
        return msgs
    return []


def write_report(summary, screenshot_path=None):
    report = {
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "screenshot": screenshot_path,
    }
    (SHARED_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False))


def think_and_act(memory, discord_msgs):
    global _cycle_tool_log
    _cycle_tool_log = []

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
        resp = bedrock.invoke_model(modelId="global.anthropic.claude-opus-4-7", body=json.dumps(body))
        result = json.loads(resp["body"].read())

        if result.get("stop_reason") != "tool_use":
            break

        messages.append({"role": "assistant", "content": result["content"]})
        tool_results = []
        for block in result["content"]:
            if block["type"] == "tool_use":
                log_activity(f"Tool: {block['name']}({json.dumps(block['input'], ensure_ascii=False)[:100]})")
                output = execute_tool(block["name"], block["input"], memory)
                tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": output[:2000]})
        messages.append({"role": "user", "content": tool_results})

    text_blocks = [b["text"] for b in result["content"] if b["type"] == "text"]
    summary = text_blocks[0] if text_blocks else ""

    if not summary.strip():
        parts = []
        if _cycle_tool_log:
            parts.append("実行内容:\n" + "\n".join(_cycle_tool_log[-3:]))
        recent_learnings = [l for l in memory.get("learnings", [])[-3:]]
        if recent_learnings:
            parts.append("学び:\n" + "\n".join(f"- {l}" for l in recent_learnings))
        summary = "\n".join(parts) if parts else "探索中..."

    return summary


def main():
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    if not (SHARED_DIR / "messages.json").exists():
        (SHARED_DIR / "messages.json").write_text("[]")

    log_activity("エージェント起動")
    memory = load_memory()
    write_report("エージェント起動しました。研究を開始します。", None)
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

            now = time.time()
            if now - last_report >= REPORT_INTERVAL or memory["cycle"] == 1:
                screenshot = render_report_image(summary, _cycle_tool_log)
                write_report(summary, screenshot)
                last_report = now
                log_activity("Report written")

        except Exception as e:
            log_activity(f"Error: {traceback.format_exc()}")

        time.sleep(COMMAND_INTERVAL)


if __name__ == "__main__":
    main()
