import fs from 'node:fs';
const app=fs.readFileSync(new URL('../frontend/src/App.jsx', import.meta.url),'utf8');
for (const needle of [
  '↪ Передать остаток',
  'Всё, что ещё не проведено',
  'Только ещё не расставленные пары',
  'persistRemainingLoadTransfer',
  'scopeTab:"bulkEdit"',
  'assignmentOccurrenceHasStarted',
]) if(!app.includes(needle)) throw new Error(`missing: ${needle}`);
if(app.includes('onTransferScheduleTeachers={persistTransferredLoadTeachers}')) throw new Error('old destructive transfer callback still wired');
console.log('v1875: UI/history-transfer static assertions passed');
