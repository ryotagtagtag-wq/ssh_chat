"""
ssh_chat_server.py  —  Production-ready WebSocket Chat Server (English/Japanese)

Environment Variables:
  ADMIN_IP    : IP address recognized as administrator (Required)
  PORT        : Listen port (Default: 10000)
  LOG_FILE    : Path to log file (Default: /data/messages.txt)
  NTFY_TOPIC  : Topic name for ntfy.sh notifications (Optional)
"""

import asyncio
import logging
import os
import signal
import datetime
import http
import aiohttp
from typing import Optional

from websockets.server import serve

# ------------------------------------------------------------------ #
#  Language Configuration
# ------------------------------------------------------------------ #

MESSAGES = {
    "en": {
        "select_lang": "Select Language: 1) English, 2) 日本語\n> ",
        "welcome": "Welcome to Anonymous Message System v2.0",
        "ip_label": "Your IP: ",
        "user_prompt": "Username (1-20 chars):\n> ",
        "welcome_back": "Welcome back, {name}!",
        "hello": "Hello, {name}!",
        "menu": "Menu: 1) Send Message, 2) Check Replies\n> ",
        "msg_prompt": "Enter your message:\n> ",
        "msg_sent": "Message sent successfully.",
        "no_replies": "No new replies.",
        "reply_header": "Reply:"
    },
    "ja": {
        "select_lang": "言語を選択してください: 1) English, 2) 日本語\n> ",
        "welcome": "匿名メッセージシステム v2.0 へようこそ",
        "ip_label": "あなたのIP: ",
        "user_prompt": "ユーザー名 (1-20文字):\n> ",
        "welcome_back": "おかえりなさい、{name}さん！",
        "hello": "こんにちは、{name}さん！",
        "menu": "メニュー: 1) メッセージ送信, 2) 返信を確認\n> ",
        "msg_prompt": "メッセージを入力してください:\n> ",
        "msg_sent": "メッセージを送信しました。",
        "no_replies": "新しい返信はありません。",
        "reply_header": "返信:"
    }
}

# ------------------------------------------------------------------ #
#  Configuration / Logging
# ------------------------------------------------------------------ #

LOG_FILE = os.environ.get("LOG_FILE", "/data/messages.txt")
ADMIN_IP = os.environ.get("ADMIN_IP", "")
PORT = int(os.environ.get("PORT", "10000"))
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
_file_lock = asyncio.Lock()

# ------------------------------------------------------------------ #
#  Helper Functions (Log/Data)
# ------------------------------------------------------------------ #

async def append_log(line: str) -> None:
    async with _file_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    if NTFY_TOPIC:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=line.encode("utf-8"))
        except Exception:
            pass

def _read_log_lines():
    if not os.path.exists(LOG_FILE): return []
    with open(LOG_FILE, "r", encoding="utf-8") as f: return f.readlines()

def username_exists(username: str) -> bool:
    return any(f"[USER: {username}]" in line for line in _read_log_lines())

def check_username_reply(username: str) -> Optional[str]:
    path = os.path.join(os.path.dirname(LOG_FILE), f"reply_user_{username.replace(' ', '_')}.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return f.read()
    return None

# ------------------------------------------------------------------ #
#  Session Handlers
# ------------------------------------------------------------------ #

def get_client_ip(websocket) -> str:
    # ヘッダーからプロキシ経由のIPを取得
    headers = getattr(websocket, 'request_headers', {})
    for header in ["CF-Connecting-IP", "X-Forwarded-For", "X-Real-IP"]:
        if val := headers.get(header):
            return val.split(",")[0].strip()
    return websocket.remote_address[0]

async def run_guest_session(websocket, ip: str) -> None:
    # 言語選択
    await websocket.send(MESSAGES["en"]["select_lang"])
    lang_choice = (await websocket.recv()).strip()
    lang = "ja" if lang_choice == "2" else "en"
    msgs = MESSAGES[lang]

    await websocket.send(f"{msgs['welcome']}\n{msgs['ip_label']}{ip}\n{msgs['user_prompt']}")
    username = (await websocket.recv()).strip()[:20]
    
    await websocket.send(msgs["welcome_back"].format(name=username) if username_exists(username) else msgs["hello"].format(name=username) + "\n")

    await websocket.send(msgs["menu"])
    choice = (await websocket.recv()).strip()

    if choice == "1":
        await websocket.send(msgs["msg_prompt"])
        body = (await websocket.recv()).strip()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await append_log(f"[{now}] [USER: {username}] [IP: {ip}] DATA: {body}")
        await websocket.send(f"{msgs['msg_sent']}\n")

    elif choice == "2":
        reply = check_username_reply(username)
        await websocket.send(f"\n{reply if reply else msgs['no_replies']}\n")

async def handle_ws(websocket) -> None:
    ip = get_client_ip(websocket)
    if ip == ADMIN_IP:
        await websocket.send("Admin Access Granted.\n")
    else:
        await run_guest_session(websocket, ip)

async def main() -> None:
    async with serve(handle_ws, "0.0.0.0", PORT):
        await asyncio.get_running_loop().create_future()

if __name__ == "__main__":
    asyncio.run(main())
