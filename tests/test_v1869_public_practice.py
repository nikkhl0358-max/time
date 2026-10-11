from pathlib import Path
import ast
from datetime import date, timedelta

src = Path(__file__).resolve().parents[1].joinpath('app.py').read_text()
tree = ast.parse(src)
wanted = {'_instance_group_ids','_total_semester_weeks_py','_instance_weeks_py','_instance_visible_weeks_for_assignment_py','_build_public_visibility_context','_trim_instance_to_public_weeks','_build_public_schedule_shard'}
mod = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted], type_ignores=[])
ns={'date':date,'timedelta':timedelta}
exec(compile(mod,'app-selected','exec'),ns)
ns['_schedule_instance_group_ids'] = lambda inst: set(ns['_instance_group_ids'](inst))
build=ns['_build_public_schedule_shard']

def project(moved=False, event=False):
    inst={"instId":"stream1","groupId":"g40","teacherId":"t1","subjectId":"s1","typeId":"lt1","weekPattern":"custom","customWeeks":[5],"format":"remote","streamParticipants":[{"groupId":"g40","subgroup":0},{"groupId":"g44","subgroup":0}]}
    if event: inst.update(isScheduleEvent=True,eventTitle='Event')
    p={"config":{"semesterStart":"2026-09-01","semesterEnd":"2026-12-31"},"schedule":{"instances":[inst],"assignment":{"stream1":{"day":0,"period":3,"roomId":None}},"locked":[],"unplaced":[]},"practices":[{"id":"p1","groupId":"g40","dateStart":"2026-09-28","dateEnd":"2026-09-28"}],"substitutions":[],"loads":[]}
    if moved: p['substitutions']=[{"type":"moved","instId":"stream1","date":"2026-09-28","targetDate":"2026-09-29","targetPeriod":2}]
    return p

p=project()
assert build(p,'teacher','t1')['instances']==[]
assert build(p,'group','g44')['instances']==[]
rows=build(project(moved=True),'teacher','t1')['instances']
assert len(rows)==1 and rows[0]['instId']=='stream1'
rows=build(project(event=True),'teacher','t1')['instances']
assert len(rows)==1 and rows[0].get('isScheduleEvent') is True
print('v1869 public-practice shard assertions passed')
