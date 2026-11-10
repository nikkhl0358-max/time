/**
 * Синхронизация маршрутов и сохранения состояния расписания:
 * чтение URL-параметров (path, search, hash), localStorage,
 * поддержка формата /specialty/:specId/year/:year/group/:groupId
 * и управление историей браузера (pushState vs replaceState).
 */

const STORAGE_KEY = 'timetable_public_selection';

function safeDecode(val) {
  try {
    return decodeURIComponent(String(val || ''));
  } catch {
    return String(val || '');
  }
}

function findEntityInParts(parts) {
  const groupIdx = parts.indexOf('group');
  if (groupIdx !== -1 && parts[groupIdx + 1]) {
    return { kind: 'group', id: safeDecode(parts[groupIdx + 1]) };
  }
  const teacherIdx = parts.indexOf('teacher');
  if (teacherIdx !== -1 && parts[teacherIdx + 1]) {
    return { kind: 'teacher', id: safeDecode(parts[teacherIdx + 1]) };
  }
  const roomIdx = parts.indexOf('room');
  if (roomIdx !== -1 && parts[roomIdx + 1]) {
    return { kind: 'room', id: safeDecode(parts[roomIdx + 1]) };
  }
  return null;
}

export function parseUrlPath(pathname) {
  const parts = (pathname || '').split('/').filter(Boolean);
  return findEntityInParts(parts);
}

export function parseUrlHash(hash) {
  if (!hash) return null;
  const [cleanHash] = hash.replace(/^#\/?/, '').split('?');
  const hyphenMatch = cleanHash.match(/^(group|teacher|room)-([a-zA-Z0-9_-]+)$/);
  if (hyphenMatch) {
    return { kind: hyphenMatch[1], id: safeDecode(hyphenMatch[2]) };
  }
  const parts = cleanHash.split('/').filter(Boolean);
  return findEntityInParts(parts);
}

export function parseUrlSearch(search) {
  const params = new URLSearchParams(search || '');
  const rawWeek = params.get('week');
  const numWeek = rawWeek ? Number(rawWeek) : null;
  const week = (numWeek && !isNaN(numWeek) && numWeek > 0 && Number.isFinite(numWeek)) ? Math.floor(numWeek) : null;
  if (params.get('group')) return { kind: 'group', id: safeDecode(params.get('group')), week };
  if (params.get('teacher')) return { kind: 'teacher', id: safeDecode(params.get('teacher')), week };
  if (params.get('room')) return { kind: 'room', id: safeDecode(params.get('room')), week };
  if (params.get('kind') && params.get('id')) {
    return { kind: safeDecode(params.get('kind')), id: safeDecode(params.get('id')), week };
  }
  return { week };
}

function extractHashWeek() {
  if (typeof window === 'undefined' || !window.location.hash?.includes('?')) return null;
  const hash = window.location.hash;
  const qs = hash.slice(hash.indexOf('?') + 1);
  const w = new URLSearchParams(qs).get('week');
  const num = w ? Number(w) : null;
  return (num && !isNaN(num) && num > 0 && Number.isFinite(num)) ? Math.floor(num) : null;
}

export function parseCurrentRoute() {
  if (typeof window === 'undefined') return { kind: 'group', id: '', week: null };
  const fromPath = parseUrlPath(window.location.pathname);
  const fromHash = parseUrlHash(window.location.hash);
  const fromSearch = parseUrlSearch(window.location.search);
  const entity = fromPath || fromHash || (fromSearch.id ? fromSearch : null);
  return {
    kind: entity?.kind || 'group',
    id: entity?.id || '',
    week: fromSearch.week || extractHashWeek()
  };
}

export function readSavedSelection() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function saveSelection(kind, id, week) {
  try {
    if (!id || typeof id !== 'string') return;
    const cleanWeek = (typeof week === 'number' && week > 0 && Number.isFinite(week)) ? Math.floor(week) : null;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ kind: String(kind || 'group'), id: String(id), week: cleanWeek }));
  } catch (err) {
    console.warn('Не удалось сохранить выбор расписания:', err);
  }
}

export function clearSavedSelection() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {}
}

export function entityExists(data, kind, id) {
  if (!id || !data) return false;
  const list = kind === 'teacher' ? data.teachers : (kind === 'room' ? data.rooms : data.groups);
  return Array.isArray(list) && list.some(item => String(item.id) === String(id));
}

