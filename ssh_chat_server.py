"""
ssh_chat_server.py  —  本番対応 WebSocket チャットサーバー

環境変数:
  ADMIN_IP    : 管理者として認識するクライアントIPアドレス (必須)
  PORT        : リッスンポート (デフォルト: 10000)
  LOG_FILE    : ログファイルパス (デフォルト: /data/messages.txt)
  LOG_LEVEL   : ログレベル (デフォルト: INFO)
"""

import asyncio
import logging
import os
import signal
import datetime
from typing import Optional

from websockets.server import serve
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

# ------------------------------------------------------------------ #
#  設定 / ロギング
# ------------------------------------------------------------------ #

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("chat_server")

# 管理者IPは環境変数から取得（未設定時は起動を拒否）
ADMIN_IP: str = os.environ.get("ADMIN_IP", "")
if not ADMIN_IP:
    logger.error("環境変数 ADMIN_IP が設定されていません。サーバーを起動できません。")
    raise SystemExit(1)

PORT: int = int(os.environ.get("PORT", "10000"))
LOG_FILE: str = os.environ.get("LOG_FILE", "/data/messages.txt")

# ログファイルの親ディレクトリが存在しない場合は作成
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# ファイルI/Oの競合を防ぐグローバルロック
_file_lock = asyncio.Lock()


# ------------------------------------------------------------------ #
#  ヘルパー関数
# ------------------------------------------------------------------ #

def _read_log_lines() -> list[str]:
    """ログファイルの全行をリストで返す（ファイルなし・空の場合は空リスト）。"""
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return f.readlines()


async def get_next_msg_id() -> int:
    """ログから現在の最大IDを読み取り、次のIDをスレッドセーフに返す。"""
    async with _file_lock:
        max_id = 0
        for line in _read_log_lines():
            if line.startswith("[ID: #"):
                try:
                    max_id = max(max_id, int(line.split("[ID: #")[1].split("]")[0]))
                except (IndexError, ValueError):
                    pass
        return max_id + 1


async def append_log(line: str) -> None:
    """ログファイルに1行追記する（ロック保護）。"""
    async with _file_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def username_exists(username: str) -> bool:
    """指定ユーザー名がログに1件以上存在するか確認する。"""
    for line in _read_log_lines():
        if f"[USER: {username}]" in line:
            return True
    return False


def get_messages_by_user(username: str) -> list[str]:
    """指定ユーザーのメッセージ行をリストで返す。"""
    return [
        line.rstrip()
        for line in _read_log_lines()
        if f"[USER: {username}]" in line
    ]


def get_all_usernames() -> list[str]:
    """ログに登場する全ユーザー名を重複なしで返す（出現順）。"""
    seen: list[str] = []
    for line in _read_log_lines():
        if "[USER: " in line:
            try:
                name = line.split("[USER: ")[1].split("]")[0]
                if name not in seen:
                    seen.append(name)
            except IndexError:
                pass
    return seen


def resolve_ip(websocket) -> str:
    """WebSocketオブジェクトからクライアントIPを安全に取得する。"""
    try:
        forwarded = websocket.request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    except AttributeError:
        pass
    addr = websocket.remote_address
    if isinstance(addr, tuple):
        return addr[0]
    return str(addr) if addr else "Unknown"


def check_username_reply(username: str) -> Optional[str]:
    """指定ユーザー名への管理者返信ファイルが存在すれば内容を返す。"""
    reply_file = _reply_path(username)
    if os.path.exists(reply_file):
        with open(reply_file, "r", encoding="utf-8") as f:
            return f.read()
    return None


