import json, subprocess, pathlib, sys
root = pathlib.Path(__file__).resolve().parents[1]
payload = json.loads((root / "tools" / "scheduler_smoke_payload.json").read_text())
def run(obj):
    proc = subprocess.run(["node", str(root / "server_scheduler.mjs")], input=json.dumps(obj), text=True, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)
first = run(payload)
if not first.get("ok") or not first.get("result"):
    raise RuntimeError("base scheduler smoke failed")
loads = payload.get("data", {}).get("loads", [])
gid = next((str(x.get("groupId") or "") for x in loads if x.get("groupId")), "")
if gid:
    second = run({"data": payload.get("data", {}), "prior": first["result"], "groupId": gid})
    if not second.get("ok") or not second.get("result"):
        raise RuntimeError("group-only scheduler smoke failed")
print("Group scheduler smoke OK")
