import subprocess
import time
import json
import urllib.request
import sys

chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
url = "http://localhost:5001/test_offline_queue.html"

# Check if websockets is installed or if we can use simple CDP or invoke the browser subagent
cmd = [
    chrome_path,
    "--headless=new",
    "--remote-debugging-port=9222",
    "--disable-gpu",
    "--no-sandbox",
    "--user-data-dir=" + r"C:\Users\ADMIN\AppData\Local\Temp\chrome_test_profile",
    url
]

proc = subprocess.Popen(cmd)
try:
    for _ in range(10):
        try:
            tabs = json.loads(urllib.request.urlopen("http://localhost:9222/json").read())
            if tabs:
                break
        except Exception:
            time.sleep(0.5)
    print("Found tabs:", len(tabs))
    print("Target:", tabs[0])
finally:
    proc.terminate()
