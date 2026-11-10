/**
 * Управление данными расписания: загрузка публичного индекса,
 * шардов групп/преподавателей/кабинетов, замен и справочников.
 */
import { ref, computed } from 'vue';
import { publicIndexGet, publicScheduleGet } from '../storage.js';
import { resolveInitialSelection, saveSelection, syncBrowserUrl } from './useRouteSync.js';
import { getRoomCategoryDetails } from '../utils/scheduleSubstitutions.js';

function mapInstancePlacement(inst, assignment) {
  const a = assignment[inst.instId] || assignment[inst.id] || {};
  const day = a.day !== undefined ? Number(a.day) : (inst.day !== undefined ? Number(inst.day) : undefined);
  const period = a.period !== undefined ? Number(a.period) + 1 : (inst.period !== undefined ? Number(inst.period) : 1);
  const isRemote = ['remote', 'distance', 'dist'].includes(inst.format);
  const roomId = isRemote ? '' : (a.roomId || inst.dedicatedRoomId || inst.preferredRoomIds?.[0] || '');
  return { ...inst, day, period, roomId, extraRoomId: isRemote ? null : (a.extraRoomId || null), assignment: a };
}

function normalizeScheduleData(schedule) {
  if (!schedule) return { instances: [], assignment: {}, rawInstances: [] };
  const assignment = schedule.assignment || {};
  const rawInstances = Array.isArray(schedule.instances) ? schedule.instances : [];
  const instances = rawInstances
    .map(inst => mapInstancePlacement(inst, assignment))
    .filter(inst => inst.day !== undefined || inst._publicMoveSupportOnly);
  return { ...schedule, instances, rawInstances };
}

const DEFAULT_TIMES = ['08:30–10:00', '10:10–11:40', '11:50–13:20', '14:00–15:30', '15:40–17:10', '17:20–18:50', '19:00–20:30'];

function canonicalPublicInstId(value) {
  let id = String(value || '');
  const rx = /__(?:PUBW\d+(?:_\d+)*|EXACT_W\d+(?:_\d+)*|TRANSFER_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*|CANONICAL_RECOVER_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*|REMOTE_ONE_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)$/i;
  while (rx.test(id)) {
    id = id.replace(rx, '');
  }
  return id;
}

function matchSubWithLesson(sub, inst, dateISO) {
  if (!sub || !inst || !dateISO) return false;
  const matchesDate = String(sub.date) === String(dateISO) || String(sub.targetDate) === String(dateISO);
  if (!matchesDate) return false;
  const subIds = [sub.instId, sub.sourceInstId, sub.originalInstId]
    .map(canonicalPublicInstId)
    .filter(Boolean);
  if (!subIds.length) return false;
  const instIds = [inst.instId, inst._publishedSourceInstId, inst.id]
    .map(canonicalPublicInstId)
    .filter(Boolean);
  return instIds.some(id => subIds.includes(id));
}

function resolveParity(cfg, weekNum) {
  const num = Number(weekNum);
  if (!num) return null;
  return cfg?.weekParityOverrides?.[String(num)] || (num % 2 === 1 ? 'odd' : 'even');
}

function resolveGroupCustomTime(g, dayIndex, idx, parity) {
  if (!g || dayIndex === null || dayIndex === undefined) return '';
  const pCustom = parity ? g.dayPeriodTimesByParity?.[parity]?.[String(dayIndex)] : null;
  if (Array.isArray(pCustom) && pCustom[idx]?.trim()) return pCustom[idx];
  const dCustom = g.dayPeriodTimes?.[String(dayIndex)] || g.dayPeriodTimes?.[dayIndex];
  return (Array.isArray(dCustom) && dCustom[idx]?.trim()) ? dCustom[idx] : '';
}

