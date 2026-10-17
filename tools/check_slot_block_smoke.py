import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
payload = json.loads((root / "tools" / "scheduler_smoke_payload.json").read_text())
payload.setdefault("data", {})["groupSlotBlocks"] = [
    {"id": "sb0", "groupId": "g", "date": "2026-08-31", "period": 0},
    {"id": "sb1", "groupId": "g", "date": "2026-09-01", "period": 0},
    {"id": "sb2", "groupId": "g", "date": "2026-09-02", "period": 0},
    {"id": "sb3", "groupId": "g", "date": "2026-09-03", "period": 0},
    {"id": "sb4", "groupId": "g", "date": "2026-09-04", "period": 0},
]
proc = subprocess.run(
    ["node", str(root / "server_scheduler.mjs")],
    input=json.dumps(payload), text=True, capture_output=True, timeout=60,
)
if proc.returncode != 0:
    raise SystemExit(proc.stderr or f"scheduler exited {proc.returncode}")
obj = json.loads(proc.stdout)
if not obj.get("ok"):
    raise SystemExit("scheduler returned ok=false")
assignment = obj.get("result", {}).get("assignment", {})
if not assignment:
    raise SystemExit("slot-block smoke: lesson was not placed")
if any(int(a.get("period", -1)) == 0 for a in assignment.values()):
    raise SystemExit(f"slot-block smoke: auto used blocked period: {assignment}")
print("Group time-slot block smoke OK")
