import fs from 'fs';
import assert from 'assert';

const app = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
const py = fs.readFileSync(new URL('../app.py', import.meta.url), 'utf8');
const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');

assert.match(storage, /CLIENT_BUILD_ID\s*=\s*"1891"/);
assert.match(py, /PUBLIC_SHARD_SCHEMA_VERSION\s*=\s*1891/);
assert.match(py, /CURRENT_SCHEDULE_CLIENT_BUILD\s*=\s*1891/);
assert.match(app, /function effectiveInstanceGroupIds\(inst\)/);
assert.match(app, /for \(const gid of \(inst\.groupIds \|\| \[\]\)\) add\(gid\)/);
assert.match(app, /return streamGroups\(inst\)\.map\(String\)\.includes\(String\(groupId\)\)/);
assert.match(py, /group_ids\.update\(_instance_group_ids\(inst\)\)/);
assert.match(py, /expanded\.update\(members\)/);
assert.match(py, /group_ids = expanded/);

// Execute the standalone JS membership helper to cover the actual ordinary vs
// transfer-split behavior rather than only checking strings.
const start = app.indexOf('function effectiveInstanceGroupIds(inst)');
const end = app.indexOf('function streamGroups(inst)', start);
assert.ok(start >= 0 && end > start);
const helperSrc = app.slice(start, end);
const helper = new Function(`${helperSrc}; return effectiveInstanceGroupIds;`)();
assert.deepStrictEqual(helper({groupId:'10', groupIds:['10','12','14'], streamParticipants:[{groupId:'12'}], streamGroupIds:['99']}), ['10','12','14']);
assert.deepStrictEqual(helper({groupId:'10', groupIds:['10','12','14'], manualStreamBreak:true, streamParticipants:[{groupId:'12'}], streamGroupIds:['99']}), ['10','12']);

console.log('v1891: public stream membership assertions passed');
