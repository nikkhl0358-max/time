/** Проверяет атомарную загрузку разделов и запрет полной записи неполного проекта. */
import assert from 'node:assert/strict';
import {
  EDITOR_SECTIONS, editorTabSections, editorBootstrapGet, editorHydrate,
  editorMissingSections, getEditorDeferred, setEditorDeferred, storageFetch,
} from '../frontend/src/editorTransport.js';
import { storageGet, storageSet } from '../frontend/src/storage.js';

const originalFetch = globalThis.fetch;
const requests = [];
const bootstrap = { config: { semesterStart: '2026-08-31' }, users: [], _syncMeta: { saveId: 'save-1' }, _editorDeferred: [...EDITOR_SECTIONS] };
const values = { loads: [{ id: 'l1' }], schedule: { instances: [{ instId: 'i1', loadId: 'l1' }] }, graphState: {}, publishedSnapshot: null, _groupRestoreUndo: {} };

function mockServer(staleSection = '') {
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    const parsed = new URL(url, 'https://example.test');
    const section = parsed.searchParams.get('section');
    const value = section === 'bootstrap' ? { value: bootstrap, token: 'revision-1', deferredSections: [...EDITOR_SECTIONS] }
      : { section, value: values[section], token: 'revision-1', ...(section === 'schedule' ? { cellRevisions: { i1: 7 } } : {}) };
    const status = section === staleSection && section ? 409 : 200;
    return { ok: status === 200, status, json: async () => status === 409 ? { error: 'revision changed' } : value };
  };
}

async function checkBootstrap() {
  setEditorDeferred(EDITOR_SECTIONS);
  mockServer();
  const response = await editorBootstrapGet();
  assert.equal(requests.length, 1, 'Login must request only bootstrap');
  assert.equal(response.value.schedule, undefined);
  assert.equal(response.value.publishedSnapshot, undefined);
  assert.deepEqual(editorTabSections('semester'), []);
  assert.deepEqual(editorTabSections('config'), []);
  assert.ok(editorTabSections('loads').includes('schedule'), 'Load transfer needs placements');
}

async function checkHydration() {
  const response = await editorHydrate(bootstrap, 'revision-1', editorTabSections('schedule'));
  assert.equal(response.value.schedule.instances[0].loadId, response.value.loads[0].id);
  assert.deepEqual(response.value._editorDeferred, ['publishedSnapshot', '_groupRestoreUndo']);
  assert.equal(response.cellRevisions.i1, 7);
  assert.equal(bootstrap.schedule, undefined, 'Hydration must not mutate the current editor');
  assert.equal(getEditorDeferred().length, 5, 'Headers change only after caller commits a complete result');
  setEditorDeferred(response.value._editorDeferred);
  const complete = await editorHydrate(response.value, 'revision-1', EDITOR_SECTIONS);
  assert.equal(complete.value.publishedSnapshot, null, 'Null is a loaded value, not a deferred section');
  assert.deepEqual(editorMissingSections(complete.value, EDITOR_SECTIONS), []);
}

async function checkConflict() {
  mockServer('schedule');
  await assert.rejects(() => editorHydrate(bootstrap, 'revision-1', editorTabSections('schedule')), error => error.status === 409);
  assert.equal(bootstrap.loads, undefined, 'Failed section must not partially apply other responses');
  assert.deepEqual(bootstrap._editorDeferred, EDITOR_SECTIONS);
}

async function checkProjectionAndWrites() {
  mockServer();
  setEditorDeferred(['publishedSnapshot']);
  await storageGet('schedule-data-v2');
  const request = requests.at(-1);
  assert.ok(request.url.includes('editor=1'), 'Ordinary reads must not download a full project');
  assert.equal(request.options.headers.get('X-Editor-Deferred'), 'publishedSnapshot');
  const count = requests.length;
  await assert.rejects(() => storageSet('schedule-data-v2', '{}'), error => error.status === 409);
  assert.equal(requests.length, count, 'Deferred full writes must fail before the network');
  setEditorDeferred([]);
  await storageSet('schedule-data-v2', '{}');
  assert.equal(requests.at(-1).options.method, 'POST');
  assert.equal(requests.at(-1).options.headers.get('X-Editor-Deferred'), '');
}

try {
  await checkBootstrap();
  await checkHydration();
  await checkConflict();
  await checkProjectionAndWrites();
  console.log('Editor client regression checks OK');
} finally {
  globalThis.fetch = originalFetch;
  setEditorDeferred(EDITOR_SECTIONS);
}
