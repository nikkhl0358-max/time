"""v1876 publication-index equivalence and a repeatable optional microbenchmark.
No Flask/server/data migration is required; execute only the pure AST helpers.
"""
from pathlib import Path
import ast
import hashlib
import json
import sys
import time
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[1]
NAMES = {
    '_instance_group_ids', '_total_semester_weeks_py', '_instance_weeks_py',
    '_build_public_visibility_context', '_build_public_shard_indexes',
    '_build_public_patch_index', '_instance_visible_weeks_for_assignment_py',
    '_trim_instance_to_public_weeks', '_build_public_schedule_shard',
    '_public_entity_data_patch', '_public_schedule_shard_for_week',
}
code = ROOT.joinpath('app.py').read_text()
module = ast.Module(body=[n for n in ast.parse(code).body if isinstance(n, ast.FunctionDef) and n.name in NAMES], type_ignores=[])
ns = {'date': date, 'timedelta': timedelta}
exec(compile(module, 'app-public-selected', 'exec'), ns)
ns['_schedule_instance_group_ids'] = lambda inst: set(ns['_instance_group_ids'](inst))

def fixture(n=320, groups=17, teachers=21):
    instances, assignment, loads, subs, practices = [], {}, [], [], []
    for i in range(n):
        iid = f'I{i}'
        gid = f'G{i % groups}'
        tid = f'T{i % teachers}'
        inst = {
            'instId': iid, 'groupId': gid, 'teacherId': tid, 'loadId': f'L{i}',
            'subjectId': f'S{i % 9}', 'typeId': 'LEK' if i % 3 == 0 else 'PR',
            'weekPattern': 'custom', 'customWeeks': [1, 2, 3, 4, 5, 6, 7, 8],
            'format': 'remote' if i % 4 == 0 else 'inperson',
        }
        if i % 5 == 0:
            inst['streamParticipants'] = [
                {'groupId': gid, 'subgroup': 0, 'loadId': f'L{i}'},
                {'groupId': f'G{(i + 1) % groups}', 'subgroup': 0, 'loadId': f'L{i + 1}'},
            ]
        if i % 11 == 0:
            inst['groupIds'] = [f'G{(i + 2) % groups}']
        if i % 13 == 0:
            inst['teacherIds'] = [f'T{(i + 3) % teachers}']
        if i % 23 == 0:
            inst['_publishedSourceInstId'] = f'ORIG{i}'
        if i % 31 == 0:
            inst['isScheduleEvent'] = True
        instances.append(inst)
        assignment[iid] = {'day': i % 6, 'period': (i // 6) % 5, 'roomId': f'R{i % 15}'}
        loads.append({'id': f'L{i}', 'groupId': gid, 'teacherId': tid, 'subjectId': inst['subjectId']})
        if i % 9 == 0:
            subs.append({'instId': iid, 'date': '2026-09-21', 'type': 'moved',
                         'teacherId': f'T{(i + 1) % teachers}', 'targetDate': '2026-09-23'})
        if i % 17 == 0:
            subs.append({'instId': f'ORIG{i}', 'date': '2026-09-28', 'type': 'moved',
                         'newTeacherId': f'T{(i + 4) % teachers}', 'targetDate': '2026-09-30'})
        if i % 7 == 0:
            subs.append({'instId': iid, 'date': '2026-09-16', 'type': 'cancelled',
                         'groupId': gid, 'newRoomId': f'R{(i + 1) % 15}'})
    for j in range(groups):
        practices.append({'groupId': f'G{j}', 'dateStart': '2026-09-21',
                          'dateEnd': '2026-09-25' if j % 3 == 0 else '2026-09-22'})
    return {
        'config': {'semesterStart': '2026-09-01', 'semesterEnd': '2026-12-31',
                   'holidays': ['2026-09-10'],
                   'calendarExceptions': [{'date': '2026-09-18', 'noClasses': True}]},
        'calendarExceptions': [{'date': '2026-09-15', 'noClasses': True}],
        'publicWeekSelectionEnabled': True, 'publishedWeeks': [1, 2, 3, 4, 5, 6, 7, 8],
        'schedule': {'instances': instances, 'assignment': assignment,
                     'locked': [f'I{i}' for i in range(0, n, 12)], 'unplaced': []},
        'loads': loads, 'substitutions': subs, 'practices': practices,
    }

def compare(p, g, t):
    indexed = ns['_build_public_shard_indexes'](p)
    vis = ns['_build_public_visibility_context'](p)
    patch_index = ns['_build_public_patch_index'](p)
    for kind, ids in (('group', [f'G{i}' for i in range(g)]), ('teacher', [f'T{i}' for i in range(t)]), ('room', ['R0', 'R1'])):
        for entity in ids:
            original = ns['_build_public_schedule_shard'](p, kind, entity)
            fast = ns['_build_public_schedule_shard'](p, kind, entity, indexed, vis)
            assert original == fast, (kind, entity, 'indexed shard differs from direct shard')
            for week in (1, 3, 5):
                week_original = ns['_public_schedule_shard_for_week'](p, original, week)
                week_fast = ns['_public_schedule_shard_for_week'](p, fast, week, [inst for inst in fast['instances'] if week in ns['_instance_weeks_py'](p['config'], inst)])
                assert week_original == week_fast, (kind, entity, week)
            for instances in (original['instances'], ns['_public_schedule_shard_for_week'](p, original, 3)['instances']):
                patch_original = ns['_public_entity_data_patch'](p, instances)
                patch_fast = ns['_public_entity_data_patch'](p, instances, patch_index)
                assert patch_original == patch_fast, (kind, entity, 'indexed patch differs')
    return len(p['schedule']['instances']), g + t + 2

p = fixture()
assert compare(p, 17, 21) == (320, 40)
# Practice-hidden stream, moved source, no-class dates and substitution-only
# teacher membership are all represented in this synthetic fixture.
print('v1876: 40 public entity shards, 120 week shards and 80 data patches are identical (direct vs indexed)')
if '--benchmark' in sys.argv:
    p = fixture(n=2500, groups=55, teachers=65)
    ids = [('group', f'G{i}') for i in range(55)] + [('teacher', f'T{i}') for i in range(65)]
    t0 = time.perf_counter()
    direct = [(ns['_build_public_schedule_shard'](p, k, i)) for k, i in ids]
    direct_time = time.perf_counter() - t0
    t0 = time.perf_counter()
    idx = ns['_build_public_shard_indexes'](p)
    vis = ns['_build_public_visibility_context'](p)
    fast = [(ns['_build_public_schedule_shard'](p, k, i, idx, vis)) for k, i in ids]
    fast_time = time.perf_counter() - t0
    assert direct == fast
    print(f'v1876 public shard benchmark (2500 instances, 120 entities): direct {direct_time:.3f}s, indexed {fast_time:.3f}s, speedup {direct_time / fast_time:.1f}x')
