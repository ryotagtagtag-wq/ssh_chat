import asyncio
import logging
import os
import datetime
from websockets.server import serve

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

LOG_FILE = "messages.txt"
MY_GLOBAL_IP = "153.191.11.135"


# ------------------------------------------------------------------ #
#  ヘルパー関数
# ------------------------------------------------------------------ #

def get_next_msg_id() -> int:
    """ログファイルから現在の最大IDを読み取り、次のIDを返す。"""
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        return 1
    max_id = 0
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("[ID: #"):
                try:
                    # "[ID: #3]" → "3"
                    id_part = line.split("[ID: #")[1].split("]")[0]
                    max_id = max(max_id, int(id_part))
                except (IndexError, ValueError):
                    pass
    return max_id + 1


def check_ip_reply(client_ip: str) -> str | None:
    """指定IPへの管理者返信ファイルが存在すれば内容を返す。"""
    safe_ip = client_ip.replace(".", "_").replace(":", "_")
    reply_file = f"reply_ip_{safe_ip}.txt"
    if os.path.exists(reply_file):
        with open(reply_file, "r", encoding="utf-8") as f:
            return f.read()
    return None


def username_taken(username: str) -> bool:
    """同名ユーザーが既にログに存在するか確認する。"""
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        return False
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if f"[USER: {username}]" in line:
                return True
    return False


def resolve_ip(websocket) -> str:
    """WebSocketオブジェクトからクライアントIPを安全に取得する。"""
    # プロキシ経由の場合は X-Forwarded-For を優先
    try:
        headers = websocket.request.headers
        forwarded = headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    except AttributeError:
        pass

    # 直接接続の場合: remote_address は (host, port) タプル
    addr = websocket.remote_address
    if isinstance(addr, tuple):
        return addr[0]
    return str(addr) if addr else "Unknown"


def load_log_text() -> str:
    """ログファイルの全内容を文字列で返す（なければ空文字）。"""
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        return ""
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------ #
#  管理者セッション
# ------------------------------------------------------------------ #

async def run_admin_session(websocket, ip_address: str) -> None:
    """管理者向けのインタラクティブセッション（複数IP返信対応）。"""
    banner = (
        "Connecting to secure-message-service... Done.\n"
        f"Verified Administrator IP: {ip_address}\n"
        "Authentication successful. Switching to administrative mode...\n"
        "============================================================\n"
        " 管理者コントロールパネル - ゲストメッセージ・ユーザー名・IPログ一覧\n"
        "============================================================\n"
    )
    await websocket.send(banner)

    log_text = load_log_text()
    if not log_text:
        await websocket.send("INFO: 新着メッセージ、または未処理のキューはありません。\n")
        await websocket.send("Connection closed by remote host.\n")
        return

    await websocket.send("現在サーバー内に格納されているログ（IPアドレス付き）:\n")
    await websocket.send(log_text.replace("\n", "\r\n") + "\n")

    # 複数IPへの返信ループ
    while True:
        await websocket.send(
            "[必須] 返信したい相手の『IPアドレス』を入力 (ログの [IP: ...] を参照) / 終了するには 'exit' と入力:\n> "
        )
        target_ip = (await websocket.recv()).strip()

        if target_ip.lower() == "exit":
            await websocket.send("セッションを終了します。Goodbye.\n")
            break

        if not target_ip:
            await websocket.send("エラー: IPアドレスを入力してください。\n")
            continue

        await websocket.send(f"\nIP: {target_ip} への応答テキストを入力してください:\n> ")
        reply_text = (await websocket.recv()).strip()

        if not reply_text:
            await websocket.send("エラー: 返信内容が空です。スキップします。\n")
            continue

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_target_ip = target_ip.replace(".", "_").replace(":", "_")
        with open(f"reply_ip_{safe_target_ip}.txt", "w", encoding="utf-8") as f:
            f.write(f"[{now}] 応答: {reply_text}\n")

        await websocket.send(
            f"\nデータ更新完了。IP: {target_ip} に応答データをバインドしました。\n"
        )


# ------------------------------------------------------------------ #
#  ゲストセッション
# ------------------------------------------------------------------ #

async def run_guest_session(websocket, ip_address: str) -> None:
    """ゲスト向けのインタラクティブセッション。"""
    # --- ユーザー名入力フェーズ ---
    await websocket.send(
        "Connecting to secure-message-service... Done.\n"
        "Initializing repository setup... OK.\n"
        "------------------------------------------------------------\n"
        " サービス名: 匿名メッセージ共有サブシステム (v2.0.0-release)\n"
        f" 検出されたあなたのIP: {ip_address}\n"
        "------------------------------------------------------------\n"
        " ユーザー名を入力してください (1〜20文字, 英数字・記号可):\n> "
    )

    username = ""
    for _ in range(3):  # 最大3回試行
        raw = (await websocket.recv()).strip()
        if not raw:
            await websocket.send("エラー: ユーザー名を入力してください。もう一度:\n> ")
            continue
        if len(raw) > 20:
            await websocket.send("エラー: ユーザー名は20文字以内にしてください。もう一度:\n> ")
            continue
        if username_taken(raw):
            await websocket.send(f"エラー: '{raw}' はすでに使用されています。別の名前を入力してください:\n> ")
            continue
        username = raw
        break

    if not username:
        await websocket.send("\nエラー: ユーザー名の設定に失敗しました。接続を終了します。\n")
        return

    logger.info("Guest identified as '%s' (%s)", username, ip_address)

    # --- メインメニュー ---
    await websocket.send(
        f"\nようこそ、{username} さん!\n"
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

        msg_id = get_next_msg_id()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[ID: #{msg_id}] [DATE: {now}] [USER: {username}] [IP: {ip_address}] DATA: {body}\n")

        logger.info("New message #%d from '%s' (%s)", msg_id, username, ip_address)
        await websocket.send(
            f"\n処理が正常に完了しました (HTTP 201 Created).\n"
            f"あなたのメッセージは受付番号 【 #{msg_id} 】 として {username} に紐付けられました。\n"
            f"Session terminated. Closing connection...\n"
        )

    elif choice == "2":
        reply = check_ip_reply(ip_address)
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
    is_admin = (ip_address == MY_GLOBAL_IP)
    logger.info("New connection from %s (admin=%s)", ip_address, is_admin)

    try:
        if is_admin:
            await run_admin_session(websocket, ip_address)
        else:
            await run_guest_session(websocket, ip_address)
    except Exception as exc:
        logger.warning("Session error for %s: %s", ip_address, exc)
    finally:
        logger.info("Connection closed: %s", ip_address)


# ------------------------------------------------------------------ #
#  HTTPリクエスト処理 (WebSocket以外)
# ------------------------------------------------------------------ #

async def http_handler(connection, request):
    """WebSocket以外のHTTPリクエストに対して空のHTMLを返す。"""
    if request.path != "/ws":
        blank_html = (
            b"<!DOCTYPE html><html>"
            b"<head><meta charset='utf-8'></head>"
            b"<body></body></html>"
        )
        from websockets.http11 import Response
        return Response(
            status_code=200,
            headers=[("Content-Type", "text/html; charset=utf-8")],
            body=blank_html,
        )
    return None


# ------------------------------------------------------------------ #
#  エントリポイント
# ------------------------------------------------------------------ #

async def main() -> None:
    port = int(os.environ.get("PORT", 10000))
    logger.info("Starting WebSocket server on port %d", port)
    async with serve(handle_ws, "0.0.0.0", port, process_request=http_handler):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
