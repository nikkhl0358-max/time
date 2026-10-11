from pathlib import Path
import ast
src=Path(__file__).resolve().parents[1].joinpath('app.py').read_text()
tree=ast.parse(src)
wanted={'_instance_group_ids','_trim_instance_to_public_weeks','_public_instance_teacher_ids','_build_public_schedule_shard','_publication_teacher_ids_for_groups'}
module=ast.Module(body=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in wanted], type_ignores=[])
ns={}
exec(compile(module,'app_subset.py','exec'),ns)
project={
 'publicWeekSelectionEnabled':False,
 'config':{},
 'loads':[{'id':'L1','groupId':'G1','teacherId':'T1','subjectId':'S1','typeId':'P'}],
 'schedule':{'instances':[{'instId':'I1','loadId':'L1','groupId':'G1','teacherId':'STALE','subjectId':'S1','typeId':'P'}], 'assignment':{'I1':{'day':0,'period':1}}, 'locked':[], 'unplaced':[]},
 'substitutions':[]
}
inst=project['schedule']['instances'][0]
assert ns['_public_instance_teacher_ids'](project,inst)=={'STALE','T1'}
sh=ns['_build_public_schedule_shard'](project,'teacher','T1')
assert [x['instId'] for x in sh['instances']]==['I1']
assert 'T1' in sh['instances'][0]['teacherIds']
project['loads'].append({'id':'L2','groupId':'G2','teacherId':'T2','subjectId':'S1','typeId':'P'})
inst['streamParticipants']=[{'groupId':'G2','loadId':'L2'}]
sh2=ns['_build_public_schedule_shard'](project,'teacher','T2')
assert len(sh2['instances'])==1
assert 'T2' in sh2['instances'][0]['teacherIds']
assert 'T1' in ns['_publication_teacher_ids_for_groups'](project, {'G1'})
assert 'T2' in ns['_publication_teacher_ids_for_groups'](project, {'G2'})
print('v1870: public teacher shard authoritative-load tests passed')
