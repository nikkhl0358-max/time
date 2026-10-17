import json, subprocess, pathlib
root = pathlib.Path(__file__).resolve().parents[1]
payload = json.loads((root / 'tools' / 'scheduler_smoke_payload.json').read_text())
def run(obj):
    proc = subprocess.run(['node', str(root / 'server_scheduler.mjs')], input=json.dumps(obj), text=True, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)
base = run(payload)['result']
loads = payload.get('data', {}).get('loads', [])
gid = next((str(x.get('groupId') or '') for x in loads if x.get('groupId')), '')
if gid:
    out = run({'data': payload.get('data', {}), 'prior': base, 'groupId': gid})
    result = out.get('result') or {}
    meta = result.get('generationMeta') or {}
    if meta.get('strategy') != 'group-batches':
        raise RuntimeError(f'expected group-batches meta, got {meta}')
    if int(meta.get('batchSize') or 0) != 12:
        raise RuntimeError(f'unexpected batch size: {meta}')
    if int(meta.get('batchBudgetMs') or 0) != 6000:
        raise RuntimeError(f'unexpected per-batch budget: {meta}')
    if int(meta.get('batchesTimedOut') or 0) < 0:
        raise RuntimeError(f'invalid timeout count: {meta}')
print('Group batch scheduler smoke OK')
