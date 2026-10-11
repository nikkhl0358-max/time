import fs from 'node:fs';
import assert from 'node:assert/strict';

const app = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');
const server = fs.readFileSync(new URL('../app.py', import.meta.url), 'utf8');

assert.match(storage, /CLIENT_BUILD_ID = "1878"/);
assert.match(server, /CURRENT_SCHEDULE_CLIENT_BUILD = 1878/);
assert.match(server, /PUBLIC_SHARD_SCHEMA_VERSION = 1878/);
assert.match(server, /def _repair_public_shard_schema_if_needed\(manifest\):/);
assert.match(server, /manifest = _repair_public_shard_schema_if_needed\(manifest\)/);
assert.match(app, /v1878 HOTFIX: public correctness/);
assert.match(app, /relevantEnrichedInstances\s*\n\s*\.map\(\(inst\) => \(\{ inst, a: sched\.assignment\?\.\[inst\.instId\] \}\)\)/);
assert.match(app, /Number\(r\.a\.day\) === Number\(day\)/);
assert.doesNotMatch(app, /const publicAssignmentsByDay = useMemo/);
assert.match(app, /partialStreamCellForGroup\(cell, r\.inst, r\.a, viewId\)/);
assert.match(app, /stream_group_cancel/);
assert.match(server, /full_schedule = _build_public_schedule_shard\(source, kind, entity_id\)\n/);
assert.match(server, /"schemaVersion": PUBLIC_SHARD_SCHEMA_VERSION/);
console.log('v1878 public recovery assertions passed');