export function useScheduleData() {
  const indexData = ref(null);
  const currentSchedule = ref({ instances: [] });
  const loading = ref(false);
  const error = ref(null);
  const selectedKind = ref('group');
  const selectedId = ref('');
  const selectedWeek = ref(10);
  const shardSubstitutions = ref([]);
  const shardLoads = ref([]);
  const manualSubstitutions = ref(null);

  const loads = computed(() => {
    const baseLoads = indexData.value?.data?.loads || [];
    return [...baseLoads, ...shardLoads.value];
  });

  const groups = computed(() => indexData.value?.data?.groups || []);
  const teachers = computed(() => indexData.value?.data?.teachers || []);
  const departments = computed(() => indexData.value?.data?.departments || []);
  const rooms = computed(() => indexData.value?.data?.rooms || []);
  const subjects = computed(() => indexData.value?.data?.subjects || []);
  const specialties = computed(() => indexData.value?.data?.specialties || []);
  const lessonTypes = computed(() => indexData.value?.data?.lessonTypes || []);
  const roomTypes = computed(() => indexData.value?.data?.roomTypes || []);
  const roomSizes = computed(() => indexData.value?.data?.roomSizes || []);
  const semesterConfig = computed(() => indexData.value?.data?.config || {});
  const announcements = computed(() => indexData.value?.data?.announcements || []);
  const publicIntroText = computed(() => indexData.value?.data?.publicIntroText || '');
  const retakes = computed(() => indexData.value?.data?.retakes || []);
  const practices = computed(() => indexData.value?.data?.practices || []);
  const publishedWeeks = computed(() => indexData.value?.data?.publishedWeeks || []);
  const publicWeekSelectionEnabled = computed(() => indexData.value?.data?.publicWeekSelectionEnabled !== false);

  const substitutions = computed({
    get: () => {
      if (manualSubstitutions.value !== null) return manualSubstitutions.value;
      const baseSubs = indexData.value?.data?.substitutions || [];
      return [...baseSubs, ...shardSubstitutions.value];
    },
    set: (val) => {
      manualSubstitutions.value = Array.isArray(val) ? val : [];
    }
  });

  async function loadPublicIndex() {
    loading.value = true;
    error.value = null;
    try {
      const res = await publicIndexGet();
      if (res?.paused) {
        error.value = res.message || 'Публичное расписание временно приостановлено';
        return;
      }
      if (res && res.data) {
        indexData.value = res;
        setDefaultSelection(res.data);
      }
    } catch {
      error.value = 'Не удалось загрузить данные расписания';
    } finally {
      loading.value = false;
    }
  }

  function setDefaultSelection(data) {
    const init = resolveInitialSelection(data);
    selectedKind.value = init.kind;
    selectedId.value = init.id;
    selectedWeek.value = init.week;
    saveSelection(init.kind, init.id, init.week);
    syncBrowserUrl(init.kind, init.id, init.week, data?.groups || []);
  }

  async function fetchEntityShard(kind, id, week) {
    const shard = await publicScheduleGet(kind, id, { week, scope: 'week' });
    if (shard?.schedule && (shard.schedule.instances || []).length) return shard;
    const semShard = await publicScheduleGet(kind, id, { scope: 'semester' });
    return semShard?.schedule ? semShard : shard;
  }

  async function loadScheduleShard(kind, id, week = selectedWeek.value) {
    if (!id) return;
    loading.value = true;
    try {
      const shard = await fetchEntityShard(kind, id, week);
      currentSchedule.value = normalizeScheduleData(shard?.schedule);
      shardSubstitutions.value = Array.isArray(shard?.dataPatch?.substitutions) ? shard.dataPatch.substitutions : [];
      shardLoads.value = Array.isArray(shard?.dataPatch?.loads) ? shard.dataPatch.loads : [];
    } catch (err) {
      console.error('Ошибка загрузки шарда расписания:', err);
    } finally {
      loading.value = false;
    }
  }

  // Быстрые O(1) хэш-индексы для устранения линейного сканирования при рендеринге списков
  const teacherMap = computed(() => {
    const m = new Map();
    teachers.value.forEach(t => { if (t?.id) m.set(String(t.id), t.name); });
    return m;
  });

  const subjectMap = computed(() => {
    const m = new Map();
    subjects.value.forEach(s => { if (s?.id) m.set(String(s.id), s.name); });
    return m;
  });

  const roomMap = computed(() => {
    const m = new Map();
    rooms.value.forEach(r => { if (r?.id) m.set(String(r.id), r); });
    return m;
  });

  const groupMap = computed(() => {
    const m = new Map();
    groups.value.forEach(g => { if (g?.id) m.set(String(g.id), g); });
    return m;
  });

  const lessonTypeMap = computed(() => {
    const m = new Map();
    lessonTypes.value.forEach(lt => { if (lt?.id) m.set(String(lt.id), lt.name); });
    return m;
  });

  const roomDetailsMap = computed(() => {
    const m = new Map();
    rooms.value.forEach(r => {
      if (r?.id) m.set(String(r.id), getRoomCategoryDetails(r, roomTypes.value, roomSizes.value));
    });
    return m;
  });

  const substitutionsByDate = computed(() => {
    const map = new Map();
    const add = (d, sub) => {
      if (!d) return;
      if (!map.has(d)) map.set(d, []);
      map.get(d).push(sub);
    };
    substitutions.value.forEach(sub => {
      add(String(sub.date || ''), sub);
      if (sub.targetDate && String(sub.targetDate) !== String(sub.date)) {
        add(String(sub.targetDate), sub);
      }
    });
    return map;
  });

  function getTeacherName(id) {
    if (!id) return 'Преподаватель не назначен';
    return teacherMap.value.get(String(id)) || 'Преподаватель не назначен';
  }

  function getSubjectName(id) {
    if (!id) return '—';
    return subjectMap.value.get(String(id)) || String(id);
  }

  function getRoomName(id) {
    if (!id) return '';
    const r = roomMap.value.get(String(id));
    return r ? (r.name || r.number || String(id)) : '';
  }

  function getRoomDetails(id) {
    if (!id) return { tagLabel: '', description: '' };
    return roomDetailsMap.value.get(String(id)) || { tagLabel: '', description: '' };
  }

  function getGroupName(id) {
    if (!id) return '';
    const g = groupMap.value.get(String(id));
    return g ? g.name : String(id);
  }

  function getLessonTypeName(id) {
    if (!id) return '';
    return lessonTypeMap.value.get(String(id)) || String(id);
  }

  function getRoomLocation(id, fallbackName = '') {
    const r = id ? roomMap.value.get(String(id)) : null;
    const name = String(r?.name || fallbackName || '').trim();
    const lower = name.toLowerCase();
    if (lower.includes('садов') || lower.startsWith('с-')) {
      return '↗ ул. Садовая, 54';
    }
    if (r?.isExternal || lower.startsWith('в-') || lower.includes('возн')) {
      return '↗ Вознесенский пр., 44';
    }
    return '';
  }

  function getPeriodTime(periodNumber, opts = {}) {
    const idx = Number(periodNumber) - 1;
    const { groupId, dayIndex, dateStr, roomId, weekNum } = opts;
    const cfg = semesterConfig.value || {};
    if (dateStr && (cfg.shortenedDates || []).includes(dateStr) && cfg.shortenedPeriodTimes?.[idx]) {
      return cfg.shortenedPeriodTimes[idx];
    }
    const g = groupId ? groupMap.value.get(String(groupId)) : null;
    const parity = resolveParity(cfg, weekNum || selectedWeek.value);
    const custom = resolveGroupCustomTime(g, dayIndex, idx, parity);
    if (custom) return custom;
    if (roomId && getRoomLocation(roomId).includes('Вознесенский') && cfg.voznesenskyPeriodTimes?.[idx]) {
      return cfg.voznesenskyPeriodTimes[idx];
    }
    return cfg.periodTimes?.[idx] || DEFAULT_TIMES[idx] || '';
  }

  function findSubstitutionForLesson(instance, dateISO) {
    if (instance?._isMovedSynthetic && instance?.substitution) {
      return instance.substitution;
    }
    const dateSubs = substitutionsByDate.value.get(String(dateISO)) || [];
    return dateSubs.find(sub => matchSubWithLesson(sub, instance, dateISO));
  }

  function updateSubstitutions(val) {
    manualSubstitutions.value = Array.isArray(val) ? val : [];
  }

  return {
    indexData,
    currentSchedule,
    loading,
    error,
    selectedKind,
    selectedId,
    selectedWeek,
    groups,
    teachers,
    departments,
    rooms,
    roomTypes,
    roomSizes,
    subjects,
    specialties,
    lessonTypes,
    loads,
    substitutions,
    announcements,
    publicIntroText,
    retakes,
    practices,
    publishedWeeks,
    publicWeekSelectionEnabled,
    semesterConfig,
    loadPublicIndex,
    loadScheduleShard,
    getTeacherName,
    getSubjectName,
    getRoomName,
    getRoomDetails,
    getRoomLocation,
    getGroupName,
    getLessonTypeName,
    getPeriodTime,
    findSubstitutionForLesson,
    updateSubstitutions,
    saveSelection,
    syncBrowserUrl
  };
}
