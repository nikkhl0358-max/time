from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
src = APP.read_text(encoding="utf-8")
tree = ast.parse(src)
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_repair_public_implicit_stream_membership")
mod = ast.Module(body=[node], type_ignores=[])
ns = {}

def _instance_group_ids(inst):
    out=[]
    if inst.get("groupId"): out.append(str(inst["groupId"]))
    explicit=inst.get("streamParticipants") or []
    if any(isinstance(x,dict) and x.get("groupId") for x in explicit):
        out += [str(x["groupId"]) for x in explicit if isinstance(x,dict) and x.get("groupId")]
    else:
        out += [str(x) for x in (inst.get("streamGroupIds") or []) if x]
    return list(dict.fromkeys(out))

ns["_instance_group_ids"] = _instance_group_ids
exec(compile(mod, str(APP), "exec"), ns)
repair=ns["_repair_public_implicit_stream_membership"]

loads=[
    {"id":"l36","groupId":"g36","subjectId":"s","teacherId":"t","typeId":"pr","subgroup":1,"streamId":"old"},
    {"id":"l42","groupId":"g42","subjectId":"s","teacherId":"t","typeId":"pr","subgroup":1,"streamId":"old"},
    {"id":"l52","groupId":"g52","subjectId":"s","teacherId":"t","typeId":"pr","subgroup":1,"streamId":"old"},
]
working={"loads":loads}
# Explicit empty occurrence scope means standalone lesson. It MUST stay standalone.
source={"loads":[loads[0]],"schedule":{"instances":[{"instId":"i1","loadId":"l36","groupId":"g36","streamParticipants":[],"streamGroupIds":[]}]}}
out=repair(source,working)
i=out["schedule"]["instances"][0]
assert i.get("streamParticipants") == []
assert i.get("streamGroupIds") == []
assert not i.get("_publicStreamMembershipRecovered")

# Truly legacy occurrence has no scope fields at all; recovery still works.
legacy={"loads":[loads[0]],"schedule":{"instances":[{"instId":"i2","loadId":"l36","groupId":"g36"}]}}
out2=repair(legacy,working)
i2=out2["schedule"]["instances"][0]
assert i2.get("_publicStreamMembershipRecovered") is True
assert set(i2.get("streamGroupIds") or []) == {"g42","g52"}

assert 'PUBLIC_SHARD_SCHEMA_VERSION = 1893' in src
assert 'CURRENT_SCHEDULE_CLIENT_BUILD = 1893' in src
assert 'CLIENT_BUILD_ID = "1893"' in (ROOT/'frontend/src/storage.js').read_text(encoding='utf-8')
print('v1893: explicit occurrence stream scope wins over load-level links')
