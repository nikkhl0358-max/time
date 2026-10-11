from pathlib import Path
import ast

src = Path(__file__).resolve().parents[1].joinpath('app.py').read_text()
tree = ast.parse(src)
wanted = {
    '_instance_group_ids', '_trim_instance_to_public_weeks', '_public_instance_teacher_ids',
    '_canonical_public_inst_id', '_public_instance_identity_ids', '_public_substitution_source_ids',
    '_substitution_targets_teacher', '_build_public_schedule_shard', '_public_entity_data_patch',
    '_publication_teacher_ids_for_groups'
}
module = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted], type_ignores=[])
ns = {}
exec(compile(module, 'app_subset_v1890.py', 'exec'), ns)

assert ns['_canonical_public_inst_id']('I1__PUBW6') == 'I1'
assert ns['_canonical_public_inst_id']('I1__PUBW6__PUBW6') == 'I1'
sub = {'instId':'I1', 'teacherId':'T_OLD', 'newTeacherId':'T_NEW', 'date':'2026-10-05', 'type':'sub'}
assert ns['_substitution_targets_teacher'](sub, 'T_NEW')
assert ns['_substitution_targets_teacher'](sub, 'T_OLD')

project = {
    'publicWeekSelectionEnabled': False,
    'config': {},
    'loads': [
        {'id':'L1','groupId':'G1','teacherId':'T_OLD','subjectId':'S1','typeId':'P'}
    ],
    'schedule': {
        # Simulate an already republished week whose source metadata itself drifted
        # to the disposable public occurrence id.
        'instances': [{
            'instId':'I1__PUBW6', '_publishedSourceInstId':'I1__PUBW6',
            'loadId':'L1','groupId':'G1','teacherId':'T_OLD','subjectId':'S1','typeId':'P'
        }],
        'assignment': {'I1__PUBW6': {'day':0,'period':1,'roomId':'R1'}},
        'locked': [], 'unplaced': []
    },
    'substitutions': [sub],
}

# Receiver of the replacement must get the source occurrence in their public shard
# even though the base lesson belongs to another teacher and the publication id differs.
sh = ns['_build_public_schedule_shard'](project, 'teacher', 'T_NEW')
assert [x['instId'] for x in sh['instances']] == ['I1__PUBW6'], sh
patch = ns['_public_entity_data_patch'](project, sh['instances'])
assert len(patch['substitutions']) == 1
assert patch['substitutions'][0]['newTeacherId'] == 'T_NEW'

# Group publication must rebuild BOTH original and receiving teacher shards if a
# legacy replacement row happens to carry both teacherId and newTeacherId.
teacher_ids = ns['_publication_teacher_ids_for_groups'](project, {'G1'})
assert 'T_OLD' in teacher_ids
assert 'T_NEW' in teacher_ids

app_src = src
assert 'PUBLIC_SHARD_SCHEMA_VERSION = 1890' in app_src
assert 'str(sub.get("teacherId") or sub.get("newTeacherId") or "") == entity_id' not in app_src
print('v1890: incoming public replacement teacher shard tests passed')
