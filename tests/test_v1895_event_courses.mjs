import fs from 'fs';
const app=fs.readFileSync('frontend/src/App.jsx','utf8');
const storage=fs.readFileSync('frontend/src/storage.js','utf8');
const server=fs.readFileSync('app.py','utf8');
function ok(v,m){if(!v) throw new Error(m)}
ok(app.includes('option value="courses">Курсы</option>'),'courses option missing');
ok(app.includes('eventKind: String(d.eventKind || "event")'),'eventKind persistence missing');
ok(app.includes('schedule-course-chip'),'admin course color class missing');
ok(app.includes('schedule-course-public'),'public course class missing');
ok(app.includes('📚 Курсы'),'public course badge missing');
ok(app.includes('eventKind: isScheduleEvent ? scheduleEventKind(cell.inst) : ""'),'semester course kind missing');
ok(storage.includes('CLIENT_BUILD_ID = "1895"'),'client build not 1895');
ok(server.includes('CURRENT_SCHEDULE_CLIENT_BUILD = 1895'),'server build not 1895');
console.log('v1895: event courses assertions passed');
