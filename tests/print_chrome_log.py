import os
import re

log_file = r"C:\Users\ADMIN\AppData\Local\Temp\chrome_test_prof\chrome_debug.log"
with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        if "CONSOLE" in line:
            m = re.search(r'"(.*)"', line)
            if m:
                # Unescape \n
                text = m.group(1).replace("\\n", "\n")
                print(text)
