/** Проверяет, что сбои транспорта повторяются, а конфликты сохранения сразу видны пользователю. */
import assert from 'node:assert/strict';
import { setEditorDeferred } from '../frontend/src/editorTransport.js';
setEditorDeferred([]);
import { storageGetStrict, storageSet } from '../frontend/src/storage.js';

const originalFetch = globalThis.fetch;
const originalTimeout = globalThis.setTimeout;
// Ускоряем только паузы retry; таймаут запроса сохраняет реальные 60 секунд.
globalThis.setTimeout = (callback, delay, ...args) => originalTimeout(callback, delay < 5000 ? 0 : delay, ...args);

function responses(sequence) {
  let calls = 0;
  globalThis.fetch = async () => {
    const item = sequence[Math.min(calls++, sequence.length - 1)];
    if (item instanceof Error) throw item;
    return { ok: item.status < 400, status: item.status, json: async () => item.body };
  };
  return () => calls;
}

async function rejectsOnce(operation, status) {
  const calls = responses([{ status, body: { error: 'rejected' } }]);
  await assert.rejects(operation, error => error.status === status);
  assert.equal(calls(), 1, `HTTP ${status} must not be retried`);
}

async function recovers(operation, first) {
  const calls = responses([first, { status: 200, body: { value: 'saved' } }]);
  assert.equal((await operation()).value, 'saved');
  assert.equal(calls(), 2);
}

async function checkRetries() {
  const read = () => storageGetStrict('test', { attempts: 2, delayMs: 100 });
  const write = () => storageSet('test', '{}', { attempts: 2 });
  for (const operation of [read, write]) {
    for (const status of [400, 401, 403, 409, 413]) await rejectsOnce(operation, status);
    await recovers(operation, { status: 503, body: { error: 'busy' } });
    await recovers(operation, new TypeError('network unavailable'));
  }
  const calls = responses([{ status: 200, body: { value: '' } }]);
  assert.equal((await read()).empty, true);
  assert.equal(calls(), 2);
  responses([{ status: 404, body: {} }]);
  assert.equal((await read()).missing, true);
}

try {
  await checkRetries();
  console.log('Storage retry regression checks OK');
} finally {
  globalThis.fetch = originalFetch;
  globalThis.setTimeout = originalTimeout;
}