def save_username_reply(username: str, reply_text: str) -> None:
    """管理者の返信をユーザー名ベースのファイルに保存する。"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(_reply_path(username), "w", encoding="utf-8") as f:
        f.write(f"[{now}] 応答: {reply_text}\n")


def _reply_path(username: str) -> str:
    """返信ファイルのパスを返す（ログと同じディレクトリ）。"""
    safe = username.replace(" ", "_").replace("/", "_").replace("..", "")
    base = os.path.dirname(LOG_FILE)
    return os.path.join(base, f"reply_user_{safe}.txt")


# ------------------------------------------------------------------ #
#  管理者セッション
# ------------------------------------------------------------------ #

async def run_admin_session(websocket, ip_address: str) -> None:
    """管理者向けのインタラクティブセッション。"""
    await websocket.send(
        "Connecting to secure-message-service... Done.\n"
        f"Verified Administrator IP: {ip_address}\n"
        "Authentication successful. Switching to administrative mode...\n"
        "============================================================\n"
        " 管理者コントロールパネル - ユーザー名・メッセージログ一覧\n"
        "============================================================\n"
    )

    usernames = get_all_usernames()
    if not usernames:
        await websocket.send("INFO: 新着メッセージ、または未処理のキューはありません。\n")
        await websocket.send("Connection closed by remote host.\n")
        return

    await websocket.send(f"登録ユーザー数: {len(usernames)} 名\n\n")
    for uname in usernames:
        msgs = get_messages_by_user(uname)
        await websocket.send(
            f"------------------------------------------------------------\n"
            f" ユーザー: {uname}  (メッセージ数: {len(msgs)})\n"
            f"------------------------------------------------------------\n"
        )
        for m in msgs:
            await websocket.send(f"  {m}\r\n")
        await websocket.send("\n")

    # 複数ユーザーへの返信ループ
    while True:
        await websocket.send(
            "[返信] 返信したいユーザー名を入力 / 終了するには 'exit' と入力:\n> "
        )
        target = (await websocket.recv()).strip()

        if target.lower() == "exit":
            await websocket.send("セッションを終了します。Goodbye.\n")
            break

        if not target:
            await websocket.send("エラー: ユーザー名を入力してください。\n")
            continue

        if target not in usernames:
            await websocket.send(
                f"エラー: '{target}' はログに存在しません。\n"
                f"既存ユーザー: {', '.join(usernames)}\n"
            )
            continue

        msgs = get_messages_by_user(target)
        await websocket.send(f"\n[{target}] の送信履歴 ({len(msgs)}件):\n")
        for m in msgs:
            await websocket.send(f"  {m}\r\n")

        await websocket.send(f"\n{target} への返信内容を入力してください:\n> ")
        reply_text = (await websocket.recv()).strip()

        if not reply_text:
            await websocket.send("エラー: 返信内容が空です。スキップします。\n")
            continue

        save_username_reply(target, reply_text)
        logger.info("Admin replied to user '%s'", target)
        await websocket.send(f"\n完了。'{target}' への返信データを保存しました。\n")


# ------------------------------------------------------------------ #
#  ゲストセッション
# ------------------------------------------------------------------ #

async def run_guest_session(websocket, ip_address: str) -> None:
    """ゲスト向けのインタラクティブセッション。"""
    await websocket.send(
        "Connecting to secure-message-service... Done.\n"
        "Initializing repository setup... OK.\n"
        "------------------------------------------------------------\n"
        " サービス名: 匿名メッセージ共有サブシステム (v2.0.0-release)\n"
        f" 検出されたあなたのIP: {ip_address}\n"
        "------------------------------------------------------------\n"
        " ユーザー名を入力してください (1〜20文字):\n> "
    )

    username = ""
    for _ in range(3):
        raw = (await websocket.recv()).strip()
        if not raw:
            await websocket.send("エラー: ユーザー名を入力してください。もう一度:\n> ")
            continue
        if len(raw) > 20:
            await websocket.send("エラー: ユーザー名は20文字以内にしてください。もう一度:\n> ")
            continue
        username = raw
        break

    if not username:
        await websocket.send("\nエラー: ユーザー名の設定に失敗しました。接続を終了します。\n")
        return

    is_returning = username_exists(username)
    if is_returning:
        past_count = len(get_messages_by_user(username))
        await websocket.send(
            f"\nおかえりなさい、{username} さん! (過去のメッセージ数: {past_count}件)\n"
        )
    else:
        await websocket.send(f"\nようこそ、{username} さん! (初回登録)\n")

    logger.info("Guest '%s' connected (ip=%s, returning=%s)", username, ip_address, is_returning)

    await websocket.send(
        "------------------------------------------------------------\n"
        " メニューを選択してください:\n"
        "   1) メッセージを送信する (Send message)\n"
        "   2) 自分への返信を確認する (Check reply)\n\n"
        "選択してください (1-2) > "
    )

    choice = (await websocket.recv()).strip()

    if choice == "1":
        await websocket.send("\nメッセージ本文を入力し、Enterキーを押してください:\n> ")
        body = (await websocket.recv()).strip()

        if not body:
            await websocket.send("\nエラー: メッセージが空です。接続を終了します。\n")
            return

        msg_id = await get_next_msg_id()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await append_log(
            f"[ID: #{msg_id}] [DATE: {now}] [USER: {username}] [IP: {ip_address}] DATA: {body}"
        )

        total = len(get_messages_by_user(username))
        logger.info("Message #%d from '%s' (ip=%s, total_by_user=%d)", msg_id, username, ip_address, total)
        await websocket.send(
            f"\n処理が正常に完了しました (HTTP 201 Created).\n"
            f"受付番号 【 #{msg_id} 】  {username} さんの通算 {total} 件目のメッセージです。\n"
            f"Session terminated. Closing connection...\n"
        )

    elif choice == "2":
        reply = check_username_reply(username)
        if reply:
            await websocket.send(
                "\n============================================================\n"
                f" [NOTICE] {username} さんへの管理者からの応答データ\n"
                "============================================================\n"
                f"{reply}\n"
                "============================================================\n"
            )
        else:
            await websocket.send(
                f"\nステータス: 処理待ち ({username} さんへの返信はまだ登録されていません)。\n"
            )
        await websocket.send("Session terminated. Closing connection...\n")

    else:
        await websocket.send("\nエラー: 1 または 2 を指定してください。接続を終了します。\n")


# ------------------------------------------------------------------ #
#  メインハンドラ
# ------------------------------------------------------------------ #

async def handle_ws(websocket) -> None:
    """接続ごとにセッションを振り分けるメインハンドラ。"""
    ip_address = resolve_ip(websocket)
    is_admin = (ip_address == ADMIN_IP)
    logger.info("New connection from %s (admin=%s)", ip_address, is_admin)

    try:
        if is_admin:
            await run_admin_session(websocket, ip_address)
        else:
            await run_guest_session(websocket, ip_address)
    except (ConnectionClosedOK, ConnectionClosedError):
        pass  # 正常切断・通信断はWARNINGにしない
    except Exception as exc:
        logger.warning("Session error for %s: %s", ip_address, exc, exc_info=True)
    finally:
        logger.info("Connection closed: %s", ip_address)


# ------------------------------------------------------------------ #
#  HTTPリクエスト処理 (WebSocket以外)
# ------------------------------------------------------------------ #

async def http_handler(connection, request):
    """/health はヘルスチェック用、それ以外のHTTPは空ページを返す。"""
    from websockets.http11 import Response

    if request.path == "/health":
        return Response(
            status_code=200,
            headers=[("Content-Type", "text/plain; charset=utf-8")],
            body=b"OK",
        )

    if request.path != "/ws":
        return Response(
            status_code=200,
            headers=[("Content-Type", "text/html; charset=utf-8")],
            body=b"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body></body></html>",
        )

    return None


# ------------------------------------------------------------------ #
#  グレースフルシャットダウン
# ------------------------------------------------------------------ #

async def main() -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal():
        logger.info("シャットダウンシグナルを受信しました。接続を安全に終了します...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    logger.info("WebSocket サーバー起動 — ポート %d  |  ADMIN_IP=%s", PORT, ADMIN_IP)
    async with serve(
        handle_ws,
        "0.0.0.0",
        PORT,
        process_request=http_handler,
        ping_interval=30,     # 30秒ごとにpingを送り切断を検出
        ping_timeout=10,      # 10秒以内にpongがなければ切断
        close_timeout=5,      # シャットダウン時のclose待機上限
    ):
        await stop_event.wait()

    logger.info("サーバーを正常に停止しました。")


if __name__ == "__main__":
    asyncio.run(main())
