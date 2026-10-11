import ast
from pathlib import Path

src = Path('app.py').read_text(encoding='utf-8')
tree = ast.parse(src)
needed = {'_instance_group_ids', '_repair_public_implicit_stream_membership'}
parts = []
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in needed:
        parts.append(ast.get_source_segment(src, node))
ns = {}
exec('\n\n'.join(parts), ns)
repair = ns['_repair_public_implicit_stream_membership']

source = {
    'loads': [{
        'id':'l10','groupId':'g10','teacherId':'t','subjectId':'s','typeId':'lec',
        'format':'remote','remoteSubgroupLoadIds':['l12','l14']
    }],
    'schedule': {
        'instances': [{
            'instId':'i1__PUBW6','groupId':'g10','loadId':'l10','teacherId':'t',
            'subjectId':'s','typeId':'lec','format':'remote','weekPattern':'custom','customWeeks':[6]
        }],
        'assignment': {'i1__PUBW6': {'day':2,'period':0}}
    }
}
working = {
    'loads': [
        {'id':'l10','groupId':'g10','teacherId':'t','subjectId':'s','typeId':'lec','format':'remote','remoteSubgroupLoadIds':['l12','l14']},
        {'id':'l12','groupId':'g12','teacherId':'t','subjectId':'s','typeId':'lec','format':'remote','remoteSubgroupLoadIds':['l10']},
        {'id':'l14','groupId':'g14','teacherId':'t','subjectId':'s','typeId':'lec','format':'remote','remoteSubgroupLoadIds':['l10']},
    ]
}

out = repair(source, working)
inst = out['schedule']['instances'][0]
assert inst['groupIds'] == ['g10','g12','g14']
assert inst['streamGroupIds'] == ['g12','g14']
assert [p['loadId'] for p in inst['streamParticipants']] == ['l12','l14']
assert {row['id'] for row in out['loads']} == {'l10','l12','l14'}
assert inst['_publicStreamMembershipRecovered'] is True

# Transfer-split rows must never be joined back to legacy peers.
source_split = {
    'loads': source['loads'],
    'schedule': {'instances': [{**source['schedule']['instances'][0], 'manualStreamBreak': True}], 'assignment': {}}
}
out_split = repair(source_split, working)
assert 'streamParticipants' not in out_split['schedule']['instances'][0]
print('v1892: public implicit stream recovery assertions passed')
