from pathlib import Path
import subprocess, tempfile
root = Path(__file__).resolve().parents[1]
src = (root / 'server_scheduler.mjs').read_text(encoding='utf-8')
marker = '\nimport fs from "node:fs";'
if marker not in src:
    raise SystemExit('server_scheduler main marker not found')
core = src.split(marker, 1)[0]
test = r'''
const data = {
  config: {semesterStart:'2026-08-31', periodTimes:['08:30–09:55','10:20–11:45']},
  groups:[{id:'g1',dayPeriodTimes:{'0':['08:00–09:25','09:35–11:00']},dayPeriodTimesByParity:{odd:{'0':['08:30–09:55','10:20–11:45']},even:{'0':['08:10–09:35','09:45–11:10']}}}]
};
if (periodTimeForGroupDay(data,'g1',0,0,'2026-08-31') !== '08:30–09:55') throw new Error('odd override failed');
if (periodTimeForGroupDay(data,'g1',0,0,'2026-09-07') !== '08:10–09:35') throw new Error('even override failed');
if (periodTimeForGroupDay(data,'g1',0,0,'') !== '08:00–09:25') throw new Error('base fallback failed');
if (groupHasCustomBellDay(data,'g1',0,'2026-08-31') !== false) throw new Error('explicit standard parity should be standard');
if (groupHasCustomBellDay(data,'g1',0,'2026-09-07') !== true) throw new Error('even custom parity should be custom');
console.log('Bell parity smoke OK');
'''
with tempfile.NamedTemporaryFile('w', suffix='.mjs', delete=False, encoding='utf-8') as fh:
    fh.write(core + '\n' + test)
    tmp = fh.name
try:
    subprocess.run(['node', tmp], check=True)
finally:
    Path(tmp).unlink(missing_ok=True)
