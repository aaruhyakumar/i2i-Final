import socket
import os
import base64
import json
import urllib.request

def get_page_info():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json/list")
    pages = json.loads(req.read().decode())
    for p in pages:
        if p.get("type") == "page" and "5001" in p.get("url", ""):
            return p
    for p in pages:
        if p.get("type") == "page":
            return p
    return pages[0]

def create_ws_frame(message: str) -> bytes:
    data = message.encode("utf-8")
    length = len(data)
    frame = bytearray()
    frame.append(0x81) # FIN + text opcode
    mask_key = os.urandom(4)
    if length < 126:
        frame.append(0x80 | length)
    elif length < 65536:
        frame.append(0x80 | 126)
        frame.extend(length.to_bytes(2, "big"))
    else:
        frame.append(0x80 | 127)
        frame.extend(length.to_bytes(8, "big"))
    frame.extend(mask_key)
    masked_data = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(data))
    frame.extend(masked_data)
    return bytes(frame)

def recv_ws_frame(sock: socket.socket) -> str:
    header = sock.recv(2)
    if len(header) < 2:
        return ""
    length = header[1] & 0x7F
    if length == 126:
        length = int.from_bytes(sock.recv(2), "big")
    elif length == 127:
        length = int.from_bytes(sock.recv(8), "big")
    payload = bytearray()
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            break
        payload.extend(chunk)
    return payload.decode("utf-8", errors="ignore")

def eval_in_webview(js_code: str) -> dict:
    page = get_page_info()
    path = page["webSocketDebuggerUrl"].split("127.0.0.1:9222")[1]
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", 9222))
    
    # Handshake
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:9222\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(handshake.encode("utf-8"))
    
    # Read HTTP handshake response
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(1024)
        
    req = {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": js_code,
            "awaitPromise": True,
            "returnByValue": True
        }
    }
    sock.sendall(create_ws_frame(json.dumps(req)))
    result = recv_ws_frame(sock)
    sock.close()
    try:
        return json.loads(result)
    except Exception:
        return {"raw": result}

if __name__ == "__main__":
    res = eval_in_webview("document.title + ' | screen: ' + (window.state?.currentScreen || 'none')")
    print("Result:", res)
