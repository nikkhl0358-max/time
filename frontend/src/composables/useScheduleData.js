/**
 * Управление данными расписания: загрузка публичного индекса,
 * шардов групп/преподавателей/кабинетов, замен и справочников.
 */
import { ref, computed } from 'vue';
import { publicIndexGet, publicScheduleGet } from '../storage.js';
import { resolveInitialSelection, saveSelection, syncBrowserUrl } from './useRouteSync.js';

function mapInstancePlacement(inst, assignment) {
  const a = assignment[inst.instId] || assignment[inst.id] || {};
  const day = a.day !== undefined ? Number(a.day) : (inst.day !== undefined ? Number(inst.day) : undefined);
  const period = a.period !== undefined ? Number(a.period) + 1 : (inst.period !== undefined ? Number(inst.period) : 1);
  const roomId = a.roomId || inst.dedicatedRoomId || inst.preferredRoomIds?.[0] || '';
  return { ...inst, day, period, roomId, extraRoomId: a.extraRoomId || null, assignment: a };
}

function normalizeScheduleData(schedule) {
  if (!schedule) return { instances: [], assignment: {} };
  const assignment = schedule.assignment || {};
  const rawInstances = Array.isArray(schedule.instances) ? schedule.instances : [];
  const instances = rawInstances
    .map(inst => mapInstancePlacement(inst, assignment))
    .filter(inst => inst.day !== undefined);
  return { ...schedule, instances, rawInstances };
}

function matchSubWithLesson(sub, inst, dayIndex, period) {
  if (sub.instId && inst.instId) {
    if (inst.instId.startsWith(sub.instId) || sub.instId.startsWith(inst.instId)) return true;
    if (inst._publishedSourceInstId && inst._publishedSourceInstId === sub.instId) return true;
  }
  const matchGroup = !sub.groupId || String(sub.groupId) === String(inst.groupId);
  const matchDay = Number(sub.day) === Number(dayIndex);
  const matchPeriod = Number(sub.period) === Number(period);
  return matchGroup && matchDay && matchPeriod;
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
  const manualSubstitutions = ref(null);

  const groups = computed(() => indexData.value?.data?.groups || []);
  const teachers = computed(() => indexData.value?.data?.teachers || []);
  const rooms = computed(() => indexData.value?.data?.rooms || []);
  const subjects = computed(() => indexData.value?.data?.subjects || []);
  const specialties = computed(() => indexData.value?.data?.specialties || []);
  const lessonTypes = computed(() => indexData.value?.data?.lessonTypes || []);
  const semesterConfig = computed(() => indexData.value?.data?.config || {});

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
    syncBrowserUrl(init.kind, init.id, init.week);
  }

  async function loadScheduleShard(kind, id, week = selectedWeek.value) {
    if (!id) return;
    loading.value = true;
    try {
      const shard = await publicScheduleGet(kind, id, { week, scope: 'week' });
      currentSchedule.value = normalizeScheduleData(shard?.schedule);
      shardSubstitutions.value = Array.isArray(shard?.dataPatch?.substitutions) ? shard.dataPatch.substitutions : [];
    } catch (err) {
      console.error('Ошибка загрузки шарда расписания:', err);
    } finally {
      loading.value = false;
    }
  }

  function getTeacherName(id) {
    if (!id) return 'Преподаватель не назначен';
    const t = teachers.value.find(item => String(item.id) === String(id));
    return t ? t.name : 'Преподаватель не назначен';
  }

  function getSubjectName(id) {
    if (!id) return '—';
    const s = subjects.value.find(item => String(item.id) === String(id));
    return s ? s.name : String(id);
  }

  function getRoomName(id) {
    if (!id) return '';
    const r = rooms.value.find(item => String(item.id) === String(id));
    return r ? (r.name || r.number || String(id)) : '';
  }

  function getGroupName(id) {
    if (!id) return '';
    const g = groups.value.find(item => String(item.id) === String(id));
    return g ? g.name : String(id);
  }

  function getLessonTypeName(id) {
    if (!id) return '';
    const lt = lessonTypes.value.find(item => String(item.id) === String(id));
    return lt ? lt.name : String(id);
  }

  function getRoomLocation(id, fallbackName = '') {
    const r = id ? rooms.value.find(item => String(item.id) === String(id)) : null;
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
    const { groupId, dayIndex } = opts;
    if (groupId && dayIndex !== null && dayIndex !== undefined) {
      const g = groups.value.find(item => String(item.id) === String(groupId));
      const custom = g?.dayPeriodTimes?.[String(dayIndex)] || g?.dayPeriodTimes?.[dayIndex];
      if (Array.isArray(custom) && custom[idx]) return custom[idx];
    }
    const times = semesterConfig.value?.periodTimes || [];
    if (times[idx]) return times[idx];
    const defaults = ['08:30–10:00', '10:10–11:40', '11:50–13:20', '14:00–15:30', '15:40–17:10', '17:20–18:50', '19:00–20:30'];
    return defaults[idx] || '';
  }

  function findSubstitutionForLesson(instance, dayIndex, period) {
    return substitutions.value.find(sub => matchSubWithLesson(sub, instance, dayIndex, period));
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
    rooms,
    subjects,
    specialties,
    lessonTypes,
    substitutions,
    semesterConfig,
    loadPublicIndex,
    loadScheduleShard,
    getTeacherName,
    getSubjectName,
    getRoomName,
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
