#!/usr/bin/env python3
from pathlib import Path
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'frontend' / 'src' / 'App.jsx'
SERVER = ROOT / 'server_scheduler.mjs'
START = 'const uid ='
END_FN = 'function emptyStats()'

def core(text: str) -> str:
    try:
        start = text.index(START)
        fn = text.index(END_FN, start)
        end = text.index('\n}', fn) + 2
        return text[start:end]
    except ValueError as exc:
        raise RuntimeError('Не удалось найти границы общего ядра планировщика') from exc

a = core(APP.read_text(encoding='utf-8'))
s = core(SERVER.read_text(encoding='utf-8'))
ha = hashlib.sha256(a.encode()).hexdigest()
hs = hashlib.sha256(s.encode()).hexdigest()
if a != s:
    print('ERROR: frontend scheduler and server scheduler differ', file=sys.stderr)
    print(f'frontend sha256: {ha}', file=sys.stderr)
    print(f'server   sha256: {hs}', file=sys.stderr)
    sys.exit(1)
print(f'Scheduler parity OK: {ha}')
