import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8')

cmd = [
    "ssh", "-i", r"C:\Users\sevam\.ssh\antigravity_key", 
    "-o", "StrictHostKeyChecking=no", 
    "root@185.5.75.211", 
    "python3 -c 'import json; db=json.load(open(\"/opt/calamo/backend/output/orders_db.json\")); [print(oid, \"->\", order.get(\"data\", {}).get(\"topic\"), \"\\n\", json.dumps(order.get(\"draft_outline\"), indent=2, ensure_ascii=False)) for oid, order in list(db.items())[-2:]]'"
]

res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
print(res.stdout)
print(res.stderr, file=sys.stderr)
