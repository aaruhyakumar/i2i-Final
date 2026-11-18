import json
import websocket
import requests

def get_ws_url():
    r = requests.get('http://127.0.0.1:9222/json/list').json()
    for item in r:
        if 'http://127.0.0.1:5001' in item.get('url', ''):
            return item.get('webSocketDebuggerUrl')
    return r[0].get('webSocketDebuggerUrl')

def eval_js(expr):
    ws_url = get_ws_url()
    ws = websocket.create_connection(ws_url)
    req = {
        'id': 1,
        'method': 'Runtime.evaluate',
        'params': {
            'expression': expr,
            'awaitPromise': True,
            'returnByValue': True
        }
    }
    ws.send(json.dumps(req))
    res = json.loads(ws.recv())
    ws.close()
    return res.get('result', {}).get('result', {}).get('value')

if __name__ == '__main__':
    data = eval_js('JSON.stringify({cn: document.getElementById("cn")?.value, ci: document.getElementById("ci")?.value, cd: document.getElementById("cd")?.value, user: window.state?.currentUser?.email})')
    print('WebView Info:', data)
