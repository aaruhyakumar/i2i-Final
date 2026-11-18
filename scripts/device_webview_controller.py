import json
import socket
import base64
import os
import requests

def get_page_ws_url():
    res = requests.get("http://localhost:9222/json")
    for tab in res.json():
        if tab.get("type") == "page" and "devtools/page" in tab.get("webSocketDebuggerUrl", ""):
            return tab["webSocketDebuggerUrl"], tab["id"]
    raise RuntimeError("No devtools page tab found")

class DevToolsClient:
    def __init__(self, ws_url):
        # ws_url is ws://localhost:9222/devtools/page/...
        path = ws_url.replace("ws://localhost:9222", "")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect(("localhost", 9222))
        
        # Handshake
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: localhost:9222\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode('utf-8'))
        resp = self.sock.recv(4096).decode('utf-8')
        if "101 " not in resp:
            raise RuntimeError(f"WebSocket handshake failed:\n{resp}")
        self._msg_id = 0

    def _send_frame(self, text):
        data = text.encode('utf-8')
        length = len(data)
        frame = bytearray([0x81]) # FIN + text opcode
        # Client to server must be masked
        mask = os.urandom(4)
        if length <= 125:
            frame.append(0x80 | length)
        elif length <= 65535:
            frame.append(0x80 | 126)
            frame.extend(length.to_bytes(2, 'big'))
        else:
            frame.append(0x80 | 127)
            frame.extend(length.to_bytes(8, 'big'))
        frame.extend(mask)
        masked_data = bytearray(b ^ mask[i % 4] for i, b in enumerate(data))
        frame.extend(masked_data)
        self.sock.sendall(frame)

    def _recv_frame(self):
        head = self.sock.recv(2)
        if not head:
            return ""
        length = head[1] & 0x7F
        if length == 126:
            length = int.from_bytes(self.sock.recv(2), 'big')
        elif length == 127:
            length = int.from_bytes(self.sock.recv(8), 'big')
        payload = bytearray()
        while len(payload) < length:
            chunk = self.sock.recv(length - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        return payload.decode('utf-8', errors='replace')

    def send_cmd(self, method, params=None):
        self._msg_id += 1
        cmd_id = self._msg_id
        msg = {"id": cmd_id, "method": method, "params": params or {}}
        self._send_frame(json.dumps(msg))
        while True:
            raw = self._recv_frame()
            if not raw:
                return None
            try:
                res = json.loads(raw)
                if res.get("id") == cmd_id:
                    return res
            except Exception:
                pass

    def evaluate(self, expr):
        res = self.send_cmd("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": True
        })
        if res and "result" in res:
            result = res["result"].get("result", {})
            return result.get("value")
        return None

    def navigate_to(self, screen_id):
        self.evaluate(f"window.app.go('{screen_id}')")
        return self.evaluate("window.app.state.currentScreen")

    def get_element_text(self, selector):
        return self.evaluate(f"document.querySelector('{selector}') ? document.querySelector('{selector}').innerText : null")

    def get_input_val(self, selector):
        return self.evaluate(f"document.querySelector('{selector}') ? document.querySelector('{selector}').value : null")

    def set_input_val(self, selector, val):
        escaped_val = val.replace("'", "\\'")
        js = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (!el) return false;
            el.value = '{escaped_val}';
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }})()
        """
        return self.evaluate(js)

    def click_element(self, selector):
        js = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (!el) return false;
            el.click();
            return true;
        }})()
        """
        return self.evaluate(js)

def pull_screenshot(filename):
    import subprocess
    adb = os.path.expandvars(r"$LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe")
    remote_path = f"/sdcard/{os.path.basename(filename)}"
    subprocess.run([adb, "shell", "screencap", "-p", remote_path], check=True)
    subprocess.run([adb, "pull", remote_path, filename], check=True)
    return filename

if __name__ == "__main__":
    ws_url, tab_id = get_page_ws_url()
    print("Connecting to:", ws_url)
    client = DevToolsClient(ws_url)
    title = client.evaluate("document.title")
    curr_screen = client.evaluate("window.app ? window.app.state.currentScreen : 'no app'")
    print("Page Title:", title)
    print("Current Screen in app.state:", curr_screen)

