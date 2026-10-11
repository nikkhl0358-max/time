import fs from 'node:fs';
import assert from 'node:assert/strict';
const root = new URL('../', import.meta.url);
const app = fs.readFileSync(new URL('frontend/src/App.jsx', root), 'utf8');
const server = fs.readFileSync(new URL('app.py', root), 'utf8');
const storage = fs.readFileSync(new URL('frontend/src/storage.js', root), 'utf8');

assert.match(app, /scheduleMove = \(instId, targetDate, targetPeriod, newFormat = "", newRoomId = ""\)/);
assert.match(app, /newRoomId:effectiveFormat==="remote"\?"":effectiveRoomId\|\|undefined/);
assert.match(app, /Аудитория<SearchableSelect/);
assert.match(app, /scheduleMove\(moveLater\.instId,moveLater\.targetDate,moveLater\.targetPeriod,moveLater\.newFormat,moveLater\.newRoomId\)/);
assert.match(app, /cell\.subOverride\?\.moved && <div className="ev-row ev-subgroup">↪ Перенесено с/);
assert.match(server, /PUBLIC_SHARD_SCHEMA_VERSION = 1894/);
assert.match(server, /CURRENT_SCHEDULE_CLIENT_BUILD = 1894/);
assert.match(server, /support_source_ids\.update\(_public_substitution_source_ids\(sub\)\)/);
assert.match(server, /_publicMoveSupportOnly/);
assert.match(server, /if normal_week:\n\s+out\["weekPattern"\] = "custom"/);
assert.match(storage, /CLIENT_BUILD_ID = "1894"/);
console.log('v1894: moved target + room picker assertions passed');
