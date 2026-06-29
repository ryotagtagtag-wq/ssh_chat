import asyncio
import os
import datetime
from websockets.server import serve

LOG_FILE = "messages.txt"
MY_GLOBAL_IP = "153.191.11.135"

def get_next_msg_id():
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0: return 1
    max_id = 0
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("[ID:"):
                try:
                    parts = line.split("]").replace("[ID: #", "")
                    max_id = max(max_id, int(parts))
                except: pass
    return max_id + 1

def check_ip_reply(client_ip):
    safe_ip = client_ip.replace(".", "_").replace(":", "_")
    reply_file = f"reply_ip_{safe_ip}.txt"
    if os.path.exists(reply_file):
        with open(reply_file, "r", encoding="utf-8") as f:
            return f.read()
    return None

client_states = {}

async def handle_ws(websocket):
    client_id = id(websocket)
    client_states[client_id] = {"menu": "main", "target_ip": None}
    
    ip_address = "Unknown"
    if hasattr(websocket, 'request_headers'):
        forwarded = websocket.request_headers.get("X-Forwarded-For", "")
        if forwarded:
            ip_address = forwarded.split(",")[0].strip()
            
    if not ip_address or ip_address == "Unknown":
        ip_address = websocket.remote_address

    is_admin = (ip_address == MY_GLOBAL_IP)

    if is_admin:
        banner = (
            "Connecting to secure-message-service... Done.\n"
            f"Verified Administrator IP: {ip_address}\n"
            "Authentication successful. Switching to administrative mode...\n"
            "============================================================\n"
            " 管理者コントロールパネル - ゲストメッセージ・IPログ一覧\n"
            "============================================================\n"
        )
        await websocket.send(banner)
        
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 0:
            await websocket.send("現在サーバー内に格納されているログ（IPアドレス付き）:\n")
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                await websocket.send(f.read().replace("\n", "\r\n"))
            await websocket.send("\n[必須] 返信したい相手の『IPアドレス』をそのまま入力してください:\n> ")
            client_states[client_id]["menu"] = "admin_select"
        else:
            await websocket.send("INFO: 新着メッセージ、または未処理のキューはありません。\n")
            await websocket.send("Connection closed by remote host.\n")
            await websocket.close()
            return
    else:
        banner = (
            "Connecting to secure-message-service... Done.\n"
            "Initializing repository setup... OK.\n"
            "------------------------------------------------------------\n"
            " サービス名: 匿名メッセージ共有サブシステム (v1.0.4-release)\n"
            f" 検出されたあなたのIP: {ip_address}\n"
            "------------------------------------------------------------\n"
            " メニューを選択してください:\n"
            "   1) メッセージを送信する (Send message)\n"
            "   2) 自分への返信を確認する (Check reply)\n\n"
            "選択してください (1-2) > "
        )
        await websocket.send(banner)

    try:
        async for message in websocket:
            input_str = message.strip()
            state = client_states[client_id]
            
            if not is_admin:
                if state["menu"] == "main":
                    if input_str == "1":
                        await websocket.send("\nメッセージ本文を入力し、Enterキーを押してください:\n> ")
                        state["menu"] = "visitor_write"
                    elif input_str == "2":
                        has_reply = check_ip_reply(ip_address)
                        if has_reply:
                            await websocket.send(
                                f"\n============================================================\n"
                                f" 🔔 あなたのIPに対する管理者からの応答データ\n"
                                f"============================================================\n"
                                f"{has_reply}\n"
                                f"============================================================\n"
                            )
                        else:
                            await websocket.send("\nステータス: 処理待ち (あなたへの返信はまだ登録されていません)。\n")
                        await websocket.send("Session terminated. Closing connection...\n")
                        await websocket.close()
                    else:
                        await websocket.send("\nエラー: 1 または 2 を指定してください。\n> ")
                        
                elif state["menu"] == "visitor_write":
                    if input_str:
                        msg_id = get_next_msg_id()
                        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        with open(LOG_FILE, "a", encoding="utf-8") as f:
                            f.write(f"[ID: #{msg_id}] [DATE: {now}] [IP: {ip_address}] DATA: {input_str}\n")
                        
                        await websocket.send(
                            f"\n処理が正常に完了しました (HTTP 201 Created).\n"
                            f"あなたのメッセージは受付番号 【 #{msg_id} 】 としてIPに紐付けられました。\n"
                            f"Session terminated. Closing connection...\n"
                        )
                        await websocket.close()

            elif is_admin:
                if state["menu"] == "admin_select":
                    if input_str:
                        state["target_ip"] = input_str
                        await websocket.send(f"\nIP: {input_str} への応答テキストを入力してください:\n> ")
                        state["menu"] = "admin_write"
                
                elif state["menu"] == "admin_write":
                    if input_str and state["target_ip"]:
                        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        safe_target_ip = state["target_ip"].replace(".", "_").replace(":", "_")
                        with open(f"reply_ip_{safe_target_ip}.txt", "w", encoding="utf-8") as f:
                            f.write(f"[{now}] 応答: {input_str}\n")
                        await websocket.send(f"\nデータ更新完了。IP: {state['target_ip']} に応答データをバインドしました。\n")
                        await websocket.close()
                        
    except Exception:
        pass
    finally:
        if client_id in client_states:
            del client_states[client_id]

async def http_and_ws_handler(path, request_headers):
    if path != "/ws":
        blank_html = "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body></body></html>"
        return (
            200,
            [("Content-Type", "text/html; charset=utf-8")],
            blank_html.encode("utf-8"),
        )
    return None

async def main():
    port = int(os.environ.get("PORT", 10000))
    async with serve(handle_ws, "0.0.0.0", port, process_request=http_and_ws_handler):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
