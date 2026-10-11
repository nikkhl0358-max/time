import fs from 'node:fs';
import assert from 'node:assert/strict';

const app = fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url), 'utf8');
const storage = fs.readFileSync(new URL('../frontend/src/storage.js', import.meta.url), 'utf8');
const server = fs.readFileSync(new URL('../app.py', import.meta.url), 'utf8');

assert.ok(app.includes('placeholder="Новый кабинет…"'));
assert.ok(app.includes('onChange={(v)=>changeResource(row,{newRoomId:v})}'));
assert.ok(app.includes('const effRoomId=sub?.newRoomId||a.roomId||""'));
assert.ok(app.includes('было: {room}'));
assert.ok(app.includes('roomId: sub.newRoomId || row.a.roomId'));
assert.match(storage, /CLIENT_BUILD_ID = "1886"/);
assert.match(server, /CURRENT_SCHEDULE_CLIENT_BUILD = 1886/);
console.log('v1886: substitution room change assertions passed');
