/** Откладывает тяжёлые разделы, сохраняя отличие «не загружено» от намеренного удаления. */
export const EDITOR_SECTIONS = Object.freeze(['loads', 'schedule', 'graphState', 'publishedSnapshot', '_groupRestoreUndo']);
const TAB_SECTIONS_MAP = {
  semester: [],
  constraints: [],
  announcements: [],
  consultations: [],
  lists: [],
  curriculum: [],
  substitutions: [],
  scenarios: [],
  attestation: [],
  practices: [],
  history: [],
  versions: [],
  config: [],
  users: [],
  loads: ['loads', 'schedule'],
  graphs: ['loads', 'graphState'],
  schedule: ['loads', 'schedule', 'graphState'],
  actualHours: ['loads', 'schedule'],
  conflicts: ['loads', 'schedule'],
  bulkEdit: ['loads', 'schedule'],
  analytics: ['loads', 'schedule'],
};
let deferredSections = [...EDITOR_SECTIONS];

export function editorTabSections(tab) {
  return TAB_SECTIONS_MAP[tab] || [];
}

export function setEditorDeferred(sections = []) {
  deferredSections = EDITOR_SECTIONS.filter(section => sections.includes(section));
}

export function getEditorDeferred() {
  return [...deferredSections];
}

export function editorMissingSections(project, wanted) {
  return wanted.filter(section => (project?._editorDeferred || []).includes(section));
}

export function editorRequestHeaders(headers = {}) {
  const result = new Headers(headers);
  result.set('X-Editor-Deferred', deferredSections.join(','));
  return result;
}

export function storageFetch(url, options = {}) {
  const method = options.method || 'GET';
  if (method === 'GET' && /^\/api\/storage\//.test(url)) {
    url += `${url.includes('?') ? '&' : '?'}editor=1`;
  }
  return fetch(url, { ...options, headers: editorRequestHeaders(options.headers) });
}

// Только GET-запросы чтения: при кратковременном обрыве соединения можно
// безопасно повторить загрузку. POST/сохранение здесь не затрагиваются.
const RETRYABLE_EDITOR_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);
const waitForEditorRetry = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function editorResponse(url) {
  const attempts = 3;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    let response;
    try {
      response = await storageFetch(url, { cache: 'no-store' });
    } catch (error) {
      if (attempt < attempts) {
        await waitForEditorRetry(350 * attempt);
        continue;
      }
      throw new Error(`Нет соединения с сервером при загрузке редактора (${url}). ${error?.message || ''}`.trim());
    }

    // При 304 тело намеренно пустое. Для данных редактора всегда требуется
    // полноценный JSON — повторяем запрос без условного кеширования.
    const body = await response.text().catch(() => '');
    let result = null;
    try { if (body.trim()) result = JSON.parse(body); } catch { /* неполный ответ */ }

    if (!response.ok) {
      const retryable = response.status === 304 || RETRYABLE_EDITOR_STATUS.has(response.status);
      if (retryable && attempt < attempts) {
        await waitForEditorRetry(350 * attempt);
        continue;
      }
      const error = new Error(result?.error || `Сервер не смог загрузить раздел редактора (HTTP ${response.status}, ${url}).`);
      error.status = response.status;
      throw error;
    }
    if (result && typeof result === 'object') return result;
    if (attempt < attempts) {
      await waitForEditorRetry(350 * attempt);
      continue;
    }
    throw new Error(`Сервер вернул пустой или неполный JSON при загрузке редактора (${url}, HTTP ${response.status}). Проверьте журнал сервера и ответ запроса в Network.`);
  }
}

export function editorBootstrapGet(key = 'schedule-data-v2') {
  return editorResponse(`/api/editor/${encodeURIComponent(key)}?section=bootstrap`);
}

export async function editorHydrate(project, token, wanted, key = 'schedule-data-v2') {
  const missing = editorMissingSections(project, wanted);
  const rows = await Promise.all(missing.map(section => editorResponse(
    `/api/editor/${encodeURIComponent(key)}?section=${encodeURIComponent(section)}&token=${encodeURIComponent(token || '')}`
  )));
  if (rows.some(row => String(row.token || '') !== String(token || ''))) {
    throw Object.assign(new Error('Данные изменились во время загрузки'), { status: 409 });
  }
  const next = { ...project, _editorDeferred: (project._editorDeferred || []).filter(section => !missing.includes(section)) };
  for (const row of rows) next[row.section] = row.value;
  return { value: next, token, cellRevisions: Object.assign({}, ...rows.map(row => row.cellRevisions || {})) };
}

export async function editorExportDownload(key = 'schedule-data-v2') {
  const response = await storageFetch(`/api/editor/${encodeURIComponent(key)}/export`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Не удалось скачать проект: HTTP ${response.status}`);
  const url = URL.createObjectURL(await response.blob());
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = 'schedule-project.json';
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