export function calculateCurrentSemesterWeek(semesterStart, today = new Date()) {
  if (!semesterStart) return null;
  const p = String(semesterStart).split('-').map(Number);
  if (p.length < 3 || p.some(isNaN)) return null;
  const [y, m, d] = p;
  const startDate = new Date(y, m - 1, d, 12, 0, 0);
  const mondayOffset = (startDate.getDay() + 6) % 7;
  startDate.setDate(startDate.getDate() - mondayOffset);
  const diffDays = Math.round((today.getTime() - startDate.getTime()) / 86400000);
  if (diffDays < 0 || isNaN(diffDays)) return null;
  return Math.floor(diffDays / 7) + 1;
}

function resolveFallbackWeek(data, requestedWeek) {
  if (requestedWeek && Number.isFinite(requestedWeek) && requestedWeek >= 1 && requestedWeek <= 24) {
    return requestedWeek;
  }
  const published = Array.isArray(data?.publishedWeeks) ? data.publishedWeeks : [];
  const calWeek = calculateCurrentSemesterWeek(data?.config?.semesterStart);
  if (calWeek && (published.includes(calWeek) || !data?.publicWeekSelectionEnabled)) {
    return calWeek;
  }
  if (published.length) {
    return published[published.length - 1];
  }
  return calWeek || 10;
}

function resolveTargetEntity(data, route, saved) {
  const validKinds = ['group', 'teacher', 'room'];
  let kind = validKinds.includes(route.kind) ? route.kind : (saved?.kind && validKinds.includes(saved.kind) ? saved.kind : 'group');
  let id = (typeof route.id === 'string' && route.id) || (typeof saved?.id === 'string' && saved.id) || '';
  if (route.id && !entityExists(data, route.kind, route.id)) {
    id = saved?.id && typeof saved.id === 'string' && entityExists(data, kind, saved.id) ? saved.id : '';
    kind = id ? kind : 'group';
  } else if (id && !entityExists(data, kind, id)) {
    id = '';
  }
  return { kind, id };
}

function resolveTargetWeek(data, route, saved) {
  const calWeek = calculateCurrentSemesterWeek(data?.config?.semesterStart);
  const published = Array.isArray(data?.publishedWeeks) ? data.publishedWeeks : [];
  if (route.week) return resolveFallbackWeek(data, route.week);
  if (calWeek && published.includes(calWeek)) return calWeek;
  if (published.length) return published[published.length - 1];
  if (typeof saved?.week === 'number' && published.includes(saved.week)) return saved.week;
  return resolveFallbackWeek(data, calWeek || 10);
}

export function resolveInitialSelection(data) {
  const route = parseCurrentRoute();
  const rawSaved = readSavedSelection();
  const saved = (rawSaved && typeof rawSaved === 'object' && !Array.isArray(rawSaved)) ? rawSaved : null;
  const { kind, id } = resolveTargetEntity(data, route, saved);
  const week = resolveTargetWeek(data, route, saved);
  return { kind, id, week };
}

function buildGroupUrl(group) {
  if (group && group.admissionYear) {
    const spec = group.specialtyId ? encodeURIComponent(String(group.specialtyId)) : 'none';
    const yr = encodeURIComponent(String(group.admissionYear));
    const gid = encodeURIComponent(String(group.id));
    return `/specialty/${spec}/year/${yr}/group/${gid}`;
  }
  return `/group/${encodeURIComponent(String(group?.id || ''))}`;
}

export function syncBrowserUrl(kind, id, week, groupsList = [], opts = {}) {
  if (typeof window === 'undefined' || window.location.pathname.startsWith('/admin')) return;
  const mode = opts.mode || 'replace';
  if (!id) {
    if (window.location.pathname !== '/') {
      const fn = mode === 'push' ? window.history.pushState : window.history.replaceState;
      fn.call(window.history, null, '', '/');
    }
    return;
  }
  let basePath = `/${kind}/${encodeURIComponent(String(id))}`;
  if (kind === 'group' && Array.isArray(groupsList)) {
    const found = groupsList.find(g => String(g.id) === String(id));
    if (found) basePath = buildGroupUrl(found);
  }
  const newUrl = `${basePath}${week ? `?week=${week}` : ''}`;
  if (window.location.pathname + window.location.search !== newUrl) {
    const fn = mode === 'push' ? window.history.pushState : window.history.replaceState;
    fn.call(window.history, null, '', newUrl);
  }
}
