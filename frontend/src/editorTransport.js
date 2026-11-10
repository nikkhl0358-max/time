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
  return fetch(url, {
    credentials: options.credentials || 'same-origin',
    ...options,
    headers: editorRequestHeaders(options.headers)
  });
}

async function editorResponse(url) {
  const response = await storageFetch(url, { cache: 'no-cache' });
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error || `Ошибка загрузки раздела: HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return result;
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
