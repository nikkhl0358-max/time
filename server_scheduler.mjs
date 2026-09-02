const uid = () => Math.random().toString(36).slice(2, 10);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

const DAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const DAY_LABELS_FULL = [
  "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье",
];
const DEFAULT_VOZNESENSKY_PERIOD_TIMES = ["08:30–09:55", "10:05–11:30", "11:40–13:05", "13:45–15:10", "15:20–16:45", "16:55–18:20"];
const SUBGROUP_LABELS = { 0: "Вся группа", 1: "Подгруппа 1", 2: "Подгруппа 2", 3: "Подгруппа 3" };

function isoDate(d) {
  // Keep calendar dates in the browser's local timezone. Using toISOString()
  // shifts midnight to the previous day in positive UTC offsets (e.g. UTC+3),
  // which broke Monday-based week ranges and semester week numbering.
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
function todayISO() {
  return isoDate(new Date());
}
function weekdayOf(dateStr) {
  // Monday = 0 .. Sunday = 6
  const d = new Date(dateStr + "T00:00:00");
  return (d.getDay() + 6) % 7;
}
function addDays(dateStr, n) {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + n);
  return isoDate(d);
}
function mondayOf(dateStr) {
  return addDays(dateStr, -weekdayOf(dateStr));
}
const RU_MONTHS = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];
function formatRuDate(dateStr) {
  const d = new Date(dateStr + "T00:00:00");
  return `${d.getDate()} ${RU_MONTHS[d.getMonth()]}`;
}
function formatRuDateFull(dateStr) {
  const d = new Date(dateStr + "T00:00:00");
  return `${DAY_LABELS_FULL[weekdayOf(dateStr)]}, ${d.getDate()} ${RU_MONTHS[d.getMonth()]}`;
}
function formatDateDM(dateStr) {
  const d = new Date(dateStr + "T00:00:00");
  return `${String(d.getDate()).padStart(2,"0")}.${String(d.getMonth()+1).padStart(2,"0")}`;
}
function compressWeeklyDates(dates) {
  const sorted = [...new Set(dates)].sort();
  if (sorted.length === 0) return "";
  const groups = [];
  let start = sorted[0], prev = sorted[0], count = 1;
  for (let i=1;i<sorted.length;i++) {
    const diff = Math.round((new Date(sorted[i]+"T00:00:00") - new Date(prev+"T00:00:00"))/86400000);
    if (diff === 7) { prev=sorted[i]; count++; continue; }
    groups.push({start, end:prev, count}); start=prev=sorted[i]; count=1;
  }
  groups.push({start,end:prev,count});
  return groups.map((g)=>g.count===1 ? formatDateDM(g.start) : `${formatDateDM(g.start)}–${formatDateDM(g.end)} (${g.count})`).join("; ");
}
const slotOf = (day, period) => `${day}_${period}`;
function isTeacherUnavailable(teacher, day, period, week = null) {
  const key = slotOf(day, period);
  if (!teacher?.availabilityByParity) return !!teacher?.unavailable?.includes(key);
  if (week != null) {
    const list = Number(week) % 2 === 0 ? (teacher.unavailableEven || []) : (teacher.unavailableOdd || []);
    return list.includes(key);
  }
  // Без конкретной недели считаем слот полностью закрытым только если он закрыт
  // и на нечётных, и на чётных неделях.
  return (teacher.unavailableOdd || []).includes(key) && (teacher.unavailableEven || []).includes(key);
}
function isTeacherUnavailableForWeeks(teacher, day, period, weeks) {
  const list = Array.isArray(weeks) && weeks.length ? weeks : [null];
  return list.some((w) => isTeacherUnavailable(teacher, day, period, w));
}
function isStaffTeacher(teacher) {
  return (teacher?.employmentType || "staff") === "staff";
}
function weekNumberOf(semesterStart, dateStr) {
  if (!semesterStart) return null;
  const start = mondayOf(semesterStart);
  const diffDays = Math.round((new Date(dateStr + "T00:00:00") - new Date(start + "T00:00:00")) / 86400000);
  if (diffDays < 0) return null;
  return Math.floor(diffDays / 7) + 1; // 1-indexed
}
// v1603: `weekNumberOf` deliberately measures whole-week offsets from the
// MONDAY of the week containing `semesterStart` — so if the semester starts
// mid-week (e.g. Wednesday), Monday and Tuesday of that same calendar week
// still count as "week 1" for week-number purposes, even though their actual
// dates fall BEFORE the real semester start. Nothing previously stopped the
// scheduler from placing an instance on such a day: `slotFreeForOwners`
// checked occupancy/availability/format but never checked whether the
// resulting date was inside [semesterStart, semesterEnd] at all. That let a
// lesson get an `assignment` entry that never actually renders on any real
// calendar date — "фантомно размещённая" пара that shows as placed in stats
// but is invisible on the actual grid. This computes the concrete date for a
// given week+day and reports whether it's inside the configured semester.
function dateForWeekDay(config, week, day) {
  if (!config?.semesterStart) return null;
  return addDays(mondayOf(config.semesterStart), (Number(week) - 1) * 7 + Number(day));
}
function weekDayWithinSemester(config, week, day) {
  const date = dateForWeekDay(config, week, day);
  if (!date) return true; // no semesterStart configured: don't block placement over it
  if (date < config.semesterStart) return false;
  if (config.semesterEnd && date > config.semesterEnd) return false;
  return true;
}
function defaultParityForWeek(n) {
  return n % 2 === 1 ? "odd" : "even";
}
function weekParityOf(semesterStart, dateStr) {
  const n = weekNumberOf(semesterStart, dateStr);
  if (n === null) return null;
  return defaultParityForWeek(n);
}
function isShortenedDate(config, dateStr) {
  return !!dateStr && (config?.shortenedDates || []).includes(dateStr);
}
function periodTimeForDate(config, period, dateStr) {
  const shortTimes = config?.shortenedPeriodTimes || [];
  if (isShortenedDate(config, dateStr) && shortTimes[period]) return shortTimes[period];
  return config?.periodTimes?.[period] || "";
}
// v1557: у группы могут быть свои звонки по дню недели И отдельно по чётности недели.
// Базовый формат: group.dayPeriodTimes = { 0: ["08:30–09:55", ...] }.
// Переопределения: group.dayPeriodTimesByParity = { odd: {0:[...]}, even: {0:[...]} }.
// Для чётной/нечётной недели сначала берётся её переопределение; если его нет — базовый вариант дня.
// Сокращённая дата имеет приоритет над любыми групповыми звонками.
function groupBellParityForDate(data, dateStr = "", parityOverride = null) {
  if (parityOverride === "odd" || parityOverride === "even") return parityOverride;
  if (!dateStr) return null;
  return weekParityForDate(data?.config || {}, dateStr);
}
function groupPeriodTimesForDay(data, groupId, day, dateStr = "", parityOverride = null) {
  const group = (data?.groups || []).find((g) => g.id === groupId);
  const parity = groupBellParityForDate(data, dateStr, parityOverride);
  const parityMap = parity ? group?.dayPeriodTimesByParity?.[parity] : null;
  const parityCustom = parityMap?.[String(day)] || parityMap?.[day];
  if (Array.isArray(parityCustom) && parityCustom.some((x) => String(x || "").trim())) return parityCustom;
  const custom = group?.dayPeriodTimes?.[String(day)] || group?.dayPeriodTimes?.[day];
  return Array.isArray(custom) && custom.some((x) => String(x || "").trim()) ? custom : (data?.config?.periodTimes || []);
}
function periodTimeForGroupDay(data, groupId, day, period, dateStr = "", parityOverride = null) {
  const shortTimes = data?.config?.shortenedPeriodTimes || [];
  if (dateStr && isShortenedDate(data?.config, dateStr) && shortTimes[period]) return shortTimes[period];
  return groupPeriodTimesForDay(data, groupId, day, dateStr, parityOverride)?.[period] || data?.config?.periodTimes?.[period] || "";
}
function groupHasCustomBellDay(data, groupId, day, dateStr = "", parityOverride = null) {
  const actual = groupPeriodTimesForDay(data, groupId, day, dateStr, parityOverride) || [];
  const standard = data?.config?.periodTimes || [];
  const len = Math.max(actual.length, standard.length);
  for (let i = 0; i < len; i++) {
    if (String(actual[i] || "").trim() !== String(standard[i] || "").trim()) return true;
  }
  return false;
}

function totalSemesterWeeks(config) {
  if (!config.semesterStart || !config.semesterEnd) return 0;
  const n = weekNumberOf(config.semesterStart, mondayOf(config.semesterEnd));
  return n || 0;
}
function parityForWeekNumber(config, n) {
  if (!n) return null;
  const override = config.weekParityOverrides ? config.weekParityOverrides[n] : null;
  return override || defaultParityForWeek(n);
}
function weekParityForDate(config, dateStr) {
  const n = weekNumberOf(config.semesterStart, dateStr);
  if (n === null) return null;
  return parityForWeekNumber(config, n);
}
function isHoliday(config, dateStr) {
  return !!(config.holidays || []).includes(dateStr);
}
function calendarExceptionFor(data, dateStr) {
  return [...(data.calendarExceptions || []), ...(data.config?.calendarExceptions || [])].find((x) => x.date === dateStr) || null;
}
function isNoClassDate(data, dateStr) {
  return isHoliday(data.config || {}, dateStr) || !!calendarExceptionFor(data, dateStr)?.noClasses;
}
function splitTags(v) {
  return String(v || "").split(/[,;]+/).map((x) => x.trim().toLowerCase()).filter(Boolean);
}
function roomMeetsSubject(room, subject, requiredCapacity=0) {
  if (!room) return { ok:false, reason:"Аудитория не найдена" };
  if (subject?.requiresComputerRoom && !room.hasComputers) return { ok:false, reason:"нужна аудитория с компьютерами" };
  if (subject?.requiresArtRoom && !room.isArtRoom) return { ok:false, reason:"нужен художественный кабинет" };
  if (subject?.requiredRoomTypeId && room.typeId !== subject.requiredRoomTypeId) return { ok:false, reason:"не подходит тип аудитории" };
  // v1599: вместимость — мягкое предпочтение, а не запрет.
  // Фактическая посещаемость обычно ниже списочного состава, поэтому маленький
  // кабинет допускается; при выборе авто предпочтение всё равно отдаётся более
  // вместительным вариантам.
  const need = [];
  const have = new Set(splitTags(room.equipment));
  const missing = need.filter((x)=>!have.has(x));
  if (missing.length) return { ok:false, reason:`нет оборудования: ${missing.join(", ")}` };
  return { ok:true };
}
function weeksMatchingPattern(config, weekPattern, customWeeks) {
  if (weekPattern === "custom") return (customWeeks || []).length;
  const total = totalSemesterWeeks(config);
  if (total === 0) return null; // семестр не настроен — считать нечем
  if (weekPattern === "all") return total;
  let count = 0;
  for (let n = 1; n <= total; n++) {
    if (parityForWeekNumber(config, n) === weekPattern) count++;
  }
  return count;
}
function instanceAppliesToDate(config, inst, dateStr) {
  const pattern = inst.weekPattern || "all";
  if (pattern === "all") return true;
  if (pattern === "custom") {
    const n = weekNumberOf(config.semesterStart, dateStr);
    return n !== null && (inst.customWeeks || []).includes(n);
  }
  const parity = weekParityForDate(config, dateStr);
  return !parity || pattern === parity;
}
function weekPatternLabel(p) {
  if (p === "odd") return "числитель (нечёт.)";
  if (p === "even") return "знаменатель (чёт.)";
  if (p === "custom") return "по номерам недель";
  return "";
}
function weekNumbersForInstance(config, inst) {
  const total = totalSemesterWeeks(config);
  if (!total) return [];
  const pattern = inst?.weekPattern || "all";
  if (pattern === "custom") return (inst.customWeeks || []).filter((n) => n >= 1 && n <= total).slice().sort((a,b)=>a-b);
  if (pattern === "all") return Array.from({ length: total }, (_, i) => i + 1);
  return Array.from({ length: total }, (_, i) => i + 1).filter((n) => parityForWeekNumber(config, n) === pattern);
}
function compactWeekNumbers(nums = []) {
  const xs = [...new Set((nums || []).map(Number).filter((n) => Number.isFinite(n) && n > 0))].sort((a,b)=>a-b);
  if (!xs.length) return "";
  const parts = [];
  let start = xs[0], prev = xs[0];
  for (let i = 1; i <= xs.length; i++) {
    const cur = xs[i];
    if (cur === prev + 1) { prev = cur; continue; }
    parts.push(start === prev ? `${start}` : `${start}–${prev}`);
    start = cur; prev = cur;
  }
  return parts.join(", ");
}
function weekNumbersLabel(config, inst) {
  const nums = weekNumbersForInstance(config, inst);
  const compact = compactWeekNumbers(nums);
  return compact ? `недели: ${compact}` : "недели не назначены";
}


function ensureUniqueIds(list, label = "item") {
  const seen = new Set();
  return (list || []).map((item, idx) => {
    let id = String(item?.id || "").trim();
    if (!id || seen.has(id)) {
      const base = id || `${label}_${idx+1}`;
      let n = 2;
      let next = `${base}__${n}`;
      while (seen.has(next)) next = `${base}__${++n}`;
      id = next;
    }
    seen.add(id);
    return id === item?.id ? item : { ...item, id };
  });
}


// v1466: при добавлении второй подгруппы интерфейс нередко клонирует строку
// нагрузки до выдачи ей нового id. Если просто оставить id у "первой" строки
// массива, assignment уже существующей подгруппы может перескочить на клон.
// Сохраняем старый id именно у строки, которая семантически совпадает со
// старой нагрузкой, а новой подгруппе выдаём отдельный id.
function ensureUniqueLoadIdsStable(list, priorLoads = []) {
  const rows = (list || []).map((x) => ({ ...(x || {}) }));
  const priorById = new Map((priorLoads || []).filter((x) => x?.id).map((x) => [String(x.id), x]));
  const byId = new Map();
  rows.forEach((row, idx) => {
    const id = String(row?.id || "");
    if (!id) return;
    if (!byId.has(id)) byId.set(id, []);
    byId.get(id).push(idx);
  });
  const used = new Set(rows.map((r) => String(r?.id || "")).filter(Boolean));
  const semanticScore = (row, old) => {
    if (!old) return 0;
    let score = 0;
    if (row.groupId === old.groupId) score += 8;
    if (row.subjectId === old.subjectId) score += 8;
    if (row.typeId === old.typeId) score += 7;
    if (row.teacherId === old.teacherId) score += 6;
    if (Number(row.subgroup || 0) === Number(old.subgroup || 0)) score += 12;
    if ((row.format || "inperson") === (old.format || "inperson")) score += 2;
    return score;
  };
  for (const [base, idxs] of byId.entries()) {
    if (idxs.length < 2) continue;
    const old = priorById.get(base);
    let keep = idxs[0];
    if (old) keep = idxs.slice().sort((a,b) => semanticScore(rows[b], old) - semanticScore(rows[a], old))[0];
    let n = 1;
    for (const idx of idxs) {
      if (idx === keep) continue;
      let next;
      do { next = `${base}__sub${++n}`; } while (used.has(next));
      used.add(next);
      rows[idx] = { ...rows[idx], id: next };
    }
  }
  return ensureUniqueIds(rows, "load");
}

function normalizeScheduleInstanceIds(schedule) {
  if (!schedule || !Array.isArray(schedule.instances)) return schedule;
  const seen = new Map();
  const assignment = { ...(schedule.assignment || {}) };
  const locked = new Set(schedule.locked || []);
  const unplaced = new Set(schedule.unplaced || []);
  const instances = [];
  for (let idx = 0; idx < schedule.instances.length; idx++) {
    const src = schedule.instances[idx] || {};
    const base = String(src.instId || `inst_${idx+1}`);
    const count = (seen.get(base) || 0) + 1;
    seen.set(base, count);
    if (count === 1) { instances.push(src); continue; }
    let instId = `${base}__DUP${count}`;
    let suffix = count;
    while (instances.some((x)=>x.instId===instId)) instId = `${base}__DUP${++suffix}`;
    const migrated = { ...src, instId, duplicateOfInstId: base };
    instances.push(migrated);
    // Старый баг мог создать несколько экземпляров с одним instId. Они фактически
    // использовали одну запись assignment; копируем её, чтобы обе карточки были
    // видны и нормальная проверка конфликтов могла их различить.
    if (assignment[base] && !assignment[instId]) assignment[instId] = { ...assignment[base] };
    if (locked.has(base)) locked.add(instId);
    if (unplaced.has(base)) unplaced.add(instId);
  }
  return { ...schedule, instances, assignment, locked:[...locked], unplaced:[...unplaced] };
}

function graphPersistenceSignature(load) {
  const l = load || {};
  return [l.groupId||"", Number(l.subgroup||0), l.subjectId||"", l.teacherId||"", l.typeId||"", l.format||"inperson"].join("|");
}
function graphStateFromLoads(loads = []) {
  const out = {};
  for (const load of (loads || [])) {
    if (!load?.id) continue;
    const entry = {
      weeklyPairs: { ...(load.weeklyPairs || {}) },
      weekPattern: "perWeek",
      graphMode: load.graphMode || "manual",
      graphLocked: !!load.graphLocked,
    };
    out[String(load.id)] = entry;
    const sig = graphPersistenceSignature(load);
    if (sig) out[`sig:${sig}`] = entry;
  }
  return out;
}

function normalizeStoredData(raw) {
  const data = raw || {};
  const config = data.config || {};
  const total = totalSemesterWeeks(config);
  const normalizeLoad = (load) => {
    const baseRaw = { twoRooms: false, mandatoryRemoteAssemblies: 0, remotePercent: 0, ...load, customWeeks: [], weeklyPairs: { ...(load.weeklyPairs || {}) } };
    // v1589: «Комбинированно» имеет фиксированную долю ДО 50%.
    // Это меняет только параметр формата нагрузки; недельный график weeklyPairs не трогаем.
    const base = (baseRaw.format || "inperson") === "auto" && !!baseRaw.canBeRemote
      ? { ...baseRaw, remotePercent: 50, mandatoryRemoteAssemblies: 0 }
      : baseRaw;
    if ((load.weekPattern || "all") === "perWeek") return { ...base, weekPattern: "perWeek" };

    // Миграция старой модели: недели больше не живут в «Нагрузках».
    // Прежние all/odd/even/custom один раз переводим в понедельный график,
    // сохраняя фактическое распределение, после чего источником истины становится weeklyPairs.
    const pairs = Math.max(0, Number(load.pairsPerWeek) || 0);
    const weeks = [];
    if ((load.weekPattern || "all") === "custom") {
      weeks.push(...(load.customWeeks || []).filter((n) => Number(n) > 0));
    } else if (total > 0) {
      for (let n = 1; n <= total; n++) {
        if ((load.weekPattern || "all") === "all" || parityForWeekNumber(config, n) === load.weekPattern) weeks.push(n);
      }
    }
    const weeklyPairs = {};
    for (const n of weeks) if (pairs > 0) weeklyPairs[n] = pairs;
    return { ...base, weekPattern: "perWeek", weeklyPairs };
  };

  const migratedExamAssessments = (data.examSchedule || []).map((ex) => ({
    id: ex.id || uid(),
    academicYear: ex.academicYear || currentAcademicYearLabel(),
    groupId: ex.groupId || "",
    subjectId: ex.subjectId || "",
    teacherId: (ex.teacherIds || [])[0] || ex.teacherId || "",
    commissionTeacherIds: (ex.teacherIds || []).slice(1),
    roomId: ex.roomId || "",
    date: ex.date || todayISO(),
    period: Number(ex.period) || 0,
    kind: ex.kind || "экзамен",
    note: ex.note || "",
  }));
  const interimAssessments = (data.interimAssessments || []).map((a) => ({ commissionTeacherIds: [], ...a }));
  for (const ex of migratedExamAssessments) if (!interimAssessments.some((a) => a.id === ex.id)) interimAssessments.push(ex);
  const normalizedSchedule = normalizeScheduleInstanceIds(data.schedule);
  const persistedGraphState = (data.graphState && typeof data.graphState === "object") ? data.graphState : {};
  let normalizedLoadsBase = ensureUniqueIds((data.loads || []).map(normalizeLoad), "load");
  // v1520 migration: до разделения связок комбинированный/ДО поток хранился в
  // streamGroupIds, поэтому одна и та же связь одновременно показывалась как ОЧНО и ДО.
  // Если у строки явно есть дистанционная часть, переносим такой legacy-поток в
  // remoteSubgroupLoadIds и освобождаем очную связь. После этого ОЧНО и ДО независимы.
  normalizedLoadsBase = normalizedLoadsBase.map((load) => {
    const delivery = load.format || "inperson";
    const hasExplicitRemotePart = delivery === "remote" || (delivery === "auto" && !!load.canBeRemote && ((Number(load.remotePercent) || 0) > 0 || (Number(load.mandatoryRemoteAssemblies) || 0) > 0));
    if (!hasExplicitRemotePart || (load.remoteSubgroupLoadIds || []).length > 0 || !(load.streamGroupIds || []).length) return load;
    const wantedGroups = new Set(load.streamGroupIds || []);
    const remoteIds = normalizedLoadsBase.filter((row) => row.id !== load.id && wantedGroups.has(row.groupId) && row.subjectId === load.subjectId && row.teacherId === load.teacherId && row.typeId === load.typeId).map((row) => row.id);
    return remoteIds.length ? { ...load, remoteSubgroupLoadIds: remoteIds, streamGroupIds: [], streamId: "" } : load;
  });
  let recoveredGraphRows = 0;
  const normalizedLoads = normalizedLoadsBase.map((load) => {
    const persisted = persistedGraphState[String(load.id)] || persistedGraphState[`sig:${graphPersistenceSignature(load)}`];
    // v1491: сохранённый график живёт отдельно от объекта нагрузки. Даже если другая
    // страница/старый клиент перезаписал loads, недельная раскладка восстанавливается.
    if (persisted && typeof persisted === "object") {
      const loadWeeks = { ...(load.weeklyPairs || {}) };
      const persistedWeeks = { ...(persisted.weeklyPairs || {}) };
      // v1522: `loads[].weeklyPairs` is the live/canonical graph state. `graphState`
      // is only a recovery mirror for old/stale clients. If the load already carries
      // any explicit week values, never overwrite them from a possibly lagging mirror.
      // This fixes cells that appeared to "erase themselves" after autosave/rebase.
      // v1546: loads[].weeklyPairs is authoritative even when intentionally empty.
      // Recovery mirrors and schedule instances may never refill/overwrite a graph.
      return {
        ...load,
        weekPattern: "perWeek",
        customWeeks: [],
        weeklyPairs: loadWeeks,
        graphMode: load.graphMode || persisted.graphMode || "manual",
        graphLocked: load.graphLocked ?? persisted.graphLocked ?? false,
      };
    }
    // v1546: графики приоритетнее расписания. Даже пустой weeklyPairs может быть
    // осознанным результатом ручной правки, поэтому расписание не имеет права
    // восстанавливать/дописывать значения обратно в график.
    return load;
  });

  const normalized = {
    ...data,
    config: { ...config, dailyLoadRules: normalizedDailyLoadRules(config), peExtraPairAllowed: config.peExtraPairAllowed !== false },
    groups: (data.groups || []).map((g) => ({
      educationBase: "9",
      programSemesters: (g.educationBase || "9") === "11" ? 6 : 8,
      curriculumSemester: 1,
      subgroupCount: 2,
      profile: "",
      ...g,
      departmentId: undefined,
    })),
    teachers: (data.teachers || []).map((t) => ({ profile: "", employmentType: "staff", ...t, preferredRoomIds: Array.isArray(t.preferredRoomIds) ? t.preferredRoomIds : [] })),
    lessonTypes: (() => {
      const list = [...(data.lessonTypes || [])];
      const ensure = (name) => { if (!list.some((x) => String(x.name || "").trim().toLowerCase() === name.toLowerCase())) list.push({ id: uid(), name }); };
      ensure("лекция"); ensure("практика"); ensure("лабораторная"); ensure("контрольная работа"); ensure("зачёт"); ensure("дифференцированный зачёт");
      return list;
    })(),
    employmentTypes: data.employmentTypes?.length ? data.employmentTypes : [
      { id: "staff", name: "штатный" }, { id: "partTime", name: "совместитель" }, { id: "contract", name: "ГПХ" },
    ],
    rooms: (data.rooms || []).map((r) => ({ hasProjector: false, ...r })),
    curriculumPlan: (data.curriculumPlan || []).map((p) => ({ educationBase: "9", ...p })),
    loads: normalizedLoads,
    graphState: Object.keys(persistedGraphState).length ? persistedGraphState : graphStateFromLoads(normalizedLoads),
    graphRecoveredRows: recoveredGraphRows,
    attestationTypes: data.attestationTypes?.length ? data.attestationTypes : ["зачёт", "экзамен", "курсовая работа", "дифф. зачёт"],
    practices: (data.practices || []).map((p) => ({ name: "", roomId: "", ...p, roomBookings: Array.isArray(p.roomBookings) ? p.roomBookings : [] })),
    calendarExceptions: data.calendarExceptions || [],
    groupDayBlocks: Array.isArray(data.groupDayBlocks) ? data.groupDayBlocks : [], // {id,groupId,date,addedLockedIds[]}
    groupSlotBlocks: Array.isArray(data.groupSlotBlocks) ? data.groupSlotBlocks : [], // {id,groupId,date,period} — запрет только для авто
    groupDayFreezes: Array.isArray(data.groupDayFreezes) ? data.groupDayFreezes : [], // {id,groupId,date,addedLockedIds[]}
    groupScheduleFreezes: Array.isArray(data.groupScheduleFreezes) ? data.groupScheduleFreezes : [], // полностью зафиксированные группы
    interimAssessments,
    examSchedule: [],
    schedule: normalizedSchedule,
    scheduleVersions: data.scheduleVersions || [],
    absenceScenarios: data.absenceScenarios || [],
    changeHistory: data.changeHistory || [],
    publicIntroText: data.publicIntroText || "",
    announcements: Array.isArray(data.announcements) ? data.announcements : [],
    consultations: Array.isArray(data.consultations) ? data.consultations : [],
    publishedSnapshot: data.publishedSnapshot || null,
    publishedAt: data.publishedAt || "",
    publishedBy: data.publishedBy || "",
  };

  // v1550: графики и расписание обязаны сходиться уже при загрузке данных.
  // Раньше после сохранения/перезагрузки мог остаться старый schedule.instances:
  // график уже показывал новые недели (например, чётные), а «Не размещено» —
  // старые нечётные недели и не содержал позднее добавленные практические строки.
  // Перестраиваем ТОЛЬКО структуру instances/unplaced из актуального weeklyPairs.
  // Существующие assignment сохраняются syncScheduleInstancesToGraph без переноса.
  if (normalized.schedule && Array.isArray(normalized.schedule.instances)) {
    normalized.schedule = syncScheduleInstancesToGraph(normalized, normalized.loads);
  }
  return normalized;
}
function weeksOverlap(instA, instB, config = null) {
  // v145.5: конфликт существует только если у двух занятий реально есть общая
  // неделя. Особенно важно для ручной расстановки: custom [1,3,5] не должен
  // конфликтовать с even [2,4,6]. Раньше custom против odd/even всегда считался
  // пересечением «на всякий случай», из-за чего система запрещала поставить
  // нечётную пару в слот, занятый только по чётным неделям.
  if (config) {
    const wa = weekNumbersForInstance(config, instA);
    const wb = new Set(weekNumbersForInstance(config, instB));
    return wa.some((w) => wb.has(w));
  }
  const pa = instA?.weekPattern || "all", pb = instB?.weekPattern || "all";
  if (pa === "all" || pb === "all") return true;
  if (pa === "custom" || pb === "custom") {
    if (pa === "custom" && pb === "custom") {
      const wa = instA.customWeeks || [], wb = new Set(instB.customWeeks || []);
      return wa.some((w) => wb.has(w));
    }
    // Без config всё равно можем корректно различить стандартный чёт/нечёт.
    const custom = pa === "custom" ? instA : instB;
    const parity = pa === "custom" ? pb : pa;
    return (custom.customWeeks || []).some((w) => defaultParityForWeek(Number(w)) === parity);
  }
  return pa === pb;
}
function subgroupOverlap(a, b) {
  const sa = a || 0, sb = b || 0;
  return sa === 0 || sb === 0 || sa === sb;
}

async function parseSpreadsheetFile(file) {
  const ext = file.name.split(".").pop().toLowerCase();
  if (ext === "csv") {
    const text = await file.text();
    const result = Papa.parse(text, { header: true, skipEmptyLines: true });
    return result.data;
  }
  const buf = await file.arrayBuffer();
  const wb = XLSX.read(buf, { type: "array" });
  const sheet = wb.Sheets[wb.SheetNames[0]];
  return XLSX.utils.sheet_to_json(sheet, { defval: "" });
}

function pickField(row, candidates) {
  const keys = Object.keys(row);
  for (const key of keys) {
    const lk = key.trim().toLowerCase();
    if (candidates.includes(lk)) {
      const v = String(row[key] ?? "").trim();
      if (v) return v;
    }
  }
  for (const key of keys) {
    const lk = key.trim().toLowerCase();
    if (candidates.some((c) => lk.includes(c))) {
      const v = String(row[key] ?? "").trim();
      if (v) return v;
    }
  }
  return "";
}
function findByName(list, name) {
  if (!name) return null;
  return list.find((x) => x.name.trim().toLowerCase() === name.trim().toLowerCase()) || null;
}

function findOrCreateByName(list, name) {
  if (!name) return "";
  const existing = list.find((x) => x.name.trim().toLowerCase() === name.trim().toLowerCase());
  if (existing) return { id: existing.id, created: null };
  const created = { id: uid(), name: name.trim() };
  return { id: created.id, created };
}

function downloadTemplate(filename, headers, exampleRows) {
  const ws = XLSX.utils.aoa_to_sheet([headers, ...exampleRows]);
  ws["!cols"] = headers.map((h) => ({ wch: Math.max(14, h.length + 2) }));
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Шаблон");
  XLSX.writeFile(wb, filename);
}

function downloadRowsXlsx(filename, sheetName, headers, rows) {
  const ws = XLSX.utils.aoa_to_sheet([headers, ...rows]);
  ws["!cols"] = headers.map((h, i) => ({ wch: Math.max(12, Math.min(36, Math.max(h.length + 2, ...rows.slice(0, 100).map((r) => String(r[i] ?? "").length + 2)))) }));
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, sheetName || "Данные");
  XLSX.writeFile(wb, filename);
}
function boolFromCell(v) {
  return /^(1|да|yes|true|есть|\+|x)$/i.test(String(v || "").trim());
}
function assignmentRoomIds(a) {
  // v1508: текущие поля roomId/extraRoomId — единственный источник истины,
  // если они присутствуют в assignment. После атомарных merge в старых данных
  // могли оставаться legacy roomIds/additionalRoomIds со старыми кабинетами.
  // Из-за их объединения одно занятие начинало «занимать» сразу много/все комнаты.
  // Legacy-массивы читаем ТОЛЬКО для действительно старой записи, где современных
  // полей вообще нет. Это сохраняет совместимость импорта, но убирает фантомную занятость.
  if (a && (Object.prototype.hasOwnProperty.call(a, "roomId") || Object.prototype.hasOwnProperty.call(a, "extraRoomId"))) {
    return [...new Set([a?.roomId, a?.extraRoomId].filter(Boolean))];
  }
  const legacy = Array.isArray(a?.roomIds) ? a.roomIds.map((x) => typeof x === "string" ? x : x?.roomId || x?.id) : [];
  const additional = Array.isArray(a?.additionalRoomIds)
    ? a.additionalRoomIds.map((x) => typeof x === "string" ? x : x?.roomId || x?.id)
    : [];
  return [...new Set([...legacy, ...additional].filter(Boolean))];
}
function assignmentRoomLabel(data, a) {
  return assignmentRoomIds(a).map((id) => data.rooms.find((r) => r.id === id)?.name).filter(Boolean).join(" + ");
}
function assignmentUsesExternalRoom(data, a) {
  return assignmentRoomIds(a).some((id) => !!data.rooms.find((r) => r.id === id)?.isExternal);
}
function scheduleSpecialClass(data, a, groupId, day, dateStr = "", parityOverride = null) {
  const external = assignmentUsesExternalRoom(data, a);
  const shifted = !!groupId && groupHasCustomBellDay(data, groupId, day, dateStr, parityOverride);
  return `${external ? " external-room-chip" : ""}${shifted ? " shifted-bells-chip" : ""}`;
}
// v1530: индекс ДО-связок строится один раз на конкретный массив loads.
// В v1529 linkedRemoteLoadRows на КАЖДЫЙ вызов повторно проходил все нагрузки,
// а в расписании/графиках этот helper вызывается сотни и тысячи раз.
// На больших данных это заметно тормозило первый рендер.
const remoteLinkIndexCache = new WeakMap();
function remoteLinkIndex(data) {
  const loads = data?.loads || [];
  if (!Array.isArray(loads)) return { byLoadId: new Map(), reverse: new Map(), clusters: new Map() };
  const cached = remoteLinkIndexCache.get(loads);
  if (cached) return cached;
  const byLoadId = new Map();
  const reverse = new Map();
  for (const row of loads) {
    if (!row?.id) continue;
    byLoadId.set(row.id, row);
    for (const ref of (row.remoteSubgroupLoadIds || [])) {
      if (!ref) continue;
      if (!reverse.has(ref)) reverse.set(ref, []);
      reverse.get(ref).push(row.id);
    }
  }
  const index = { byLoadId, reverse, clusters: new Map() };
  remoteLinkIndexCache.set(loads, index);
  return index;
}
function linkedRemoteLoadRows(data, load) {
  if (!load?.id) return [];
  const index = remoteLinkIndex(data);
  if (index.clusters.has(load.id)) return index.clusters.get(load.id);
  const seen = new Set();
  const queue = [load.id];
  while (queue.length) {
    const id = queue.shift();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const row = index.byLoadId.get(id);
    if (row) for (const ref of (row.remoteSubgroupLoadIds || [])) if (!seen.has(ref)) queue.push(ref);
    for (const sourceId of (index.reverse.get(id) || [])) if (!seen.has(sourceId)) queue.push(sourceId);
  }
  const rows = [...seen].map((id) => index.byLoadId.get(id)).filter(Boolean);
  // Компоненты одной связной ДО-связки всегда имеют один и тот же кластер.
  for (const row of rows) if (row?.id) index.clusters.set(row.id, rows);
  return rows;
}
function linkedInPersonLoadRows(data, load) {
  if (!load) return [];
  const loads = data?.loads || [];
  if (load.streamId) return loads.filter((row) => row.streamId === load.streamId);
  const wanted = new Set([load.groupId, ...(load.streamGroupIds || [])].filter(Boolean));
  return loads.filter((row) => wanted.has(row.groupId) && row.subjectId === load.subjectId && row.teacherId === load.teacherId && row.typeId === load.typeId && Number(row.subgroup || 0) === Number(load.subgroup || 0));
}
function enrichInstanceStreamGroups(data, inst) {
  if (!inst) return inst;
  const load = (data?.loads || []).find((l) => l.id === inst.loadId);
  if (!load) return inst;
  // v1529: очная и ДО-связки — разные контуры. Для конкретного occurrence
  // добавляем только тот состав, который относится к его фактическому формату.
  const rows = inst.format === "remote" ? linkedRemoteLoadRows(data, load) : linkedInPersonLoadRows(data, load);
  const ids = new Set([inst.groupId, ...(inst.groupIds || []), ...(inst.streamGroupIds || []), load.groupId].filter(Boolean));
  const participants = [...(Array.isArray(inst.streamParticipants) ? inst.streamParticipants : [])];
  for (const row of rows) {
    if (row?.groupId) ids.add(row.groupId);
    if (row?.groupId && row.id !== load.id && !participants.some((p) => p.loadId === row.id || (p.groupId === row.groupId && Number(p.subgroup || 0) === Number(row.subgroup || 0)))) {
      participants.push({ groupId: row.groupId, subgroup: Number(row.subgroup) || 0, loadId: row.id });
    }
  }
  const primary = inst.groupId || load.groupId;
  return { ...inst, streamGroupIds: [...ids].filter((gid) => gid !== primary), streamParticipants: participants.filter((p) => p.groupId !== primary || Number(p.subgroup || 0) !== Number(inst.subgroup || 0)) };
}
function instanceGroupNames(data, inst) {
  const enriched = enrichInstanceStreamGroups(data, inst);
  const ids = [...new Set([enriched?.groupId, ...(enriched?.groupIds || []), ...(enriched?.streamGroupIds || [])].filter(Boolean))];
  return ids.map((id) => data.groups.find((g) => g.id === id)?.name).filter(Boolean);
}

async function sha256Hex(text) {
  const enc = new TextEncoder().encode(text);
  const buf = await crypto.subtle.digest("SHA-256", enc);
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

function buildSeed() {
  const dep1 = uid(), dep2 = uid();
  const sp1 = uid();
  const lt1 = uid(), lt2 = uid(), lt3 = uid();
  const rt1 = uid(), rt2 = uid(), rt3 = uid(), rt4 = uid();
  const rs1 = uid(), rs2 = uid(), rs3 = uid();
  const g1 = uid(), g2 = uid();
  const t1 = uid(), t2 = uid(), t3 = uid();
  const r1 = uid(), r2 = uid(), r3 = uid();
  const s1 = uid(), s2 = uid(), s3 = uid();
  return {
    config: {
      activeDays: [true, true, true, true, true, false, false],
      periodsPerDay: 6,
      periodTimes: [
        "08:00–09:30", "09:40–11:10", "11:30–13:00",
        "13:30–15:00", "15:10–16:40", "16:50–18:20",
      ],
      voznesenskyPeriodTimes: [...DEFAULT_VOZNESENSKY_PERIOD_TIMES],
      shortenedPeriodTimes: [
        "08:00–09:10", "09:20–10:30", "10:40–11:50",
        "12:00–13:10", "13:20–14:30", "14:40–15:50",
      ],
      shortenedDates: [], // ISO даты сокращённых учебных дней
      semesterStart: "",
      semesterEnd: "",
      weekParityOverrides: {}, // { [weekNumber]: 'odd' | 'even' }
      holidays: [], // ISO dates
      calendarExceptions: [], // legacy placeholder
      remoteQuotaPercent: null, // целевой максимум доли ДО от всех пар, если задан
      minRemoteDaysPerWeek: 0, // минимум разных ДО-дней для группы в учебной неделе; 0 = не ограничивать
      dailyLoadRules: DEFAULT_DAILY_LOAD_RULES.map((r) => ({ ...r })),
      peExtraPairAllowed: true, // физкультура может увеличить дневной максимум на 1, но никогда до 6
    },
    departments: [
      { id: dep1, name: "ЦК рекламы и дизайна" },
      { id: dep2, name: "ЦК гуманитарных дисциплин" },
    ],
    specialties: [{ id: sp1, name: "42.02.01 Реклама" }],
    lessonTypes: [
      { id: lt1, name: "лекция" },
      { id: lt2, name: "практика" },
      { id: lt3, name: "лабораторная" },
    ],
    employmentTypes: [
      { id: "staff", name: "штатный" },
      { id: "partTime", name: "совместитель" },
      { id: "contract", name: "ГПХ" },
    ],
    roomTypes: [
      { id: rt1, name: "обычная" },
      { id: rt2, name: "лекционная" },
      { id: rt3, name: "компьютерная" },
      { id: rt4, name: "лаборатория" },
    ],
    roomSizes: [
      { id: rs1, name: "малая (до 15 мест)" },
      { id: rs2, name: "средняя (до 30 мест)" },
      { id: rs3, name: "большая (свыше 30 мест)" },
    ],
    groups: [
      { id: g1, name: "2-РД-1", students: 25, specialtyId: sp1, admissionYear: 2024, educationBase: "9", programSemesters: 8, curriculumSemester: 3, shift: 1, dayOff: 6, preferInPerson: false, profile: "" },
      { id: g2, name: "2-РД-2", students: 23, specialtyId: sp1, admissionYear: 2024, educationBase: "9", programSemesters: 8, curriculumSemester: 3, shift: 1, dayOff: 6, preferInPerson: false, profile: "" },
    ],
    teachers: [
      { id: t1, name: "Иванова А.П.", departmentId: dep1, unavailable: [], employmentType: "staff", profile: "" },
      { id: t2, name: "Петров С.М.", departmentId: dep1, unavailable: [], employmentType: "staff", profile: "" },
      { id: t3, name: "Сидорова Е.В.", departmentId: dep2, unavailable: [], employmentType: "staff", profile: "" },
    ],
    rooms: [
      { id: r1, name: "301", typeId: rt1, sizeId: rs2, hasComputers: false, isArtRoom: false, capacity: 30, isExternal: false, hasProjector: false },
      { id: r2, name: "404 (комп.)", typeId: rt3, sizeId: rs2, hasComputers: true, isArtRoom: false, capacity: 15, isExternal: false, hasProjector: false },
      { id: r3, name: "Актовый зал", typeId: rt2, sizeId: rs3, hasComputers: false, isArtRoom: false, capacity: 80, isExternal: false, hasProjector: false },
    ],
    subjects: [
      { id: s1, name: "Реклама и PR", requiresComputerRoom: false, requiresArtRoom: false, departmentId: dep1, cycle: "Общепрофессиональные" },
      { id: s2, name: "Компьютерная графика", requiresComputerRoom: true, requiresArtRoom: false, departmentId: dep1, cycle: "Общепрофессиональные" },
      { id: s3, name: "Маркетинг", requiresComputerRoom: false, requiresArtRoom: false, departmentId: dep2, cycle: "Общепрофессиональные" },
    ],
    loads: [
      { id: uid(), groupId: g1, teacherId: t1, subjectId: s1, typeId: lt1, roomType: "любая", pairsPerWeek: 2, subgroup: 0, weekPattern: "all" },
      { id: uid(), groupId: g1, teacherId: t2, subjectId: s2, typeId: lt2, roomType: rt3, pairsPerWeek: 2, subgroup: 1, weekPattern: "all" },
      { id: uid(), groupId: g2, teacherId: t1, subjectId: s1, typeId: lt1, roomType: "любая", pairsPerWeek: 2, subgroup: 0, weekPattern: "all" },
      { id: uid(), groupId: g2, teacherId: t3, subjectId: s3, typeId: lt2, roomType: "любая", pairsPerWeek: 0, subgroup: 0, weekPattern: "all" },
    ],
    curriculumPlan: [
      { id: uid(), specialtyId: sp1, educationBase: "9", semesterNumber: 3, subjectId: s1, lessonTypeId: lt1, totalHours: 34 },
      { id: uid(), specialtyId: sp1, educationBase: "9", semesterNumber: 3, subjectId: s2, lessonTypeId: lt2, totalHours: 48 },
      { id: uid(), specialtyId: sp1, educationBase: "9", semesterNumber: 3, subjectId: s3, lessonTypeId: lt2, totalHours: 26 },
    ],
    schedule: null, // { assignment, unplaced, stats, instances, locked }
    substitutions: [], // { id, date, instId, type: 'sub'|'cancel', newTeacherId, newRoomId, note }
    retakes: [], // { id, date, period, groupId, subjectId, teacherId, roomId, note }
    interimAssessments: [], // { id, academicYear, groupId, subjectId, teacherId, date, period, roomId, kind, note }
    attestationTypes: ["зачёт", "экзамен", "курсовая работа", "дифф. зачёт"],
    practices: [], // { id, groupId, kind, name, teacherId, dateStart, dateEnd, note, roomBookings:[{id,date,period,roomId}] }
    calendarExceptions: [], // {id,date,kind,name,noClasses}
    groupDayBlocks: [], // временные запреты занятий конкретной группе на конкретную дату
    groupSlotBlocks: [], // запреты авторасстановки конкретной группе на дату + номер пары
    groupDayFreezes: [], // замороженные дни: авто не меняет состав/слоты дня
    groupScheduleFreezes: [], // полностью зафиксированные расписания групп
    examSchedule: [], // {id,kind,groupId,subjectId,teacherIds,date,period,durationPeriods,roomId,note}
    scheduleVersions: [], // published/restore points
    absenceScenarios: [],
    hoursPlan: [], // { id, groupId, subjectId, totalHours } — устарело, не используется (см. curriculumPlan)
    users: [], // { id, name, login, passwordHash, role: 'superadmin' | 'admin' }
    publicIntroText: "",
    announcements: [], // { id, text, date }
    consultations: [], // { id, teacherId, day, startTime, endTime, note } — еженедельно весь семестр
    changeHistory: [], // { id, at, user, section, action, details }
    publishedSnapshot: null, // публичный снимок; изменения черновика не видны до публикации
    publishedAt: "",
    publishedBy: "",
  };
}

/* ============================ occupancy / validation ============================ */

function isPracticalLessonTypeName(name) {
  const n = String(name || "").trim().toLowerCase();
  return n === "пр" || n.startsWith("пр ") || n.startsWith("пр.") || n.startsWith("практич");
}
function isLectureLessonTypeName(name) {
  const n = String(name || "").trim().toLowerCase();
  return n === "лек" || n.startsWith("лек ") || n.startsWith("лек.") || n.startsWith("лекц");
}
function isControlLessonTypeName(name) {
  const n = String(name || "").trim().toLowerCase();
  return n.includes("контрольн") || n.includes("контрработ") || n.includes("контр работа") || n.includes("контр. раб") || n === "кр" || n.startsWith("кр ") || n.startsWith("кр.");
}
function isCreditLessonTypeName(name) {
  const n = String(name || "").trim().toLowerCase();
  return n === "зчо" || n.startsWith("зчо ") || n.startsWith("зчо.") || n.includes("зачет") || n.includes("зачёт") || n.includes("зчо") || n.includes("дифф") && (n.includes("зач") || n.includes("аттест"));
}
function isLaboratoryLessonTypeName(name) {
  const n = String(name || "").trim().toLowerCase();
  return n.startsWith("лаб") || n.includes("лаборатор");
}
function groupCourseNumber(group) {
  const sem = Number(group?.curriculumSemester || 0);
  if (sem > 0) return Math.ceil(sem / 2);
  const m = String(group?.name || "").match(/^(\d+)/);
  return m ? Number(m[1]) : 0;
}
// Эквивалентный этап обучения для потоков: группа после 11 класса
// идёт на один учебный год впереди группы после 9 класса. Поэтому
// 1 курс после 11 можно объединять с 2 курсом после 9, 2 с 3 и т.д.
function streamEducationBase(group) {
  // Для старых записей групп база могла сохраниться как 9 кл. по умолчанию,
  // хотя срок программы уже указывает на набор после 11 класса. Для потоков
  // считаем 6-семестровую программу базой 11 кл., чтобы 1 курс (11 кл.)
  // корректно совпадал со 2 курсом (9 кл.).
  if (String(group?.educationBase || "") === "11") return "11";
  const semesters = Number(group?.programSemesters || 0);
  if (semesters > 0 && semesters <= 6) return "11";
  return "9";
}
function streamStudyStage(group) {
  const course = groupCourseNumber(group);
  if (!course) return 0;
  return course + (streamEducationBase(group) === "11" ? 1 : 0);
}
function sameStreamStudyStage(a, b) {
  // v1528: направление/специальность не является ограничением для потока.
  // Если дисциплина (и для обычного потока преподаватель/вид занятия) совпадает,
  // группы разных направлений можно объединить на одном эквивалентном этапе обучения.
  return !!a && !!b && streamStudyStage(a) === streamStudyStage(b);
}

function defaultStudentsForGroupSubgroup(group, subgroup) {
  const total = Math.max(0, Number(group?.students) || 0);
  const sg = Number(subgroup) || 0;
  if (!sg) return total;
  const count = Math.max(1, Number(group?.subgroupCount) || 2);
  if (count === 2) return sg === 1 ? Math.ceil(total / 2) : Math.floor(total / 2);
  const base = Math.floor(total / count);
  const extra = total % count;
  return base + (sg <= extra ? 1 : 0);
}
function defaultStudentsForLoadGroups(groupById, groupIds, subgroup) {
  return (groupIds || []).reduce((sum, gid) => sum + defaultStudentsForGroupSubgroup(groupById[gid], subgroup), 0);
}

const DEFAULT_DAILY_LOAD_RULES = [
  { course: 1, educationBase: "9",  minPairs: 2, maxPairs: 3, minRemoteDays: 0 },
  { course: 1, educationBase: "11", minPairs: 2, maxPairs: 4, minRemoteDays: 0 },
  { course: 2, educationBase: "9",  minPairs: 2, maxPairs: 4, minRemoteDays: 0 },
  { course: 2, educationBase: "11", minPairs: 2, maxPairs: 4, minRemoteDays: 0 },
  { course: 3, educationBase: "9",  minPairs: 2, maxPairs: 5, minRemoteDays: 0 },
  { course: 3, educationBase: "11", minPairs: 2, maxPairs: 5, minRemoteDays: 0 },
  { course: 4, educationBase: "9",  minPairs: 2, maxPairs: 5, minRemoteDays: 0 },
  { course: 4, educationBase: "11", minPairs: 2, maxPairs: 5, minRemoteDays: 0 },
];
function normalizedDailyLoadRules(config) {
  const source = Array.isArray(config?.dailyLoadRules) ? config.dailyLoadRules : [];
  return DEFAULT_DAILY_LOAD_RULES.map((def) => {
    const saved = source.find((r) => Number(r.course) === def.course && String(r.educationBase) === def.educationBase) || {};
    const minPairs = clamp(Math.round(Number(saved.minPairs ?? def.minPairs) || def.minPairs), 1, 5);
    const maxPairs = clamp(Math.round(Number(saved.maxPairs ?? def.maxPairs) || def.maxPairs), minPairs, 5);
    const legacyRemoteDays = Number(config?.minRemoteDaysPerWeek);
    const minRemoteDays = clamp(Math.round(Number(saved.minRemoteDays ?? (Number.isFinite(legacyRemoteDays) ? legacyRemoteDays : def.minRemoteDays)) || 0), 0, 7);
    // Дневная нагрузка и желаемое число ДО-дней задаются отдельно для курса и базы поступления.
    // legacy maxPeriod и общий minRemoteDaysPerWeek сохраняются только для обратной совместимости.
    return { ...def, ...saved, course: def.course, educationBase: def.educationBase, minPairs, maxPairs, minRemoteDays };
  });
}
function dailyLoadRuleForGroup(config, group) {
  const course = groupCourseNumber(group);
  const educationBase = String(group?.educationBase || "9");
  return normalizedDailyLoadRules(config).find((r) => r.course === course && r.educationBase === educationBase) ||
    { course, educationBase, minPairs: 2, maxPairs: 5, minRemoteDays: 0 };
}
function isPhysicalEducationName(name) {
  const n = String(name || "").trim().toLowerCase().replace(/ё/g, "е");
  return n.includes("физическ") && n.includes("культур") || n === "физкультура" || n.includes("физ. культура");
}

function streamParticipants(inst) {
  const explicit = Array.isArray(inst.streamParticipants) ? inst.streamParticipants : [];
  const base = [{ groupId: inst.groupId, subgroup: Number(inst.subgroup) || 0, loadId: inst.loadId || "" }, ...explicit]
    .filter((p) => p?.groupId);
  if (base.length > 1 || explicit.length) {
    const seen = new Set();
    return base.filter((p) => {
      const key = `${p.groupId}|${Number(p.subgroup) || 0}|${p.loadId || ""}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }
  return [{ groupId: inst.groupId, subgroup: Number(inst.subgroup) || 0, loadId: inst.loadId || "" },
    ...((inst.streamGroupIds || []).filter(Boolean).map((groupId) => ({ groupId, subgroup: Number(inst.subgroup) || 0, loadId: "" })))];
}
function streamGroups(inst) {
  return [...new Set(streamParticipants(inst).map((p) => p.groupId).filter(Boolean))];
}
function subgroupsForGroup(inst, groupId) {
  return streamParticipants(inst).filter((p) => p.groupId === groupId).map((p) => Number(p.subgroup) || 0);
}
function participantSubgroupsOverlap(a, b, groupId) {
  const aa = subgroupsForGroup(a, groupId);
  const bb = subgroupsForGroup(b, groupId);
  return aa.some((sa) => bb.some((sb) => subgroupOverlap(sa, sb)));
}
// Авторитетная нормализация состава группы для диагностик.
// В старых потоковых экземплярах мог сохраниться синтетический subgroup=0,
// хотя реальная строка нагрузки относится только к п/г 1 или п/г 2.
// Источник истины — loadId -> loads; metadata экземпляра используется только как fallback.
function normalizedParticipantSubgroups(data, inst, groupId) {
  const loadsById = new Map((data?.loads || []).map((l) => [l.id, l]));
  const authoritative = [];
  const fallback = [];
  for (const part of streamParticipants(inst || {})) {
    if (part.groupId !== groupId) continue;
    const load = part.loadId ? loadsById.get(part.loadId) : null;
    if (load && load.groupId === groupId) authoritative.push(Number(load.subgroup) || 0);
    else fallback.push(Number(part.subgroup) || 0);
  }
  if (inst?.groupId === groupId && inst?.loadId) {
    const load = loadsById.get(inst.loadId);
    if (load && load.groupId === groupId) authoritative.push(Number(load.subgroup) || 0);
  }
  if (authoritative.length) return [...new Set(authoritative)];
  if (inst?.groupId === groupId) fallback.push(Number(inst.subgroup) || 0);
  const concrete = [...new Set(fallback.filter((x) => x > 0))];
  return concrete.length ? concrete : [...new Set(fallback.length ? fallback : [0])];
}
function normalizedParticipantSubgroupsOverlap(data, a, b, groupId) {
  const aa = normalizedParticipantSubgroups(data, a, groupId);
  const bb = normalizedParticipantSubgroups(data, b, groupId);
  return aa.some((sa) => bb.some((sb) => subgroupOverlap(sa, sb)));
}
function belongsToGroup(inst, groupId) {
  return streamGroups(inst).includes(groupId);
}

// v1619: один преподаватель может одновременно вести две разные подгруппы
// ОДНОЙ группы как одно фактически объединённое занятие. Разрешаем это только
// для одинаковых дисциплины, вида занятия и формата; потоки/разные группы не
// попадают под исключение. Это защищает от случайного разрешения преподавателю
// вести два разных предмета одновременно.
function sameTeacherSiblingSubgroupsCompatible(a, b) {
  if (!a || !b || a.instId === b.instId) return false;
  if (a.isVacancyTeacher || b.isVacancyTeacher) return false;
  if (!a.teacherId || a.teacherId !== b.teacherId) return false;
  if (String(a.subjectId || "") !== String(b.subjectId || "")) return false;
  if (String(a.typeId || "") !== String(b.typeId || "")) return false;
  if ((a.format || "inperson") !== (b.format || "inperson")) return false;
  const ag = streamGroups(a), bg = streamGroups(b);
  if (ag.length !== 1 || bg.length !== 1 || ag[0] !== bg[0]) return false;
  const asg = Number(a.subgroup || 0), bsg = Number(b.subgroup || 0);
  return asg > 0 && bsg > 0 && asg !== bsg;
}

function computeOccupancy(assignment, instances) {
  const byInst = Object.fromEntries(instances.map((i) => [i.instId, i]));
  const groupOccupants = new Map(); // `${sk}|${groupId}` -> Set(instId)
  const teacherSlots = new Set();
  const roomSlots = new Set();
  const groupDayFormats = new Map(); // `${groupId}_${day}` -> format
  const teacherDayFormats = new Map(); // `${teacherId}_${day}` -> format
  for (const instId in assignment) {
    const inst = byInst[instId];
    if (!inst) continue;
    const a = assignment[instId];
    const sk = `${a.day}_${a.period}`;
    for (const gid of streamGroups(inst)) {
      const gKey = `${sk}|${gid}`;
      if (!groupOccupants.has(gKey)) groupOccupants.set(gKey, new Set());
      groupOccupants.get(gKey).add(instId);
      groupDayFormats.set(`${gid}_${a.day}`, inst.format);
    }
    teacherSlots.add(inst.isVacancyTeacher ? `${sk}|vacancy|${instId}` : `${sk}|${inst.teacherId}`);
    for (const rid of assignmentRoomIds(a)) roomSlots.add(`${sk}|${rid}`);
    teacherDayFormats.set(`${inst.teacherId}_${a.day}`, inst.format);
  }
  return { byInst, groupOccupants, teacherSlots, roomSlots, groupDayFormats, teacherDayFormats };
}

function normalizedRoomName(room) {
  return String(room?.name || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function roomIdsEquivalent(rooms, a, b) {
  if (a == null || b == null || a === "" || b === "") return false;
  if (String(a) === String(b)) return true;
  const ra = (rooms || []).find((r) => String(r.id) === String(a));
  const rb = (rooms || []).find((r) => String(r.id) === String(b));
  if (!ra || !rb) return false;
  const na = normalizedRoomName(ra), nb = normalizedRoomName(rb);
  return !!na && na === nb;
}

function canPlace(assignment, instances, rooms, instId, day, period, roomId, extraRoomId = null, proposedHalf = undefined, config = null, allowMixedDayFormat = false) {
  const inst = instances.find((i) => i.instId === instId);
  if (!inst) return { ok: false, reason: "Занятие не найдено" };

  const overlaps = (other) => !!other && weeksOverlap(other, inst, config);
  const sameParticipantGroup = (other) => streamGroups(inst).some((gid) => belongsToGroup(other, gid) && participantSubgroupsOverlap(other, inst, gid));
  const siblingTeacherPartnersAt = (half) => Object.entries(assignment || {}).filter(([otherId, a]) => {
    if (otherId === instId || !a || Number(a.day) !== Number(day) || Number(a.period) !== Number(period)) return false;
    const other = instances.find((i) => i.instId === otherId);
    if (!other || !weeksOverlap(other, inst, config)) return false;
    const sameHalf = a.half == null || half == null || Number(a.half) === Number(half);
    return sameHalf && !inst.isVacancyTeacher && !other.isVacancyTeacher && other.teacherId === inst.teacherId;
  });
  const halfCandidates = inst.halfPair
    ? [...new Set([proposedHalf, assignment?.[instId]?.half, 0, 1].filter((h) => h === 0 || h === 1).map(Number))]
    : [null];

  // Аудиторные требования не зависят от выбранной половины пары.
  let room = null, extraRoom = null, selectedRooms = [];
  if (inst.format !== "remote") {
    room = rooms.find((r) => r.id === roomId);
    extraRoom = extraRoomId ? rooms.find((r)=>r.id===extraRoomId) : null;
    if (!room) return { ok: false, reason: "Аудитория не найдена" };
    if (extraRoomId && !extraRoom) return {ok:false,reason:"Вторая аудитория не найдена"};
    if (extraRoomId && roomIdsEquivalent(rooms, extraRoomId, roomId)) return {ok:false,reason:"Вторая аудитория должна отличаться от первой"};
    selectedRooms=[room,...(extraRoom?[extraRoom]:[])];
    // v1463: при обычном переносе внутри той же аудитории правило основной
    // аудитории преподавателя вообще не должно срабатывать. Сравниваем не только
    // с dedicatedRoomId, но и с фактической аудиторией этой пары до переноса.
    const currentAssignedRoomId = assignment?.[instId]?.roomId ?? null;
    const keepsCurrentRoom = currentAssignedRoomId != null && roomIdsEquivalent(rooms, roomId, currentAssignedRoomId);
    if (inst.dedicatedRoomId && !keepsCurrentRoom && !roomIdsEquivalent(rooms, roomId, inst.dedicatedRoomId)) return { ok: false, reason: "За преподавателем закреплена другая основная аудитория" };
    for (const selectedRoom of selectedRooms) {
      if (!inst.dedicatedRoomId && inst.allowedRoomIds && inst.allowedRoomIds.length > 0 && !inst.allowedRoomIds.some((rid) => roomIdsEquivalent(rooms, rid, selectedRoom.id))) return {ok:false,reason:"Одна из аудиторий не входит в список допустимых для занятия"};
      if (!inst.dedicatedRoomId && (!inst.allowedRoomIds || inst.allowedRoomIds.length === 0) && inst.roomType !== "любая" && selectedRoom.typeId !== inst.roomType) return {ok:false,reason:"Нужна аудитория другого типа"};
      if (inst.requiresComputer && !selectedRoom.hasComputers) return {ok:false,reason:"Все назначенные аудитории должны быть с компьютерами"};
      if (inst.requiresArt && !selectedRoom.isArtRoom) return {ok:false,reason:"Все назначенные аудитории должны быть художественными кабинетами"};
      if (inst.requiredRoomTypeId && selectedRoom.typeId !== inst.requiredRoomTypeId) return {ok:false,reason:"Дисциплина требует другой тип аудитории"};
    }
    // v1599: вместимость кабинета — мягкое предупреждение, не запрет размещения.
  }

  let firstReason = "Конфликт расписания";
  for (const half of halfCandidates) {
    let reason = "";
    const teacherPartners = siblingTeacherPartnersAt(half);
    const allowedSiblingTeacherId = teacherPartners.length === 1 && sameTeacherSiblingSubgroupsCompatible(inst, instances.find((i) => i.instId === teacherPartners[0][0])) ? teacherPartners[0][0] : null;
    if (allowedSiblingTeacherId && inst.format !== "remote") {
      const partnerAssignment = assignment?.[allowedSiblingTeacherId];
      if (partnerAssignment?.roomId && roomId && !roomIdsEquivalent(rooms, partnerAssignment.roomId, roomId)) {
        reason = "Две подгруппы одной группы у одного преподавателя должны находиться в одной аудитории";
      }
    }
    if (reason) { firstReason = reason; continue; }
    for (const [otherId, a] of Object.entries(assignment || {})) {
      if (otherId === instId || !a) continue;
      const other = instances.find((i) => i.instId === otherId);
      if (!other || !overlaps(other)) continue;

      // Формат дня должен совпадать только на реально пересекающихся неделях.
      // v1613: `allowMixedDayFormat` is a manual-only override (never set by
      // auto-scheduling, which still enforces "one format per day" strictly
      // via dayFormatOk/slotFreeForOwners in generateSchedule) — lets a
      // person deliberately place an in-person lesson on an otherwise-ДО day
      // for that group/teacher, or vice versa.
      if (!allowMixedDayFormat && a.day === day && other.format !== inst.format) {
        if (sameParticipantGroup(other)) { reason = `В этот день у группы уже стоят занятия ${other.format === "remote" ? "дистанционно" : "очно"} на пересекающихся неделях — весь день недели должен быть в одном формате`; break; }
        if (!inst.isVacancyTeacher && !other.isVacancyTeacher && other.teacherId === inst.teacherId) { reason = `В этот день у преподавателя уже стоят занятия ${other.format === "remote" ? "дистанционно" : "очно"} на пересекающихся неделях — весь день недели должен быть в одном формате`; break; }
      }
      if (a.day !== day || a.period !== period) continue;

      // Полная пара конфликтует с любой половиной; две 0,5 пары могут делить
      // один номер пары, если занимают разные половины.
      const sameHalf = (a.half == null || half == null || Number(a.half) === Number(half));
      if (!sameHalf) continue;
      if (!inst.isVacancyTeacher && !other.isVacancyTeacher && other.teacherId === inst.teacherId && otherId !== allowedSiblingTeacherId) { reason = "Преподаватель занят в это время на пересекающихся неделях"; break; }
      if (sameParticipantGroup(other)) { reason = "У группы/подгруппы уже есть занятие в это время на пересекающихся неделях"; break; }
      if (inst.format !== "remote") {
        const occupiedRooms = new Set(assignmentRoomIds(a));
        if (otherId !== allowedSiblingTeacherId && selectedRooms.some((r)=>[...occupiedRooms].some((rid)=>roomIdsEquivalent(rooms, rid, r.id)))) { reason = "Одна из аудиторий занята в это время на пересекающихся неделях"; break; }
      }
    }
    if (!reason) return { ok: true, half };
    firstReason = reason;
  }
  return { ok: false, reason: firstReason };
}

function findFreeRoom(assignment, instances, rooms, instId, day, period, config = null) {
  const inst = instances.find((i) => i.instId === instId);
  if (inst.format === "remote") return { id: null, name: "дистанционно" };
  const opts = inst.dedicatedRoomId
    ? rooms.filter((r) => roomIdsEquivalent(rooms, r.id, inst.dedicatedRoomId))
    : (inst.allowedRoomIds && inst.allowedRoomIds.length > 0)
      ? rooms.filter((r) => inst.allowedRoomIds.some((rid) => roomIdsEquivalent(rooms, rid, r.id)))
      : inst.roomType === "любая" ? rooms : rooms.filter((r) => r.typeId === inst.roomType);
  for (const r of opts) {
    if (canPlace(assignment, instances, rooms, instId, day, period, r.id, null, undefined, config).ok) return r;
  }
  return null;
}

/* ============================ scheduling engine ============================ */
/*
  Instances come from "loads" (group/subgroup + teacher + subject + pairsPerWeek).
  1) Greedy randomized construction — scores candidate slots to favor contiguous
     blocks (fewer windows) and spread the same subject across the week.
  2) Simulated-annealing local search (move / swap / place-unplaced) minimizing
     unplaced lessons >> total gaps ("windows") >> same-subject-same-day repeats.
  Hard constraints, never violated: a group-stream (whole group or a specific
  subgroup), a teacher, and a room can each hold only one lesson per slot, and a
  room's type must match what the lesson requires. Instances already present in
  `lockedIds` (manual placements, or auto placements the user pinned) are fixed
  in place and excluded from the search — this is the "combined" mode.
*/

function generateSchedule(data, prior, perfScope = null) {
  const { config, rooms, loads, teachers, subjects, groups, lessonTypes = [] } = data;
  const activeDays = config.activeDays.map((on, i) => (on ? i : null)).filter((v) => v !== null);
  const periodsPerDay = config.periodsPerDay;
  const teacherById = Object.fromEntries(teachers.map((t) => [t.id, t]));
  const subjectById = Object.fromEntries(subjects.map((s) => [s.id, s]));
  const groupById = Object.fromEntries(groups.map((g) => [g.id, g]));
  const lessonTypeById = Object.fromEntries(lessonTypes.map((t) => [t.id, t]));

  // Обязательные общие ДО-сборки для практик.
  // Состав больше не угадывается по всему потоку: пользователь вручную задаёт
  // ДО-связку конкретных строк нагрузки (группа + подгруппа).
  const practicalAssemblyPlanByLoadId = new Map();
  const practicalAssemblyPlans = [];
  const visitedRemoteLinks = new Set();
  const eligibleRemoteMember = (anchor, other) => {
    if (!anchor || !other) return false;
    const ag = groupById[anchor.groupId], og = groupById[other.groupId];
    // v1523: явная ДО-связка — это ручное решение составителя расписания.
    // Не отбрасываем выбранную группу только потому, что у неё другой курс/база
    // или другой вид занятия (например, у одной группы Лек, у другой ЗчО).
    // Жёстко сохраняем только смысл общей пары: одна дисциплина и один преподаватель.
    // Направление/специальность может отличаться: общие дисциплины можно вести потоком
    // для нескольких направлений. Флаг canBeRemote у ПРИСОЕДИНЯЕМОЙ строки
    // не является ограничением: явное ручное ДО-совмещение составителя имеет приоритет
    // и переводит именно общие выбранные слои этой нагрузки в ДО.
    return !!ag && !!og && other.subjectId === anchor.subjectId && other.teacherId === anchor.teacherId;
  };

  // v1587: единая каноническая схема форматов для комбинированной нагрузки.
  // Она применяется ДО создания schedule.instances, поэтому «Не размещено»
  // сразу получает правильные очные/ДО recurring-блоки.
  // v1689: в потоковой строке графика число 2 означает 2 ак.ч., то есть ОДНУ пару.
  // Раньше потоковый план трактовал weeklyPairs=2 как две полноценные пары и
  // создавал два одинаковых stream-instance на одну неделю. Обычные/подгрупповые
  // строки не меняем: исправление локально только для потоков.
  // v1692: делить часы на 2 можно только для РЕАЛЬНОГО потока, а не просто
  // потому, что у строки остался старый streamId/streamGroupIds. Иначе обычная
  // нагрузка с weeklyPairs=1 превращалась в 0.5 пары и получала подпись
  // «1-я половина», хотя в графике пользователь задал одну обычную пару.
  const isGraphHourStreamLoad = (l) => {
    if (!l) return false;
    const linkedIds = new Set([l.groupId, ...(l.streamGroupIds || [])].filter(Boolean));
    return loads.some((other) => {
      if (!other || other.id === l.id) return false;
      const sameExplicitStream = !!l.streamId && !!other.streamId && l.streamId === other.streamId;
      const linkedGroup = linkedIds.has(other.groupId) || (other.streamGroupIds || []).includes(l.groupId);
      if (!sameExplicitStream && !linkedGroup) return false;
      return other.subjectId === l.subjectId && other.teacherId === l.teacherId && other.typeId === l.typeId;
    });
  };
  // v1695: weeklyPairs хранит КОЛИЧЕСТВО ПАР, а не академические часы.
  // Значение 2 у потока означает две отдельные пары этой недели.
  // Половинная механика относится только к конкретной подгрупповой строке.
  const loadMayUseHalfPair = (l) => Number(l?.subgroup || 0) > 0;
  const occurrencePairUnitsForLoad = (_l, raw) => Math.max(0, Number(raw) || 0);
  const occurrenceKeysForLoad = (l) => {
    const keys = [];
    if ((l.weekPattern || "perWeek") !== "perWeek") return keys;
    for (const [weekRaw, countRaw] of Object.entries(l.weeklyPairs || {}).sort((a,b)=>Number(a[0])-Number(b[0]))) {
      const week = Number(weekRaw), count = occurrencePairUnitsForLoad(l, countRaw);
      const whole = Math.floor(count + 1e-9);
      for (let layer = 1; layer <= whole; layer++) keys.push(`${week}:${layer}`);
      if (loadMayUseHalfPair(l) && count - whole >= 0.49) keys.push(`${week}:H`);
    }
    return keys;
  };
  const occurrenceLayerOrder = (key) => {
    const [, layerRaw] = String(key).split(":");
    return layerRaw === "H" ? 999 : (Number(layerRaw) || 0);
  };
  const selectCombinedRemoteKeys = (rawKeys, remotePercent, preferRemoteFirst = false) => {
    const commonKeys = [...new Set(rawKeys)].sort((a,b)=>{
      const aw=Number(String(a).split(":")[0])||0, bw=Number(String(b).split(":")[0])||0;
      return aw-bw || occurrenceLayerOrder(a)-occurrenceLayerOrder(b);
    });
    // v1589: в комбинированном режиме ДО жёстко ограничено 50%.
    // Используем floor, чтобы на нечётном числе слоёв никогда не превысить 50%.
    const pct = 50;
    if (!commonKeys.length) return new Set();
    const requested = Math.max(0, Math.min(commonKeys.length, Math.floor((commonKeys.length * pct + 1e-9) / 100)));
    const byWeek = new Map();
    for (const key of commonKeys) {
      const week = Number(String(key).split(":")[0]);
      if (!byWeek.has(week)) byWeek.set(week, []);
      byWeek.get(week).push(key);
    }
    for (const list of byWeek.values()) list.sort((a,b)=>occurrenceLayerOrder(a)-occurrenceLayerOrder(b));
    const activeWeeks=[...byWeek.keys()].sort((a,b)=>a-b);
    const allowedRemote=[];
    if (preferRemoteFirst) {
      // v1599: комбинированная Лек строится в порядке ДО-поток -> очно.
      // При 2+ лекциях в неделю хотя бы последняя остаётся очной, а ДО берём
      // с первых слоёв. При одной лекции в неделю чередование начинается с ДО.
      const maxRemoteLayer=Math.max(0,...activeWeeks.map((week)=>Math.max(0,(byWeek.get(week)||[]).length-1)));
      for (let layerIndex=0; layerIndex<maxRemoteLayer; layerIndex++) {
        for (const week of activeWeeks) {
          const list=byWeek.get(week)||[];
          if (list.length>=2 && layerIndex<list.length-1) allowedRemote.push(list[layerIndex]);
        }
      }
      activeWeeks.forEach((week,activeIndex)=>{
        const list=byWeek.get(week)||[];
        if (list.length===1 && activeIndex%2===0) allowedRemote.push(list[0]);
      });
    } else {
      activeWeeks.forEach((week,activeIndex)=>{
        const list=byWeek.get(week)||[];
        if (list.length >= 2) {
          // Для прочих комбинированных занятий сохраняем минимум одну очную пару
          // в начале недели, а ДО распределяем по остальным слоям.
          allowedRemote.push(...list.slice(1));
        } else if (list.length === 1 && activeIndex % 2 === 1) {
          // При одной паре: очно / ДО / очно / ДО по активным неделям.
          allowedRemote.push(list[0]);
        }
      });
    }
    const target=Math.min(requested,allowedRemote.length);
    if (target<=0) return new Set();
    if (preferRemoteFirst) return new Set(allowedRemote.slice(0,target));
    if (target>=allowedRemote.length) return new Set(allowedRemote);
    const selected=new Set();
    for (let i=0;i<target;i++) {
      const idx=Math.min(allowedRemote.length-1,Math.floor((i+0.5)*allowedRemote.length/target));
      selected.add(allowedRemote[idx]);
    }
    if (selected.size<target) for (const key of allowedRemote) { selected.add(key); if (selected.size>=target) break; }
    return selected;
  };
  const subgroupCombinedSchemeCache = new Map();
  const subgroupCombinedSchemeFor = (load) => {
    if (!load || Number(load.subgroup || 0) <= 0) return null;
    const familyKey=[load.groupId,load.subjectId,load.typeId,"subgroups"].join("|");
    if (subgroupCombinedSchemeCache.has(familyKey)) return subgroupCombinedSchemeCache.get(familyKey);
    const family=loads.filter((row)=>row.groupId===load.groupId && row.subjectId===load.subjectId && row.typeId===load.typeId && Number(row.subgroup||0)>0 && !!row.canBeRemote && (row.format||"inperson")==="auto");
    if (!family.length) { subgroupCombinedSchemeCache.set(familyKey,null); return null; }
    const pct=50;
    // Берём объединение week:layer: один и тот же слой получает одинаковый формат
    // у всех подгрупп, даже если преподаватели разные.
    const scheme=selectCombinedRemoteKeys(family.flatMap(occurrenceKeysForLoad),pct,isLectureLessonTypeName(lessonTypeById[load.typeId]?.name));
    subgroupCombinedSchemeCache.set(familyKey,scheme);
    return scheme;
  };

  for (const anchor of loads) {
    if (visitedRemoteLinks.has(anchor.id)) continue;
    const remoteKind = isPracticalLessonTypeName(lessonTypeById[anchor.typeId]?.name) || isLectureLessonTypeName(lessonTypeById[anchor.typeId]?.name);
    if (!remoteKind || !anchor.canBeRemote) continue;
    const delivery = anchor.format || "inperson";
    if (delivery === "inperson") continue;
    // v1520: ДО-объединение хранится ТОЛЬКО в remoteSubgroupLoadIds.
    // streamGroupIds/streamId теперь относятся исключительно к очным потокам.
    const hasRemoteMerge = (anchor.remoteSubgroupLoadIds || []).length > 0;
    // «Комбинированно»: процент ДО работает и без объединения.
    // «Только ДО»: отдельную каноническую сборку строим лишь когда пользователь
    // действительно выбрал совмещение; одиночная нагрузка идёт обычным ДО-экземпляром.
    const wantsSoloRemote = delivery === "auto" && (Number(anchor.remotePercent) || 0) > 0;
    if (!hasRemoteMerge && !wantsSoloRemote) continue;
    const linkedIds = hasRemoteMerge
      ? new Set([anchor.id, ...(anchor.remoteSubgroupLoadIds || [])])
      : new Set([anchor.id]);
    const bucket = loads.filter((row) => linkedIds.has(row.id) && eligibleRemoteMember(anchor, row));
    if (!bucket.some((row) => row.id === anchor.id)) bucket.unshift(anchor);
    bucket.forEach((row) => visitedRemoteLinks.add(row.id));
    const manualRequested = Math.max(0, ...bucket.map((l) => Math.round(Number(l.mandatoryRemoteAssemblies) || 0)));
    const remotePercent = delivery === "auto" ? 50 : Math.max(0, Math.min(100, ...bucket.map((l) => Number(l.remotePercent) || 0)));
    const primary = bucket[0];
    const occurrenceLists = bucket.map(occurrenceKeysForLoad);
    let commonKeys = bucket.length >= 1 ? (occurrenceLists[0] || []) : [];
    for (const list of occurrenceLists.slice(1)) {
      const allowed = new Set(list);
      commonKeys = commonKeys.filter((k) => allowed.has(k));
    }
    // v102: для практик процент ДО автоматически переводится в точное число
    // общих ДО-сборок по реально общим слоям недельного графика выбранных подгрупп.
    // Если процент не задан, сохраняется обратная совместимость с ручным числом сборок.
    const requestedByPercent = remotePercent > 0 && commonKeys.length > 0
      ? Math.max(0, Math.min(commonKeys.length, delivery === "auto"
        ? Math.floor((commonKeys.length * 50 + 1e-9) / 100)
        : Math.round(commonKeys.length * remotePercent / 100)))
      : 0;
    const requested = delivery === "remote" && hasRemoteMerge ? commonKeys.length : (requestedByPercent || manualRequested);
    if (requested <= 0) continue;
    let selectedKeys;
    if (delivery === "auto" && remotePercent > 0) {
      const subgroupScheme = subgroupCombinedSchemeFor(primary);
      selectedKeys = subgroupScheme
        ? new Set(commonKeys.filter((key)=>subgroupScheme.has(key)))
        : selectCombinedRemoteKeys(commonKeys, remotePercent,isLectureLessonTypeName(lessonTypeById[primary.typeId]?.name));
    } else {
      // «Только ДО» и ручное обязательное число ДО-сборок не ограничиваются
      // минимальным очным присутствием.
      selectedKeys = new Set();
      for (let i=0;i<requested;i++) {
        const idx=Math.min(commonKeys.length-1,Math.floor((i+0.5)*commonKeys.length/requested));
        if (idx>=0) selectedKeys.add(commonKeys[idx]);
      }
    }
    const plan = {
      primary, bucket, requested: selectedKeys.size, selectedKeys,
      shortfall: delivery === "auto" && remotePercent > 0 ? 0 : Math.max(0, requested - selectedKeys.size),
      streamGroupIds: [...new Set(bucket.slice(1).map((l)=>l.groupId).filter(Boolean))],
      streamParticipants: bucket.slice(1).map((l) => ({ groupId: l.groupId, subgroup: Number(l.subgroup) || 0, loadId: l.id })),
    };
    practicalAssemblyPlans.push(plan);
    for (const load of bucket) practicalAssemblyPlanByLoadId.set(load.id, plan);
  }

  // Лекционные и очные практические потоки строим ПО КОНКРЕТНОМУ СЛОЮ
  // графика, а не как один «монолит» на весь streamId.
  // v1644: если у связанных групп разное количество пар, общий поток занимает
  // только реально совпадающие недели/слои. Лишний слой группы с большей
  // нагрузкой остаётся её обычной индивидуальной парой и НЕ размножается на
  // остальные группы потока.
  const addSelectedStreamKey = (map, loadId, key) => {
    if (!map.has(loadId)) map.set(loadId, { selectedKeys: new Set() });
    map.get(loadId).selectedKeys.add(key);
  };

  const lectureStreamPlanByLoadId = new Map(); // union ключей, уже забранных потоком
  const lectureStreamPlans = [];
  const lectureStreamBuckets = new Map();
  for (const load of loads) {
    if (!isLectureLessonTypeName(lessonTypeById[load.typeId]?.name)) continue;
    const hasStream = (load.streamGroupIds || []).length > 0 || !!load.streamId;
    if (!hasStream) continue;
    const sid = load.streamId || `legacy:${[load.subjectId,load.teacherId,load.typeId,...[load.groupId,...(load.streamGroupIds||[])].sort()].join("|")}`;
    if (!lectureStreamBuckets.has(sid)) lectureStreamBuckets.set(sid, []);
    lectureStreamBuckets.get(sid).push(load);
  }
  for (const [streamId, seed] of lectureStreamBuckets) {
    const wantedGroups = new Set(seed.flatMap((l)=>[l.groupId,...(l.streamGroupIds||[])]));
    const anchor = seed[0];
    const bucket = loads.filter((row)=>wantedGroups.has(row.groupId) && isLectureLessonTypeName(lessonTypeById[row.typeId]?.name) && row.subjectId===anchor.subjectId && row.teacherId===anchor.teacherId && row.typeId===anchor.typeId && Number(row.subgroup||0)===Number(anchor.subgroup||0));
    if (bucket.length < 2) continue;

    // На каждом week:layer состав участников определяется отдельно.
    // Это принципиально для графиков 2/1, 2/2/1 и т.п.
    const membersByKey = new Map();
    for (const row of bucket) {
      for (const key of occurrenceKeysForLoad(row)) {
        if (!membersByKey.has(key)) membersByKey.set(key, []);
        membersByKey.get(key).push(row);
      }
    }
    const orderedKeys = [...membersByKey.keys()].sort((a,b)=>{
      const aw=Number(String(a).split(":")[0])||0, bw=Number(String(b).split(":")[0])||0;
      return aw-bw || occurrenceLayerOrder(a)-occurrenceLayerOrder(b);
    });
    for (const key of orderedKeys) {
      // Если конкретный слой этой строки уже ушёл в ДО-сборку, очно он в поток
      // второй раз не попадает.
      const members = (membersByKey.get(key) || []).filter((row)=>{
        const remotePlan = practicalAssemblyPlanByLoadId.get(row.id);
        return !remotePlan?.selectedKeys?.has(key);
      });
      if (members.length < 2) continue;
      const primary = members[0];
      const plan = {
        streamId, bucket: members, primary,
        selectedKeys: new Set([key]), remoteKeys: new Set(), inPersonKeys: new Set([key]), allRemote:false,
        streamGroupIds: members.slice(1).map((l)=>l.groupId),
        streamParticipants: members.slice(1).map((l)=>({groupId:l.groupId,subgroup:Number(l.subgroup)||0,loadId:l.id}))
      };
      lectureStreamPlans.push(plan);
      members.forEach((l)=>addSelectedStreamKey(lectureStreamPlanByLoadId,l.id,key));
    }
  }

  // Очные потоки практических занятий для всей группы — тот же принцип:
  // участники вычисляются отдельно на каждом слое графика.
  const practicalInPersonStreamPlanByLoadId = new Map();
  const practicalInPersonStreamPlans = [];
  const practicalInPersonBuckets = new Map();
  for (const load of loads) {
    if (!isPracticalLessonTypeName(lessonTypeById[load.typeId]?.name) || Number(load.subgroup || 0) !== 0) continue;
    if (!(load.streamGroupIds || []).length && !load.streamId) continue;
    if ((load.format || "inperson") === "remote") continue;
    const sid = load.streamId || `practical_inperson:${[load.subjectId,load.teacherId,load.typeId,...[load.groupId,...(load.streamGroupIds||[])].sort()].join("|")}`;
    if (!practicalInPersonBuckets.has(sid)) practicalInPersonBuckets.set(sid, []);
    practicalInPersonBuckets.get(sid).push(load);
  }
  for (const [streamId, seed] of practicalInPersonBuckets) {
    const anchor = seed[0];
    const anchorGroup = groupById[anchor.groupId];
    if (!anchorGroup) continue;
    const wantedGroups = new Set(seed.flatMap((l)=>[l.groupId,...(l.streamGroupIds||[])]));
    const bucket = loads.filter((row)=>{
      const g = groupById[row.groupId];
      return wantedGroups.has(row.groupId) && !!g && sameStreamStudyStage(g, anchorGroup) &&
        isPracticalLessonTypeName(lessonTypeById[row.typeId]?.name) && Number(row.subgroup||0)===0 &&
        row.subjectId===anchor.subjectId && row.teacherId===anchor.teacherId && row.typeId===anchor.typeId;
    });
    if (bucket.length < 2) continue;

    const membersByKey = new Map();
    for (const row of bucket) {
      for (const key of occurrenceKeysForLoad(row)) {
        if (!membersByKey.has(key)) membersByKey.set(key, []);
        membersByKey.get(key).push(row);
      }
    }
    const orderedKeys = [...membersByKey.keys()].sort((a,b)=>{
      const aw=Number(String(a).split(":")[0])||0, bw=Number(String(b).split(":")[0])||0;
      return aw-bw || occurrenceLayerOrder(a)-occurrenceLayerOrder(b);
    });
    for (const key of orderedKeys) {
      const members = (membersByKey.get(key) || []).filter((row)=>{
        const remotePlan = practicalAssemblyPlanByLoadId.get(row.id);
        return !remotePlan?.selectedKeys?.has(key);
      });
      if (members.length < 2) continue;
      const primary = members[0];
      const plan = {
        streamId, bucket: members, primary, selectedKeys:new Set([key]),
        streamGroupIds:members.slice(1).map((l)=>l.groupId),
        streamParticipants:members.slice(1).map((l)=>({groupId:l.groupId,subgroup:0,loadId:l.id}))
      };
      practicalInPersonStreamPlans.push(plan);
      members.forEach((l)=>addSelectedStreamKey(practicalInPersonStreamPlanByLoadId,l.id,key));
    }
  }

  const compatiblePracticalStreamGroupIds = (load) => {
    const primaryGroup = groupById[load.groupId];
    if (!primaryGroup) return [];
    return (load.streamGroupIds || []).filter((gid) => {
      const g = groupById[gid];
      if (!g || !sameStreamStudyStage(g, primaryGroup)) return false;
      return loads.some((other) => other.id !== load.id && other.groupId === gid && other.subjectId === load.subjectId && other.teacherId === load.teacherId && other.typeId === load.typeId && Number(other.subgroup||0) === Number(load.subgroup||0) && !!other.canBeRemote);
    });
  };

  const instances = [];

  // Сначала создаём обязательные общие ДО-сборки. Каждая сборка действует ровно
  // на одну конкретную учебную неделю/слой графика и тем самым считается ровно один раз.
  for (const plan of practicalAssemblyPlans) {
    const load = plan.primary;
    const groupIds = [...new Set([load.groupId, ...plan.streamGroupIds].filter(Boolean))];
    const groupsTotal = defaultStudentsForLoadGroups(groupById, groupIds, load.subgroup || 0);
    const commonRemote = {
      loadId: load.id,
      groupId: load.groupId,
      teacherId: load.teacherId,
      subjectId: load.subjectId,
      typeId: load.typeId,
      roomType: load.roomType,
      subgroup: load.subgroup || 0,
      format: "remote",
      streamGroupIds: plan.streamGroupIds,
      streamParticipants: plan.streamParticipants,
      isVacancyTeacher: !!teacherById[load.teacherId]?.isVacancy,
      profileSubject: !!load.profileSubject,
      position: load.position || "any",
      allowedRoomIds: [],
      preferredRoomIds: [],
      twoRooms: false,
      requiresComputer: false,
      requiresArt: false,
      requiredRoomTypeId: "",
      minRoomCapacity: 0,
      requiredEquipment: "",
      dedicatedRoomId: "",
      pairing: "none",
      requiredCapacity: Number(load.expectedStudents) > 0 ? Number(load.expectedStudents) : groupsTotal,
      mandatoryRemoteAssembly: true,
    };
    let seq = 0;
    // ДО-недели одного слоя объединяем в один recurring-блок. Это же представление
    // используется в «Не размещено»: никаких отдельных карточек на каждую неделю.
    const selectedByLayer = new Map();
    for (const key of plan.selectedKeys) {
      const [weekRaw, layerRaw] = key.split(":");
      if (!selectedByLayer.has(layerRaw)) selectedByLayer.set(layerRaw, []);
      selectedByLayer.get(layerRaw).push(Number(weekRaw));
    }
    for (const [layerRaw, rawWeeks] of [...selectedByLayer.entries()].sort((a,b)=>(a[0]==="H"?999:Number(a[0]))-(b[0]==="H"?999:Number(b[0])))) {
      const weeks=[...new Set(rawWeeks)].sort((a,b)=>a-b);
      if (!weeks.length) continue;
      const halfPair=layerRaw==="H";
      instances.push({
        instId: `${load.id}__MANDO_${layerRaw}`,
        ...commonRemote,
        halfPair,
        weekPattern: "custom",
        customWeeks: weeks,
        mandatoryAssemblyNumber: ++seq,
        blockId: "", blockIndex: 0, blockSize: 1, blockMode: "",
      });
    }
    // Если в графике меньше пар, чем пользователь обязал собрать дистанционно,
    // недостающие сборки нельзя «простить»: они сразу становятся неразмещёнными.
    for (let i = 0; i < plan.shortfall; i++) {
      instances.push({
        instId: `${load.id}__MANDO_SHORTFALL_${i+1}`,
        ...commonRemote,
        weekPattern: "custom",
        customWeeks: [],
        mandatoryAssemblyNumber: ++seq,
        mandatoryAssemblyMissingFromGraph: true,
        forceUnplaced: true,
        blockId: "", blockIndex: 0, blockSize: 1, blockMode: "",
      });
    }
  }

  for (const plan of lectureStreamPlans) {
    const load=plan.primary;
    const groupIds=[...new Set(plan.bucket.map((l)=>l.groupId))];
    const groupsTotal=defaultStudentsForLoadGroups(groupById,groupIds,load.subgroup||0);
    let seq=0;
    for (const key of plan.selectedKeys) {
      const [weekRaw,layerRaw]=key.split(":");
      const week=Number(weekRaw), halfPair=layerRaw==="H";
      instances.push({
        instId:`${load.id}__LECTURE_STREAM_${week}_${layerRaw}_${++seq}`, loadId:load.id, groupId:load.groupId, teacherId:load.teacherId, subjectId:load.subjectId, typeId:load.typeId,
        roomType:load.roomType, subgroup:load.subgroup||0, format:"inperson", streamGroupIds:plan.streamGroupIds, streamParticipants:plan.streamParticipants,
        isVacancyTeacher:!!teacherById[load.teacherId]?.isVacancy, profileSubject:!!load.profileSubject, position:load.position||"any", allowedRoomIds:load.allowedRoomIds||[], preferredRoomIds:teacherById[load.teacherId]?.preferredRoomIds||[], twoRooms:!!load.twoRooms,
        requiresComputer:!!subjectById[load.subjectId]?.requiresComputerRoom, requiresArt:!!subjectById[load.subjectId]?.requiresArtRoom, requiredRoomTypeId:subjectById[load.subjectId]?.requiredRoomTypeId||"", minRoomCapacity:0, requiredEquipment:"", dedicatedRoomId:teacherById[load.teacherId]?.dedicatedRoomId||"", pairing:"none", halfPair,
        requiredCapacity:Number(load.expectedStudents)>0?Number(load.expectedStudents):groupsTotal, weekPattern:"custom", customWeeks:[week], lectureRemoteStream:false, lectureInPersonStream:true,
        blockId:"",blockIndex:0,blockSize:1,blockMode:""
      });
    }
  }

  for (const plan of practicalInPersonStreamPlans) {
    const load=plan.primary;
    const groupIds=[...new Set(plan.bucket.map((l)=>l.groupId))];
    const groupsTotal=defaultStudentsForLoadGroups(groupById,groupIds,0);
    let seq=0;
    for (const key of plan.selectedKeys) {
      const [weekRaw,layerRaw]=key.split(":");
      const week=Number(weekRaw), halfPair=layerRaw==="H";
      instances.push({
        instId:`${load.id}__PRACTICAL_STREAM_${week}_${layerRaw}_${++seq}`, loadId:load.id, groupId:load.groupId, teacherId:load.teacherId, subjectId:load.subjectId, typeId:load.typeId,
        roomType:load.roomType, subgroup:0, format:"inperson", streamGroupIds:plan.streamGroupIds, streamParticipants:plan.streamParticipants,
        isVacancyTeacher:!!teacherById[load.teacherId]?.isVacancy, profileSubject:!!load.profileSubject, position:load.position||"any", allowedRoomIds:load.allowedRoomIds||[], preferredRoomIds:teacherById[load.teacherId]?.preferredRoomIds||[], twoRooms:!!load.twoRooms,
        requiresComputer:!!subjectById[load.subjectId]?.requiresComputerRoom, requiresArt:!!subjectById[load.subjectId]?.requiresArtRoom, requiredRoomTypeId:subjectById[load.subjectId]?.requiredRoomTypeId||"", minRoomCapacity:0, requiredEquipment:"", dedicatedRoomId:teacherById[load.teacherId]?.dedicatedRoomId||"", pairing:"none", halfPair,
        requiredCapacity:Number(load.expectedStudents)>0?Number(load.expectedStudents):groupsTotal, weekPattern:"custom", customWeeks:[week], practicalInPersonStream:true,
        blockId:"",blockIndex:0,blockSize:1,blockMode:""
      });
    }
  }

  loads.forEach((load) => {
    const isPractical = isPracticalLessonTypeName(lessonTypeById[load.typeId]?.name);
    let resolvedFormat = load.canBeRemote ? (load.format || "auto") : "inperson";
    let effectiveStreamGroupIds = load.streamGroupIds || [];
    let effectiveStreamParticipants = [];
    if (isPractical) {
      if (resolvedFormat === "auto") {
        // В режиме «Авто» обычные практические пары остаются очными.
        // Только заданное пользователем точное количество обязательных сборок
        // создаётся отдельно ниже как общие дистанционные пары.
        resolvedFormat = "inperson";
        effectiveStreamGroupIds = [];
        effectiveStreamParticipants = [];
      } else if (resolvedFormat === "remote") {
        // ДО-связка строится отдельно через remoteSubgroupLoadIds; очный поток сюда не переносим.
        effectiveStreamGroupIds = [];
        effectiveStreamParticipants = [];
      } else {
        // Очные практические потоки строятся отдельными каноническими экземплярами.
        effectiveStreamGroupIds = [];
        effectiveStreamParticipants = [];
      }
    } else if (resolvedFormat === "auto") {
      // Для лекций «Можно ДО» дистанционная часть создаётся отдельными общими
      // потоковыми экземплярами выше. Оставшиеся экземпляры этой строки очные.
      resolvedFormat = "inperson";
      effectiveStreamGroupIds = [];
      effectiveStreamParticipants = [];
    } else if (isLectureLessonTypeName(lessonTypeById[load.typeId]?.name) && resolvedFormat === "remote") {
      // Общая часть «Только ДО» также создаётся отдельным каноническим потоком;
      // здесь остаются только возможные несовпадающие по графику хвосты группы.
      effectiveStreamGroupIds = [];
      effectiveStreamParticipants = [];
    }
    if (isPractical && practicalInPersonStreamPlanByLoadId.has(load.id)) {
      effectiveStreamGroupIds = [];
      effectiveStreamParticipants = [];
    }
    // Если для лекции уже построен канонический поток (очный или ДО), обычные
    // остаточные экземпляры не должны повторно включать все группы потока.
    if (isLectureLessonTypeName(lessonTypeById[load.typeId]?.name) && lectureStreamPlanByLoadId.has(load.id)) {
      effectiveStreamGroupIds = [];
      effectiveStreamParticipants = [];
    }
    const involvedGroupIds = [...new Set([load.groupId, ...effectiveStreamGroupIds].filter(Boolean))];
    const groupsTotal = defaultStudentsForLoadGroups(groupById, involvedGroupIds, load.subgroup || 0);
    const requiredCapacity = Number(load.expectedStudents) > 0 ? Number(load.expectedStudents) : groupsTotal;
    const common = {
      loadId: load.id,
      groupId: load.groupId,
      teacherId: load.teacherId,
      subjectId: load.subjectId,
      typeId: load.typeId,
      roomType: load.roomType,
      subgroup: load.subgroup || 0,
      format: resolvedFormat,
      streamGroupIds: effectiveStreamGroupIds,
      streamParticipants: effectiveStreamParticipants,
      isVacancyTeacher: !!teacherById[load.teacherId]?.isVacancy,
      profileSubject: !!load.profileSubject,
      position: load.position || "any",
      allowedRoomIds: load.allowedRoomIds || [],
      preferredRoomIds: teacherById[load.teacherId]?.preferredRoomIds || [],
      twoRooms: !!load.twoRooms,
      requiresComputer: !!subjectById[load.subjectId]?.requiresComputerRoom,
      requiresArt: !!subjectById[load.subjectId]?.requiresArtRoom,
      requiredRoomTypeId: subjectById[load.subjectId]?.requiredRoomTypeId || "",
      minRoomCapacity: 0,
      requiredEquipment: "",
      dedicatedRoomId: teacherById[load.teacherId]?.dedicatedRoomId || "",
      pairing: load.pairing || "none",
      halfPair: false,
      requiredCapacity,
    };

    if ((load.weekPattern || "all") === "perWeek") {
      // «Графики»: разное число пар на разных неделях — раскладываем на слои,
      // каждый слой — обычный шаблонный инстанс с custom-набором недель.
      const wp = load.weeklyPairs || {};
      const graphPairUnitsAt = (w) => occurrencePairUnitsForLoad(load, wp[w]);
      const weekNums = Object.keys(wp).map(Number).filter((w) => graphPairUnitsAt(w) > 0);
      const maxCount = weekNums.length ? Math.max(...weekNums.map((w) => graphPairUnitsAt(w))) : 0;
      const maxWhole = Math.floor(maxCount + 1e-9);
      const mandatoryPlan = practicalAssemblyPlanByLoadId.get(load.id) || null;
      const lecturePlan = isLectureLessonTypeName(lessonTypeById[load.typeId]?.name) ? lectureStreamPlanByLoadId.get(load.id) : null;
      const practicalInPersonPlan = isPractical ? practicalInPersonStreamPlanByLoadId.get(load.id) : null;
      const graphPairing = load.pairing || "none";
      const graphBlockSize = { block2: 2, block3: 3, block4: 4 }[graphPairing] || 0;
      for (let layer = 1; layer <= maxWhole; layer++) {
        const weeksForLayer = weekNums.filter((w) => graphPairUnitsAt(w) >= layer && !(mandatoryPlan?.selectedKeys?.has(`${w}:${layer}`)) && !(lecturePlan?.selectedKeys?.has(`${w}:${layer}`)) && !(practicalInPersonPlan?.selectedKeys?.has(`${w}:${layer}`)));
        if (weeksForLayer.length === 0) continue;
        const blockIndex = graphBlockSize ? (layer - 1) % graphBlockSize : 0;
        const blockStart = graphBlockSize ? (layer - blockIndex) : 0;
        const remainingInBlock = graphBlockSize ? Math.min(graphBlockSize, maxWhole - blockStart + 1) : 1;
        instances.push({
          instId: `${load.id}__L${layer}`, ...common, weekPattern: "custom", customWeeks: weeksForLayer,
          blockId: graphBlockSize ? `${load.id}__graph_blk${Math.floor((layer - 1) / graphBlockSize)}` : "",
          blockIndex, blockSize: remainingInBlock, blockMode: graphBlockSize ? "consecutive" : ""
        });
      }
      // v109: 1 ак.ч. = половина пары. Две разные половины могут делить один номер пары.
      const halfWeeks = loadMayUseHalfPair(load)
        ? weekNums.filter((w) => (graphPairUnitsAt(w) - Math.floor(graphPairUnitsAt(w))) >= 0.49 && !(mandatoryPlan?.selectedKeys?.has(`${w}:H`)) && !(lecturePlan?.selectedKeys?.has(`${w}:H`)) && !(practicalInPersonPlan?.selectedKeys?.has(`${w}:H`)))
        : [];
      if (halfWeeks.length) instances.push({ instId: `${load.id}__H`, ...common, halfPair: true, weekPattern: "custom", customWeeks: halfWeeks, blockId: "", blockIndex: 0, blockSize: 1, blockMode: "" });
      return;
    }

    const rawPairs = Math.max(0, Number(load.pairsPerWeek) || 0);
    const n = Math.floor(rawPairs + 1e-9);
    const hasHalf = rawPairs - n >= 0.49;
    const pairing = load.pairing || "none";
    const weekFields = { weekPattern: load.weekPattern || "all", customWeeks: load.customWeeks || [] };

    if (pairing === "oneDay" && n > 0) {
      const blockId = `${load.id}__oneday`;
      for (let i = 0; i < n; i++) {
        instances.push({ instId: `${load.id}__${i}`, ...common, ...weekFields, blockId, blockIndex: i, blockSize: n, blockMode: "oneDay" });
      }
      return;
    }

    if ((pairing === "block2" || pairing === "block3" || pairing === "block4") && n > 0) {
      const size = { block2: 2, block3: 3, block4: 4 }[pairing];
      let idx = 0, blockNum = 0;
      while (idx < n) {
        const thisSize = Math.min(size, n - idx);
        const blockId = `${load.id}__blk${blockNum}`;
        for (let k = 0; k < thisSize; k++) {
          instances.push({ instId: `${load.id}__${idx + k}`, ...common, ...weekFields, blockId, blockIndex: k, blockSize: thisSize, blockMode: "consecutive" });
        }
        idx += thisSize;
        blockNum++;
      }
      return;
    }

    // "none" и "single" — обычные независимые инстансы.
    for (let i = 0; i < n; i++) instances.push({ instId: `${load.id}__${i}`, ...common, ...weekFields, blockId: "", blockIndex: 0, blockSize: 1, blockMode: "" });
    if (loadMayUseHalfPair(load) && hasHalf) instances.push({ instId: `${load.id}__H`, ...common, ...weekFields, halfPair: true, blockId: "", blockIndex: 0, blockSize: 1, blockMode: "" });
  });

  // v1656: страховка графика от потери отдельной week:layer потребности внутри потока.
  // Поток — это способ совместного проведения, но КАЖДАЯ строка нагрузки по-прежнему
  // обязана иметь техническое покрытие для каждого значения weeklyPairs. Если из-за
  // пересекающихся streamId/разного числа пар канонический stream-instance не был
  // создан для конкретной строки/недели/слоя, добавляем индивидуальный repair-instance.
  // Он не размещается автоматически и поэтому честно появляется в «Не размещено».
  const graphCoverage = new Set();
  const graphLayerOfInstance = (inst) => {
    if (inst?.halfPair) return "H";
    const id = String(inst?.instId || "");
    let m = id.match(/__L(\d+)$/);
    if (m) return m[1];
    // v1687: для обычной нагрузки с pairsPerWeek > 1 генератор создаёт
    // instId вида load__0, load__1, ... . Раньше они все падали в fallback
    // layer="1", поэтому после размещения первой пары вторая считалась
    // тем же самым требованием и исчезала из picker/«Не размещено».
    // Индекс 0 соответствует первому слою, поэтому переводим его в 1-based layer.
    m = id.match(/__(\d+)$/);
    if (m) return String((Number(m[1]) || 0) + 1);
    m = id.match(/__(?:LECTURE_STREAM|PRACTICAL_STREAM)_\d+_([^_]+)_/);
    if (m) return m[1];
    m = id.match(/__MANDO_([^_]+)$/);
    if (m) return m[1];
    m = id.match(/__GRAPH_REPAIR_\d+_([^_]+)$/);
    if (m) return m[1];
    return "1";
  };
  // v1660: старые/переупакованные потоковые instances иногда содержат участника
  // без loadId. Тогда v1656 считал, что конкретная строка нагрузки не покрыта,
  // создавал GRAPH_REPAIR и в итоге уже поставленная ДО-пара снова предлагалась
  // как неразмещённая. Восстанавливаем loadId участника по его группе +
  // предмету + преподавателю + виду занятия + подгруппе.
  const graphCoveredLoadIds = (inst) => {
    const ids = new Set([String(inst?.loadId || "")].filter(Boolean));
    for (const part of streamParticipants(inst || {})) {
      if (part?.loadId) { ids.add(String(part.loadId)); continue; }
      for (const load of loads) {
        if (String(load?.groupId || "") !== String(part?.groupId || "")) continue;
        if (String(load?.subjectId || "") !== String(inst?.subjectId || "")) continue;
        if (String(load?.teacherId || "") !== String(inst?.teacherId || "")) continue;
        if (String(load?.typeId || "") !== String(inst?.typeId || "")) continue;
        if (Number(load?.subgroup || 0) !== Number(part?.subgroup || 0)) continue;
        ids.add(String(load.id));
      }
    }
    return ids;
  };
  for (const inst of instances) {
    const layer = graphLayerOfInstance(inst);
    const coveredLoadIds = graphCoveredLoadIds(inst);
    for (const week of weekNumbersForInstance(config, inst).map(Number)) {
      for (const loadId of coveredLoadIds) graphCoverage.add(`${loadId}|${week}|${layer}`);
    }
  }
  for (const load of loads) {
    if ((load.weekPattern || "all") !== "perWeek") continue;
    const wp = load.weeklyPairs || {};
    const template = instances.find((inst) => String(inst?.loadId || "") === String(load.id)) || null;
    for (const [weekRaw, countRaw] of Object.entries(wp)) {
      const week = Number(weekRaw), count = occurrencePairUnitsForLoad(load, countRaw);
      if (!Number.isFinite(week) || week <= 0 || count <= 0) continue;
      const whole = Math.floor(count + 1e-9);
      const requiredLayers = Array.from({length: whole}, (_,i)=>String(i+1));
      if (loadMayUseHalfPair(load) && count - whole >= 0.49) requiredLayers.push("H");
      for (const layer of requiredLayers) {
        const covKey = `${load.id}|${week}|${layer}`;
        if (graphCoverage.has(covKey)) continue;
        const halfPair = layer === "H";
        const teacher = teacherById[load.teacherId] || {};
        const repair = {
          ...(template ? { ...template } : {}),
          instId: `${load.id}__GRAPH_REPAIR_${week}_${layer}`,
          loadId: load.id,
          groupId: load.groupId,
          teacherId: load.teacherId,
          subjectId: load.subjectId,
          typeId: load.typeId,
          roomType: load.roomType,
          subgroup: Number(load.subgroup || 0),
          format: (load.canBeRemote && (load.format || "inperson") === "remote") ? "remote" : "inperson",
          streamGroupIds: [], streamParticipants: [],
          isVacancyTeacher: !!teacher.isVacancy,
          profileSubject: !!load.profileSubject,
          position: load.position || "any",
          allowedRoomIds: load.allowedRoomIds || [],
          preferredRoomIds: teacher.preferredRoomIds || [],
          twoRooms: !!load.twoRooms,
          requiresComputer: !!subjectById[load.subjectId]?.requiresComputerRoom,
          requiresArt: !!subjectById[load.subjectId]?.requiresArtRoom,
          requiredRoomTypeId: subjectById[load.subjectId]?.requiredRoomTypeId || "",
          dedicatedRoomId: teacher.dedicatedRoomId || "",
          pairing: "none", halfPair,
          weekPattern: "custom", customWeeks: [week],
          blockId: "", blockIndex: 0, blockSize: 1, blockMode: "",
          graphCoverageRepair: true,
        };
        instances.push(repair);
        graphCoverage.add(covKey);
      }
    }
  }

  // v1480: ручное расщепление — часть структуры расписания, а не временный UI-эффект.
  // При любом пересчёте восстанавливаем отдельные manualSplit-фрагменты из prior,
  // вычитая их недели из канонического экземпляра, построенного по нагрузке.
  // Благодаря этому «Пересчитать» и «Пересчитать группу» больше не склеивают
  // вручную разделённые недели обратно в один __L* / __H блок.
  if (prior?.instances?.length) {
    const canonicalById = new Map(instances.map((i) => [i.instId, i]));
    const splitChildren = (prior.instances || []).filter((i) =>
      i?.manualSplit && i?.splitFromInstId && i.instId !== i.splitFromInstId
    );
    const restoredChildren = [];
    const restoredIds = new Set();
    for (const old of splitChildren) {
      if (!old?.instId || restoredIds.has(old.instId)) continue;
      const base = canonicalById.get(old.splitFromInstId);
      if (!base) continue;
      const allowed = new Set(weekNumbersForInstance(config, base).map(Number));
      const keepWeeks = weekNumbersForInstance(config, old)
        .map(Number)
        .filter((w) => allowed.has(w))
        .sort((a,b)=>a-b);
      if (!keepWeeks.length) continue;
      if (base.weekPattern === "custom") {
        base.customWeeks = (base.customWeeks || []).map(Number).filter((w) => !keepWeeks.includes(w));
      }
      restoredChildren.push({
        ...old,
        weekPattern: "custom",
        customWeeks: keepWeeks,
        blockId: "", blockIndex: 0, blockSize: 1, blockMode: "",
        manualSplit: true,
        splitFromInstId: old.splitFromInstId,
      });
      restoredIds.add(old.instId);
    }
    if (restoredChildren.length) {
      for (let i = instances.length - 1; i >= 0; i--) {
        const inst = instances[i];
        if (inst.weekPattern === "custom" && (inst.customWeeks || []).length === 0 && !inst.forceUnplaced) instances.splice(i, 1);
      }
      instances.push(...restoredChildren);
    }
  }

  // v1464: «Графики» должны быть источником истины для состава недель
  // расписания сразу после редактирования, не только после полного пересчёта.
  // В служебном режиме возвращаем только канонический набор экземпляров,
  // не запуская тяжёлую авторасстановку.
  if (perfScope?.instancesOnly) {
    return { assignment: {}, unplaced: instances.map((i) => i.instId), stats: emptyStats(), instances, locked: [] };
  }

  if (activeDays.length === 0 || periodsPerDay < 1 || instances.length === 0) {
    return { assignment: {}, unplaced: instances.map((i) => i.instId), stats: emptyStats(), instances, locked: [] };
  }

  const shuffledRooms = [...rooms].sort(() => Math.random() - 0.5);
  const byInst = Object.fromEntries(instances.map((i) => [i.instId, i]));
  const instIdSet = new Set(instances.map((i) => i.instId));

  const busyGroupOccupants = new Map(); // `${sk}|${groupId}` -> Set(instId)
  const busyTeacher = new Set(); // `${sk}|t|${teacherId}`
  const busyRoom = new Set(); // `${sk}|r|${roomId}`
  const groupDayPeriods = new Map();
  const teacherDayPeriods = new Map();
  const groupDayInstances = new Map(); // `${groupId}_${day}` -> Set(instId) — для проверки единого формата дня (очно/ДО)
  const teacherDayInstances = new Map();
  const assignment = {};
  // v143 performance: static indexes used throughout generation. Previously many
  // candidate checks repeatedly scanned every instance / practice / room.
  const scopedGroupId = String(perfScope?.groupId || "").trim();
  const scopedWeekNumber = Math.max(0, Number(perfScope?.weekNumber || 0));
  const instanceWeeks = new Map(instances.map((inst) => [inst.instId, weekNumbersForInstance(config, inst)]));
  const weeksOf = (inst) => instanceWeeks.get(inst?.instId) || weekNumbersForInstance(config, inst);
  const inWeekScope = (inst) => !scopedWeekNumber || weeksOf(inst).map(Number).includes(scopedWeekNumber);
  const busyTeacherOccupants = new Map(); // `${sk}|teacherId` -> Set(instId)
  const busyRoomOccupants = new Map(); // `${sk}|roomId` -> Set(instId)
  const practiceRoomSlots = new Set();
  for (const pr of (data.practices || [])) for (const b of (pr.roomBookings || [])) {
    if (b?.roomId && b?.date && b?.period != null) practiceRoomSlots.add(`${b.date}|${Number(b.period)}|${b.roomId}`);
  }
  const blockedDatesByGroup = new Map();
  for (const b of (data.groupDayBlocks || [])) {
    if (!b?.groupId || !b?.date) continue;
    if (!blockedDatesByGroup.has(b.groupId)) blockedDatesByGroup.set(b.groupId, new Set());
    blockedDatesByGroup.get(b.groupId).add(b.date);
  }
  const blockedSlotsByGroup = new Map();
  for (const b of (data.groupSlotBlocks || [])) {
    if (!b?.groupId || b?.period == null) continue;
    if (!blockedSlotsByGroup.has(b.groupId)) blockedSlotsByGroup.set(b.groupId, []);
    blockedSlotsByGroup.get(b.groupId).push(b);
  }
  const loadInstanceIds = new Map();
  for (const inst of instances) {
    if (!loadInstanceIds.has(inst.loadId)) loadInstanceIds.set(inst.loadId, []);
    loadInstanceIds.get(inst.loadId).push(inst.instId);
  }

  const slotKey = (day, period) => `${day}_${period}`;
  const dpKey = (ownerId, day) => `${ownerId}_${day}`;
  const halfConflicts = (existingHalf, proposedHalf) => existingHalf == null || proposedHalf == null || Number(existingHalf) === Number(proposedHalf);
  const assignmentConflictsHalf = (otherId, inst, half) => {
    const oa = assignment[otherId]; const other = byInst[otherId];
    return !!oa && !!other && weeksOverlap(other, inst, config) && halfConflicts(oa.half ?? null, half);
  };

  function groupDayFormat(groupId, day) {
    const set = groupDayInstances.get(dpKey(groupId, day));
    if (!set || set.size === 0) return null;
    const firstId = set.values().next().value;
    return byInst[firstId]?.format || "inperson";
  }
  function teacherDayFormatOf(teacherId, day) {
    const set = teacherDayInstances.get(dpKey(teacherId, day));
    if (!set || set.size === 0) return null;
    const firstId = set.values().next().value;
    return byInst[firstId]?.format || "inperson";
  }
  function dayFormatOk(inst, day) {
    for (const gid of streamGroups(inst)) {
      const gf = groupDayFormat(gid, day);
      if (gf && gf !== inst.format) return false;
    }
    const tf = teacherDayFormatOf(inst.teacherId, day);
    if (tf && tf !== inst.format) return false;
    return true;
  }

  function roomFitsCapacity(room, inst) {
    // v1599: вместимость не является hard constraint.
    return true;
  }
  const candidateRoomCache = new Map();
  function candidateRooms(inst) {
    const cached = candidateRoomCache.get(inst.instId);
    if (cached) return cached;
    let opts;
    if (inst.dedicatedRoomId) opts = shuffledRooms.filter((r) => r.id === inst.dedicatedRoomId);
    else if (inst.allowedRoomIds && inst.allowedRoomIds.length > 0) opts = shuffledRooms.filter((r) => inst.allowedRoomIds.includes(r.id));
    else {
      opts = inst.roomType === "любая" ? shuffledRooms : shuffledRooms.filter((r) => r.typeId === inst.roomType);
      if (inst.requiresComputer) opts = opts.filter((r) => r.hasComputers);
      if (inst.requiresArt) opts = opts.filter((r) => r.isArtRoom);
    }
    // v1623: аудитории с пометкой «Вознесенский пр., 44» (isExternal)
    // разрешены только для ручного размещения. Автогенератор никогда не должен
    // выбирать их — даже если такая аудитория указана как закреплённая/допустимая.
    opts = opts.filter((r) => !r.isExternal);
    if (inst.requiredRoomTypeId) opts = opts.filter((r)=>r.typeId===inst.requiredRoomTypeId);
    if (inst.requiredEquipment) opts = opts.filter((r)=>splitTags(inst.requiredEquipment).every((x)=>new Set(splitTags(r.equipment)).has(x)));
    if (!inst.twoRooms) opts = opts.filter((r) => roomFitsCapacity(r, {...inst, requiredCapacity:Math.max(Number(inst.requiredCapacity)||0, Number(inst.minRoomCapacity)||0)}));
    const preferred = new Set(inst.preferredRoomIds || []);
    const neededCapacity = Math.max(Number(inst.requiredCapacity)||0, Number(inst.minRoomCapacity)||0);
    const capacityClass = (r) => {
      const cap = Number(r?.capacity) || 0;
      if (!neededCapacity || !cap || cap >= neededCapacity) return 0;
      if (cap >= neededCapacity * 0.75) return 1;
      return 2;
    };
    const result = opts.slice().sort((a,b) =>
      capacityClass(a) - capacityClass(b) ||
      (preferred.has(b.id)?1:0) - (preferred.has(a.id)?1:0) ||
      (Number(b.capacity)||0) - (Number(a.capacity)||0)
    );
    candidateRoomCache.set(inst.instId, result);
    return result;
  }

  // v63: временная блокировка конкретной даты для конкретной группы.
  // Если хотя бы одна неделя, на которую действует инстанс, попадает на заблокированную
  // дату этого дня недели, такой слот нельзя использовать в авторасстановке.
  function groupDayBlockedForInstance(gid, inst, day) {
    if (!config.semesterStart) return false;
    const blockedDates = blockedDatesByGroup.get(gid);
    if (!blockedDates?.size) return false;
    for (const w of weeksOf(inst)) {
      const date = addDays(addDays(mondayOf(config.semesterStart), (w - 1) * 7), day);
      if (blockedDates.has(date)) return true;
    }
    return false;
  }

  // v142: блокировка времени пары для группы.
  // date === "*" означает запрет номера пары на весь семестр во все учебные дни.
  // Старые точечные блокировки v140 (конкретная дата + номер пары) продолжают поддерживаться.
  // Это запрет только для автоматической расстановки: вручную пару поставить можно.
  function groupTimeSlotBlockedForInstance(gid, inst, day, period) {
    const blocks = data.groupSlotBlocks || [];
    if (!blocks.length) return false;
    const wanted = blocks.filter((b) => b.groupId === gid && Number(b.period) === Number(period));
    if (!wanted.length) return false;
    if (wanted.some((b) => b.date === "*" || b.allSemester === true)) return true;
    if (!config.semesterStart) return false;
    const blockedDates = new Set(wanted.map((b) => b.date).filter((date) => date && date !== "*"));
    for (const w of weeksOf(inst)) {
      const date = addDays(addDays(mondayOf(config.semesterStart), (w - 1) * 7), day);
      if (blockedDates.has(date)) return true;
    }
    return false;
  }

  // v67: заморозка дня — существующие пары восстанавливаются как locked,
  // а новые автоматические назначения в эту дату запрещены.
  function groupDayFrozenForInstance(gid, inst, day) {
    const freezes = data.groupDayFreezes || [];
    if (!freezes.length || !config.semesterStart) return false;
    const frozenDates = new Set(freezes.filter((b) => b.groupId === gid).map((b) => b.date));
    if (!frozenDates.size) return false;
    for (const w of weeksOf(inst)) {
      const date = addDays(addDays(mondayOf(config.semesterStart), (w - 1) * 7), day);
      if (frozenDates.has(date)) return true;
    }
    return false;
  }

  function groupSlotFree(inst, day, period, { allowFrozenDay = false, allowBlockedDay = false, allowBlockedSlot = false } = {}, half = null) {
    for (const gid of streamGroups(inst)) {
      if (!allowBlockedDay && groupDayBlockedForInstance(gid, inst, day)) return false;
      if (!allowBlockedSlot && groupTimeSlotBlockedForInstance(gid, inst, day, period)) return false;
      if (!allowFrozenDay && groupDayFrozenForInstance(gid, inst, day)) return false;
      const key = `${slotKey(day, period)}|${gid}`;
      const occupants = busyGroupOccupants.get(key);
      if (!occupants || occupants.size === 0) continue;
      for (const otherId of occupants) {
        const other = byInst[otherId];
        if (normalizedParticipantSubgroupsOverlap(data, other, inst, gid) && assignmentConflictsHalf(otherId, inst, half)) return false;
      }
    }
    return true;
  }

  function roomBlockedByPractice(inst, day, period, roomId) {
    if (!config.semesterStart || !roomId) return false;
    for (const w of weeksOf(inst)) {
      const date = addDays(addDays(mondayOf(config.semesterStart), (w - 1) * 7), day);
      if (practiceRoomSlots.has(`${date}|${Number(period)}|${roomId}`)) return true;
    }
    return false;
  }
  function freeRoomAt(inst, day, period, additionallyReserved = new Set(), half = null) {
    if (inst.format === "remote") return { id: null, name: "дистанционно", extraRoomId: null };
    const sk = slotKey(day, period);
    const candidates = candidateRooms(inst);
    const roomOccupied = (rid) => {
      const occupants = busyRoomOccupants.get(`${sk}|${rid}`);
      if (!occupants?.size) return false;
      for (const oid of occupants) {
        if (!assignmentConflictsHalf(oid, inst, half)) continue;
        const other = byInst[oid];
        if (sameTeacherSiblingSubgroupsCompatible(inst, other)) continue;
        return true;
      }
      return false;
    };
    const isFree = (r) => !roomOccupied(r.id) && !additionallyReserved.has(r.id) && !roomBlockedByPractice(inst, day, period, r.id);
    // v1619: если в этом слоте уже стоит вторая подгруппа той же группы у того
    // же преподавателя, это одно объединённое занятие — автоматически используем
    // ту же аудиторию, а не отправляем преподавателя в два кабинета одновременно.
    const teacherPartners = !inst.isVacancyTeacher ? [...(busyTeacherOccupants.get(`${sk}|${inst.teacherId}`) || [])]
      .filter((oid) => assignmentConflictsHalf(oid, inst, half) && sameTeacherSiblingSubgroupsCompatible(inst, byInst[oid])) : [];
    if (teacherPartners.length === 1) {
      const partnerRoomId = assignment?.[teacherPartners[0]]?.roomId;
      const sharedRoom = partnerRoomId ? candidates.find((r) => roomIdsEquivalent(rooms, r.id, partnerRoomId)) : null;
      if (sharedRoom && isFree(sharedRoom) && !inst.twoRooms) return { ...sharedRoom, extraRoomId: null };
    }
    for (const r of candidates) {
      if (!isFree(r)) continue;
      if (!inst.twoRooms) return { ...r, extraRoomId: null };
      for (const r2 of candidates) {
        if (r2.id === r.id || !isFree(r2)) continue;
        // v1599: суммарная вместимость двух кабинетов тоже мягкая.
        return { ...r, extraRoomId: r2.id };
      }
    }
    return null;
  }

  function assign(instId, day, period, roomId, extraRoomId = null, half = null) {
    const inst = byInst[instId];
    const sk = slotKey(day, period);
    for (const gid of streamGroups(inst)) {
      const gk = `${sk}|${gid}`;
      if (!busyGroupOccupants.has(gk)) busyGroupOccupants.set(gk, new Set());
      busyGroupOccupants.get(gk).add(instId);
      const gdk = dpKey(gid, day);
      if (!groupDayPeriods.has(gdk)) groupDayPeriods.set(gdk, new Set());
      groupDayPeriods.get(gdk).add(period);
      if (!groupDayInstances.has(gdk)) groupDayInstances.set(gdk, new Set());
      groupDayInstances.get(gdk).add(instId);
    }
    busyTeacher.add(inst.isVacancyTeacher ? `${sk}|t|vacancy|${instId}` : `${sk}|t|${inst.teacherId}`);
    if (!inst.isVacancyTeacher) {
      const tk = `${sk}|${inst.teacherId}`;
      if (!busyTeacherOccupants.has(tk)) busyTeacherOccupants.set(tk, new Set());
      busyTeacherOccupants.get(tk).add(instId);
    }
    for (const rid of [roomId, extraRoomId].filter(Boolean)) {
      busyRoom.add(`${sk}|r|${rid}`);
      const rk = `${sk}|${rid}`;
      if (!busyRoomOccupants.has(rk)) busyRoomOccupants.set(rk, new Set());
      busyRoomOccupants.get(rk).add(instId);
    }
    const tdk = dpKey(inst.teacherId, day);
    if (!teacherDayPeriods.has(tdk)) teacherDayPeriods.set(tdk, new Set());
    teacherDayPeriods.get(tdk).add(period);
    if (!teacherDayInstances.has(tdk)) teacherDayInstances.set(tdk, new Set());
    teacherDayInstances.get(tdk).add(instId);
    assignment[instId] = { day, period, roomId, extraRoomId, half: inst.halfPair ? (half ?? 0) : null };
  }

  function unassign(instId) {
    const inst = byInst[instId];
    const a = assignment[instId];
    if (!a) return;
    const sk = slotKey(a.day, a.period);
    for (const gid of streamGroups(inst)) {
      busyGroupOccupants.get(`${sk}|${gid}`)?.delete(instId);
      groupDayPeriods.get(dpKey(gid, a.day))?.delete(a.period);
      groupDayInstances.get(dpKey(gid, a.day))?.delete(instId);
    }
    busyTeacher.delete(inst.isVacancyTeacher ? `${sk}|t|vacancy|${instId}` : `${sk}|t|${inst.teacherId}`);
    if (!inst.isVacancyTeacher) busyTeacherOccupants.get(`${sk}|${inst.teacherId}`)?.delete(instId);
    for (const rid of assignmentRoomIds(a)) {
      const rk = `${sk}|${rid}`;
      busyRoomOccupants.get(rk)?.delete(instId);
      if (!busyRoomOccupants.get(rk)?.size) busyRoom.delete(`${sk}|r|${rid}`);
    }
    teacherDayPeriods.get(dpKey(inst.teacherId, a.day))?.delete(a.period);
    teacherDayInstances.get(dpKey(inst.teacherId, a.day))?.delete(instId);
    delete assignment[instId];
  }

  function slotFreeForOwners(inst, day, period, { allowUnavailableTeacher = false, allowFrozenDay = false, allowBlockedDay = false, allowBlockedSlot = false } = {}, half = null) {
    // v1603: reject any week+day combination whose actual calendar date falls
    // outside [semesterStart, semesterEnd] — see weekDayWithinSemester above.
    // This is an absolute constraint (no allow* override): a date outside the
    // semester isn't a soft preference to avoid, it's not a teaching day at
    // all, so there's no "last resort" sense in which placing there is ever
    // valid.
    for (const week of weeksOf(inst)) {
      if (!weekDayWithinSemester(config, week, day)) return false;
    }
    if (!groupSlotFree(inst, day, period, { allowFrozenDay, allowBlockedDay, allowBlockedSlot }, half)) return false;
    if (!inst.isVacancyTeacher) {
      const teacherOccupants = busyTeacherOccupants.get(`${slotKey(day, period)}|${inst.teacherId}`);
      if (teacherOccupants?.size) {
        const overlapping = [...teacherOccupants].filter((oid) => assignmentConflictsHalf(oid, inst, half));
        if (overlapping.length > 1) return false;
        if (overlapping.length === 1 && !sameTeacherSiblingSubgroupsCompatible(inst, byInst[overlapping[0]])) return false;
      }
    }
    // v104: для штатного преподавателя отмеченная недоступность — МЯГКОЕ ограничение.
    // Автогенератор сначала избегает таких слотов, но может использовать их как
    // последний резерв, если иначе нагрузка останется неразмещённой. Для
    // совместителей/ГПХ недоступность остаётся абсолютным запретом.
    const slotTeacher = teacherById[inst.teacherId];
    if (!allowUnavailableTeacher && !inst.isVacancyTeacher && isTeacherUnavailableForWeeks(slotTeacher, day, period, inst.weeks) && !isStaffTeacher(slotTeacher)) return false;
    if (!dayFormatOk(inst, day)) return false;
    return true;
  }

  // ---- pre-place locked instances (manual pins / kept-from-previous-run) ----
  // v1600: a locked instance is a hard promise to the user ("алгоритм не
  // трогает закреплённые вручную пары"). Previously, if restoring it at its
  // exact prior slot failed for any reason (config changed under it, its slot
  // is no longer valid, a conflicting locked instance already claimed the
  // room/time), the code just `continue`d — which left it OFF `lockedIds`
  // entirely, so it fell through into the normal placement pool below and the
  // algorithm was free to move it. That is exactly how a "закреплённое"
  // lesson could end up somewhere else after a recalculation. Now, whenever
  // restoration fails, the instance is still added to `lockedIds` (so the
  // main loop can never touch it) but is left WITHOUT an assignment — it
  // surfaces as a visible unplaced/conflict item for the user to resolve
  // manually instead of being silently relocated.
  const lockedIds = new Set();
  if (prior && prior.locked && prior.assignment) {
    for (const instId of prior.locked) {
      if (!instIdSet.has(instId)) continue;
      const a = prior.assignment[instId];
      if (!a) continue;
      const inst = byInst[instId];
      if (!activeDays.includes(a.day) || a.period >= periodsPerDay) { lockedIds.add(instId); continue; }
      if (!slotFreeForOwners(inst, a.day, a.period, { allowUnavailableTeacher: true, allowFrozenDay: true, allowBlockedDay: false, allowBlockedSlot: true })) { lockedIds.add(instId); continue; }
      if (inst.format === "remote") {
        assign(instId, a.day, a.period, null, null, a.half ?? null);
        lockedIds.add(instId);
        continue;
      }
      const room = rooms.find((r) => r.id === a.roomId);
      const roomOk = room &&
        (inst.dedicatedRoomId ? a.roomId === inst.dedicatedRoomId :
          (inst.allowedRoomIds && inst.allowedRoomIds.length > 0) ? inst.allowedRoomIds.includes(a.roomId) :
          (inst.roomType === "любая" || room.typeId === inst.roomType)) &&
        (!inst.requiresComputer || room.hasComputers) &&
        (!inst.requiresArt || room.isArtRoom) &&
        !busyRoom.has(`${slotKey(a.day, a.period)}|r|${a.roomId}`) && (!a.extraRoomId || !busyRoom.has(`${slotKey(a.day, a.period)}|r|${a.extraRoomId}`));
      if (!roomOk) { lockedIds.add(instId); continue; }
      assign(instId, a.day, a.period, a.roomId, a.extraRoomId || null, a.half ?? null);
      lockedIds.add(instId);
    }
  }

  function loadCountOnDay(loadId, day, excludeInstId) {
    let c = 0;
    for (const instId of (loadInstanceIds.get(loadId) || [])) {
      if (instId === excludeInstId) continue;
      const a = assignment[instId];
      if (a && a.day === day) c++;
    }
    return c;
  }

  function gapCountForSet(set) {
    if (!set || set.size <= 1) return 0;
    const arr = [...set];
    return Math.max(...arr) - Math.min(...arr) + 1 - set.size;
  }

  function projectedGapDelta(set, period) {
    if (!set || set.size === 0 || set.has(period)) return 0;
    const before = gapCountForSet(set);
    const next = new Set(set); next.add(period);
    return gapCountForSet(next) - before;
  }

  // v66: все проверки компактности считаются ПО КОНКРЕТНОЙ НЕДЕЛЕ.
  // Числитель и знаменатель — две независимые сетки. Пара, существующая только
  // на нечётных неделях, не должна создавать «фантомное окно» на чётных и наоборот.
  function groupWeekDayPeriods(gid, week, day, excludeInstId = null) {
    const set = new Set();
    for (const other of instances) {
      if (other.instId === excludeInstId || !belongsToGroup(other, gid)) continue;
      if (!weeksOf(other).includes(week)) continue;
      const a = assignment[other.instId];
      if (a && a.day === day) set.add(a.period);
    }
    return set;
  }
  // v103: реальная траектория студента внутри группы. В системе всегда две
  // подгруппы: занятие всей группы относится к обеим, подгрупповое — только к своей.
  // Это нужно и для дневного максимума, и для подсчёта окон: объединение п/г 1 и п/г 2
  // в один Set скрывало реальные окна студентов.
  function groupSubgroupWeekDayPeriods(gid, subgroup, week, day, excludeInstId = null) {
    const set = new Set();
    for (const other of instances) {
      if (other.instId === excludeInstId || !belongsToGroup(other, gid)) continue;
      if (!weeksOf(other).includes(week)) continue;
      const subs = subgroupsForGroup(other, gid);
      const affects = subs.some((sg) => Number(sg) === 0 || Number(sg) === Number(subgroup));
      if (!affects) continue;
      const a = assignment[other.instId];
      if (a && a.day === day) set.add(a.period);
    }
    return set;
  }
  function affectedSubgroupsForGroup(inst, gid) {
    const subs = subgroupsForGroup(inst, gid);
    if (subs.some((sg) => Number(sg) === 0)) return [1, 2];
    const out = [...new Set(subs.map((sg) => Number(sg)).filter((sg) => sg === 1 || sg === 2))];
    return out.length ? out : [1, 2];
  }
  function teacherWeekDayPeriods(teacherId, week, day, excludeInstId = null) {
    const set = new Set();
    for (const other of instances) {
      if (other.instId === excludeInstId || other.teacherId !== teacherId) continue;
      if (!weeksOf(other).includes(week)) continue;
      const a = assignment[other.instId];
      if (a && a.day === day) set.add(a.period);
    }
    return set;
  }
  function groupHasPhysicalEducationOnWeekDay(gid, week, day, prospectiveInst = null) {
    if (prospectiveInst && belongsToGroup(prospectiveInst, gid) &&
        weeksOf(prospectiveInst).includes(week) &&
        isPhysicalEducationName(subjectById[prospectiveInst.subjectId]?.name)) return true;
    for (const other of instances) {
      if (!belongsToGroup(other, gid) || !weeksOf(other).includes(week)) continue;
      const a = assignment[other.instId];
      if (a && a.day === day && isPhysicalEducationName(subjectById[other.subjectId]?.name)) return true;
    }
    return false;
  }

  // v99: дневной максимум для АВТОРАССТАНОВКИ берётся только из раздела
  // «Ограничения нагрузки» — отдельно для курса и базы поступления группы.
  // Никакого скрытого правила «один раз в неделю +1» нет. Единственное
  // дополнительное исключение — физкультура, и только если соответствующий
  // флажок включён в ограничениях: в день с физкультурой допускается +1,
  // но общий предел всё равно не выше 5 пар. Ручной режим может превышать
  // эти значения после предупреждения.
  function dailyLoadLimitsOk(inst, day, period) {
    const weeks = weeksOf(inst);
    for (const gid of streamGroups(inst)) {
      const group = groupById[gid];
      if (!group) continue;
      const rule = dailyLoadRuleForGroup(config, group);
      const baseMax = Math.min(Number(config.periodsPerDay) || 6, Math.max(1, Number(rule.maxPairs) || 5));
      const affectedSubs = affectedSubgroupsForGroup(inst, gid);
      for (const week of weeks) {
        for (const subgroup of affectedSubs) {
          const set = groupSubgroupWeekDayPeriods(gid, subgroup, week, day, inst.instId);
          const nextSize = set.has(period) ? set.size : set.size + 1;
          const peExtra = config.peExtraPairAllowed !== false &&
            groupHasPhysicalEducationOnWeekDay(gid, week, day, inst) ? 1 : 0;
          const allowedMax = Math.min(Number(config.periodsPerDay) || 6, 5, baseMax + peExtra);
          if (nextSize > allowedMax) return false;
        }
      }
    }
    return true;
  }
  function dailyLoadLimitsOkForPeriods(inst, day, periods) {
    const addPeriods = [...new Set(periods.map(Number))];
    const weeks = weeksOf(inst);
    for (const gid of streamGroups(inst)) {
      const group = groupById[gid];
      if (!group) continue;
      const rule = dailyLoadRuleForGroup(config, group);
      const baseMax = Math.min(Number(config.periodsPerDay) || 6, Math.max(1, Number(rule.maxPairs) || 5));
      for (const week of weeks) {
        for (const subgroup of affectedSubgroupsForGroup(inst, gid)) {
          const set = groupSubgroupWeekDayPeriods(gid, subgroup, week, day, null);
          addPeriods.forEach((p) => set.add(p));
          const peExtra = config.peExtraPairAllowed !== false && groupHasPhysicalEducationOnWeekDay(gid, week, day, inst) ? 1 : 0;
          const allowedMax = Math.min(Number(config.periodsPerDay) || 6, 5, baseMax + peExtra);
          if (set.size > allowedMax) return false;
        }
      }
    }
    return true;
  }

  // v69: «первая/последняя пара» означает край фактического учебного блока дня,
  // а НЕ фиксированные номера 1 и 6. Например, для блока 3–5 первой является
  // 3-я пара, последней — 5-я. Правило проверяется отдельно для каждой недели.
  function dailyEdgePositionOk(inst, day, period) {
    const weeks = weeksOf(inst);
    for (const gid of streamGroups(inst)) {
      for (const week of weeks) {
        let minOther = Infinity;
        let maxOther = -Infinity;
        for (const other of instances) {
          if (other.instId === inst.instId || !belongsToGroup(other, gid)) continue;
          if (!weeksOf(other).includes(week)) continue;
          const a = assignment[other.instId];
          if (!a || a.day !== day) continue;
          minOther = Math.min(minOther, a.period);
          maxOther = Math.max(maxOther, a.period);
          // Уже поставленная «первая в дне» должна остаться на левом краю блока.
          if (other.position === "first" && period < a.period && inst.position !== "first") return false;
          // Уже поставленная «последняя в дне» должна остаться на правом краю блока.
          if (other.position === "last" && period > a.period && inst.position !== "last") return false;
        }
        if (inst.position === "first" && minOther !== Infinity && period > minOther) return false;
        if (inst.position === "last" && maxOther !== -Infinity && period < maxOther) return false;
        // 1 ак.ч. (0,5 пары) должен находиться только на краю фактического
        // учебного блока дня. Иначе студенту пришлось бы уходить/возвращаться
        // посреди дня, а половинка визуально превращалась бы в окно.
        if (inst.halfPair && minOther !== Infinity && period > minOther && period < maxOther) return false;
        // Если уже поставленная половинка перестанет быть первой/последней после
        // добавления новой пары, такой кандидат также запрещаем.
        for (const other of instances) {
          if (!other.halfPair || other.instId === inst.instId || !belongsToGroup(other, gid) || !weeksOf(other).includes(week)) continue;
          const oa = assignment[other.instId];
          if (!oa || oa.day !== day) continue;
          let hasBefore = false, hasAfter = false;
          for (const third of instances) {
            if (third.instId === other.instId || third.instId === inst.instId || !belongsToGroup(third, gid) || !weeksOf(third).includes(week)) continue;
            const ta = assignment[third.instId];
            if (!ta || ta.day !== day) continue;
            if (ta.period < oa.period) hasBefore = true;
            if (ta.period > oa.period) hasAfter = true;
          }
          if (period < oa.period) hasBefore = true;
          if (period > oa.period) hasAfter = true;
          if (hasBefore && hasAfter) return false;
        }
      }
    }
    return true;
  }

  // v58: окна у группы — абсолютный hard constraint авторасстановки.
  // Если день группы сейчас непрерывен, после добавления он ОБЯЗАН остаться непрерывным.
  // Если разрыв уже создан ручными закреплениями, автомат может только уменьшить его
  // (например, заполнить пустую пару), но не сохранять разрыв добавлением занятий сбоку.
  // Аналогично не создаём новые окна преподавателю.
  function candidateDoesNotCreateWindows(inst, day, period) {
    // v98: окна остаются мягкой целью оптимизации, но дневной максимум группы
    // снова проверяется по дневному максимуму из «Ограничений нагрузки»
    // (с опциональным +1 только для дня с физкультурой).
    if (!dailyLoadLimitsOk(inst, day, period)) return false;
    if (!dailyEdgePositionOk(inst, day, period)) return false;
    if (!lecturePracticeOrderOk(inst, day, period)) return false;
    return true;
  }

  // Перестановка уже размещённой пары тоже не должна оставлять после себя окно.
  // Без этой проверки оптимизатор мог снять внутреннюю пару из блока 2–5 и
  // перенести её в другой день, оставив 2,4,5 — именно такой сценарий v58 запрещает.
  function removalDoesNotCreateWindows(inst, day, period) {
    // v89: промежуточное ухудшение компактности разрешено локальному поиску.
    // Итоговое решение выбирается по cost(), где окна группы имеют очень высокий штраф.
    return true;
  }

  // v97: целевой профиль дня — не "набить один день до вечера", а
  // распределить занятия по неделе равномерно и по возможности начинать около 10:00.
  // Внутри допустимого диапазона предпочтителен средний объём (обычно 3–4 пары),
  // поэтому день из 5–6 пар больше не выигрывает только за счёт меньшего числа учебных дней.
  function groupDayLoadPenalty(size, gid) {
    if (size <= 0) return 0;
    const rule = dailyLoadRuleForGroup(config, groupById[gid]);
    const minTarget = Math.max(1, Number(rule.minPairs) || 2);
    const maxTarget = Math.max(minTarget, Number(rule.maxPairs) || 5);
    const ideal = Math.min(4, Math.max(minTarget, Math.round((minTarget + maxTarget) / 2)));
    if (size < minTarget) return (minTarget - size) * 18000 + (size === 1 ? 45000 : 0);
    if (size <= maxTarget) {
      const d = size - ideal;
      return d * d * 900;
    }
    // В авторасстановке превышение ограничено правилом одного расширенного дня.
    // Этот штраф нужен, чтобы даже допустимый 4-й слот использовался только при пользе.
    const over = size - maxTarget;
    return (maxTarget - ideal) * (maxTarget - ideal) * 900 + over * over * 8000;
  }

  // Предпочтение по времени: 2-я пара — естественное начало дня (~10:00).
  // 3–4-я пары также хорошие; 1-я и особенно 6-я допустимы, но без необходимости
  // генератор не должен сдвигать весь блок к краям дня.
  function preferredPeriodPenalty(period) {
    const p = Number(period);
    if (p === 1) return 0;
    if (p === 2) return 180;
    if (p === 3) return 420;
    if (p === 4) return 950;
    if (p === 0) return 1400;
    if (p >= 5) return 2600 + (p - 5) * 1200;
    return 0;
  }

  function dayStartPenaltyForSet(set) {
    if (!set || !set.size) return 0;
    const minP = Math.min(...set);
    const maxP = Math.max(...set);
    let penalty = 0;
    // Лучшее начало — 2-я пара. Начало с 3–4-й сильнее штрафуется, чем 1-я:
    // пользователь явно предпочитает расписание "с 10 утра", а не поздние блоки.
    if (minP === 0) penalty += 900;
    else if (minP === 1) penalty += 0;
    else if (minP === 2) penalty += 1500;
    else if (minP === 3) penalty += 3500;
    else penalty += 6500 + (minP - 4) * 2000;
    if (maxP >= 5) penalty += 1800;
    return penalty;
  }

  function projectedGroupDayLoadDelta(inst, day, period) {
    let delta = 0, samples = 0;
    const weeks = weeksOf(inst);
    for (const gid of streamGroups(inst)) {
      for (const week of weeks) {
        for (const subgroup of affectedSubgroupsForGroup(inst, gid)) {
          const set = groupSubgroupWeekDayPeriods(gid, subgroup, week, day, inst.instId);
          if (set.has(period)) continue;
          delta += groupDayLoadPenalty(set.size + 1, gid) - groupDayLoadPenalty(set.size, gid);
          samples++;
        }
      }
    }
    return samples ? delta / samples : delta;
  }

  function lessonOrderValue(day, period) {
    const dayIndex = activeDays.indexOf(day);
    return (dayIndex < 0 ? Number(day) || 0 : dayIndex) * periodsPerDay + period;
  }
  function isLectureInst(inst) { return isLectureLessonTypeName(lessonTypeById[inst?.typeId]?.name); }
  function isPracticalInst(inst) { return isPracticalLessonTypeName(lessonTypeById[inst?.typeId]?.name); }
  function sameSubjectParticipantsRegardlessWeeks(a, b) {
    if (!a || !b || !a.subjectId || a.subjectId !== b.subjectId) return false;
    for (const gid of streamGroups(a)) {
      if (!belongsToGroup(b, gid)) continue;
      if (normalizedParticipantSubgroupsOverlap(data, a, b, gid)) return true;
    }
    return false;
  }
  function sameSubjectParticipantsOverlap(a, b) {
    return sameSubjectParticipantsRegardlessWeeks(a, b) && weeksOverlap(a, b, config);
  }
  function occurrenceOrder(inst, day, period, pick = "min") {
    const weeks = weeksOf(inst);
    if (!weeks.length) return Infinity;
    const vals = weeks.map((week) => ((week - 1) * activeDays.length + Math.max(0, activeDays.indexOf(day))) * periodsPerDay + period);
    return pick === "max" ? Math.max(...vals) : Math.min(...vals);
  }
  function isRemoteLectureLead(inst) {
    if (!isLectureInst(inst) || inst?.format !== "remote") return false;
    // v1598: для комбинированной лекционной нагрузки первая ДО-пара является
    // обязательной потоковой вводной частью. Практику нельзя ставить раньше неё.
    return !!inst.mandatoryRemoteAssembly || (inst.streamGroupIds || []).length > 0 || (inst.streamParticipants || []).length > 0;
  }
  function lecturePracticeOrderOk(inst, day, period) {
    if (!isLectureInst(inst) && !isPracticalInst(inst)) return true;
    const candidateFirst = occurrenceOrder(inst, day, period, "min");
    const relevantLectures = instances.filter((other) => other.instId !== inst.instId && isLectureInst(other) && sameSubjectParticipantsRegardlessWeeks(inst, other));
    const remoteLeadLectures = relevantLectures.filter(isRemoteLectureLead);
    const inPersonLectures = relevantLectures.filter((other) => other.format !== "remote");

    // v1599: для комбинированной лекционной нагрузки порядок именно такой:
    // ДО-потоковая Лек -> очная Лек конкретной группы. Практика — дополнительный
    // следующий этап только если она реально есть в учебном плане; никакой Пр
    // искусственно не создаётся и отсутствие практики ничего не блокирует.
    if (isLectureInst(inst)) {
      if (inst.format !== "remote" && remoteLeadLectures.length) {
        const remoteOrders = remoteLeadLectures.map((other) => {
          const a = assignment[other.instId];
          return a ? occurrenceOrder(other, a.day, a.period, "min") : Infinity;
        });
        const remoteFirst = Math.min(...remoteOrders);
        if (!Number.isFinite(remoteFirst) || candidateFirst <= remoteFirst) return false;
      }
      if (isRemoteLectureLead(inst)) {
        const assignedInPerson = inPersonLectures.map((other) => {
          const a = assignment[other.instId];
          return a ? occurrenceOrder(other, a.day, a.period, "min") : Infinity;
        }).filter(Number.isFinite);
        if (assignedInPerson.length && candidateFirst >= Math.min(...assignedInPerson)) return false;
      }
      const assignedPracticals = instances.filter((other) => other.instId !== inst.instId && isPracticalInst(other) && sameSubjectParticipantsRegardlessWeeks(inst, other))
        .map((other) => { const a = assignment[other.instId]; return a ? occurrenceOrder(other, a.day, a.period, "min") : Infinity; })
        .filter(Number.isFinite);
      if (assignedPracticals.length && candidateFirst >= Math.min(...assignedPracticals)) return false;
    }

    if (isPracticalInst(inst)) {
      if (!relevantLectures.length) return true;
      // Если есть ДО-потоковая + очная лекционная части, практика ждёт обеих.
      // Если есть только лекции и практики нет — этот код, естественно, не вызывается.
      const gates = remoteLeadLectures.length && inPersonLectures.length
        ? [...remoteLeadLectures, ...inPersonLectures]
        : (remoteLeadLectures.length ? remoteLeadLectures : relevantLectures);
      const gateOrders = gates.map((other) => {
        const a = assignment[other.instId];
        return a ? occurrenceOrder(other, a.day, a.period, "min") : Infinity;
      });
      if (gateOrders.some((x) => !Number.isFinite(x))) return false;
      const lastRequiredLecture = Math.max(...gateOrders);
      if (candidateFirst <= lastRequiredLecture) return false;
    }
    return true;
  }
  function lecturePracticeAdjacencyBonus(inst, day, period) {
    let bonus = 0;
    if (!isLectureInst(inst) && !isPracticalInst(inst)) return bonus;
    for (const other of instances) {
      if (other.instId === inst.instId || !sameSubjectParticipantsOverlap(inst, other)) continue;
      const a = assignment[other.instId];
      if (!a || a.day !== day) continue;
      if (isPracticalInst(inst) && isLectureInst(other) && a.period === period - 1) bonus -= 120;
      if (isLectureInst(inst) && isPracticalInst(other) && a.period === period + 1) bonus -= 120;
    }
    return bonus;
  }

  // v66: если лекция и практика одной дисциплины чередуются через неделю,
  // они являются двумя вариантами ОДНОГО места в двухнедельном шаблоне.
  // Предпочтительно: тот же день + та же пара (нечёт: лекция, чёт: практика
  // или наоборот). Это также позволяет каждой неделе оставаться компактной без окон.
  function alternatingLecturePracticeSlotPenalty(inst, day, period) {
    if (!isLectureInst(inst) && !isPracticalInst(inst)) return 0;
    const myWeeks = new Set(weeksOf(inst));
    if (!myWeeks.size) return 0;
    let score = 0;
    for (const other of instances) {
      if (other.instId === inst.instId || !sameSubjectParticipantsRegardlessWeeks(inst, other)) continue;
      const oppositeTypes = (isLectureInst(inst) && isPracticalInst(other)) || (isPracticalInst(inst) && isLectureInst(other));
      if (!oppositeTypes) continue;
      const otherWeeks = weeksOf(other);
      if (!otherWeeks.length || otherWeeks.some((w) => myWeeks.has(w))) continue;
      const myParities = new Set([...myWeeks].map((w)=>parityForWeekNumber(config,w)));
      const otherParities = new Set(otherWeeks.map((w)=>parityForWeekNumber(config,w)));
      if ([...myParities].some((p)=>otherParities.has(p))) continue;
      const a = assignment[other.instId];
      if (!a) continue;
      if (a.day === day && a.period === period) score -= 4200;
      else if (a.day === day) score += 500 + Math.abs(a.period - period) * 220;
      else score += 1200;
    }
    return score;
  }

  // v62: стабильность ДВУХНЕДЕЛЬНОГО шаблона (числитель/знаменатель).
  // Нечётные недели сравниваются только с нечётными, чётные — только с чётными.
  // Поэтому 1-я неделя стремится повторять 3-ю/5-ю, а 2-я — 4-ю/6-ю.
  // Числитель и знаменатель могут иметь разные устойчивые сетки. Это soft
  // constraint: праздники, практики, недоступность и прочие hard constraints важнее.
  function weeklyStabilitySlotPenalty(inst, day, period) {
    const total = totalSemesterWeeks(config);
    if (!total) return 0;
    const targetWeeks = weeksOf(inst);
    if (!targetWeeks.length) return 0;
    let score = 0;
    for (const gid of streamGroups(inst)) {
      for (const week of targetWeeks) {
        const parity = parityForWeekNumber(config, week);
        // v63: шаблон строится только вперёд по времени. Более поздняя неделя
        // подстраивается под уже сформированную предыдущую неделю той же чётности,
        // а не наоборот. Это не даёт расписанию «скакать назад» при добавлении нагрузки.
        const neighbors = [];
        for (let w = week - 1; w >= 1; w--) {
          if (parityForWeekNumber(config, w) === parity) { neighbors.push(w); break; }
        }
        for (const neighbor of neighbors) {
          let exact = false, sameDay = false, nearest = Infinity;
          let sameLoadExact = false, sameLoadDay = false;
          for (const other of instances) {
            if (other.instId === inst.instId || !belongsToGroup(other, gid)) continue;
            if (!weeksOf(other).includes(neighbor)) continue;
            const a = assignment[other.instId];
            if (!a) continue;
            const sameLoad = other.loadId === inst.loadId;
            if (a.day === day) {
              sameDay = true;
              nearest = Math.min(nearest, Math.abs(a.period - period));
              if (sameLoad) sameLoadDay = true;
              if (a.period === period) {
                exact = true;
                if (sameLoad) sameLoadExact = true;
              }
            }
          }
          // Самая сильная цель — та же нагрузка в том же слоте через две недели.
          if (sameLoadExact) score -= 900;
          else if (sameLoadDay) score -= 260;
          else if (exact) score -= 170;
          else if (sameDay) score += Math.min(70, nearest * 20);
          else score += 35;
        }
      }
    }
    return score;
  }

  function remoteDaysForGroupWeek(gid, week, excludeId = null) {
    const days = new Set();
    for (const other of instances) {
      if (excludeId && other.instId === excludeId) continue;
      if (other.format !== "remote" || !belongsToGroup(other, gid)) continue;
      if (!weeksOf(other).includes(week)) continue;
      const a = assignment[other.instId];
      if (a) days.add(a.day);
    }
    return days;
  }

  function preferredRemoteDaysForGroup(gid) {
    const g = groups.find((x) => x.id === gid);
    if (!g) return 0;
    return clamp(Math.round(Number(dailyLoadRuleForGroup(config, g).minRemoteDays) || 0), 0, Math.min(7, activeDays.length));
  }

  function remoteDayDeficitTotal() {
    // Это только мягкая метрика качества: расписание может не достичь цели, если другие условия важнее.
    // v1569: при пересчёте одной группы остальные 85+ групп неизменяемы, поэтому
    // их ДО-дефицит является константой и не должен пересчитываться при каждом cost().
    let deficit = 0;
    const totalWeeks = totalSemesterWeeks(config);
    const metricGroups = scopedGroupId ? groups.filter((g) => g.id === scopedGroupId) : groups;
    for (const g of metricGroups) {
      const minimum = preferredRemoteDaysForGroup(g.id);
      if (!minimum) continue;
      for (let week = 1; week <= totalWeeks; week++) {
        const remotePlanned = instances.some((i) => i.format === "remote" && belongsToGroup(i, g.id) && weeksOf(i).includes(week));
        if (!remotePlanned) continue;
        const actual = remoteDaysForGroupWeek(g.id, week).size;
        deficit += Math.max(0, minimum - actual);
      }
    }
    return deficit;
  }

  function remoteDaySpreadScore(inst, day) {
    if (inst.format !== "remote") return 0;
    let score = 0, samples = 0;
    for (const gid of streamGroups(inst)) {
      const minimum = preferredRemoteDaysForGroup(gid);
      if (!minimum) continue;
      for (const week of weeksOf(inst)) {
        const days = remoteDaysForGroupWeek(gid, week, inst.instId);
        // Мягкое предпочтение: новый ДО-день лучше, пока цель не достигнута, но это не блокирует другие решения.
        if (days.size < minimum) score += days.has(day) ? 4500 : -4500;
        else score += days.has(day) ? -250 : 450;
        samples++;
      }
    }
    return samples ? score / samples : 0;
  }

  function scoreSlot(inst, day, period) {
    let score = 0;

    // v70: оцениваем компактность ПО КАЖДОЙ ФАКТИЧЕСКОЙ НЕДЕЛЕ занятия.
    // Раньше soft-score использовал объединённую сетку всего семестра и мог считать
    // пары другой чётности соседними. Это приводило к странным решениям вида 1–3 + 5.
    const targetWeeks = weeksOf(inst);
    let groupScore = 0, teacherScore = 0, groupSamples = 0, teacherSamples = 0;
    for (const gid of streamGroups(inst)) {
      for (const week of targetWeeks) {
        const gp = groupWeekDayPeriods(gid, week, day, inst.instId);
        const gEmpty = gp.size === 0;
        // v72: ещё сильнее собираем занятия группы в непрерывные блоки.
        // Новый учебный день заметно дороже пристыковки к уже существующему блоку;
        // заполнение внутреннего разрыва получает максимальный приоритет.
        if (gEmpty) groupScore += 120000;
        // v93: все учебные дни Пн–Сб равноправны.
        // Отдельного штрафа за пятницу/субботу нет: новый день оценивается
        // только по общей компактности, окнам и доступности ресурсов.
        if (gp.has(period - 1) || gp.has(period + 1)) groupScore -= 7000;
        // Заполнение внутреннего окна должно доминировать почти над любым soft-критерием.
        groupScore += projectedGapDelta(gp, period) * 60000;
        groupSamples++;
      }
    }
    for (const week of targetWeeks) {
      const tp = teacherWeekDayPeriods(inst.teacherId, week, day, inst.instId);
      const tEmpty = tp.size === 0;
      if (tEmpty) teacherScore += 1800;
      if (tp.has(period - 1) || tp.has(period + 1)) teacherScore -= 900;
      teacherScore += projectedGapDelta(tp, period) * 9000;
      teacherSamples++;
    }
    score += groupSamples ? groupScore / groupSamples : 0;
    score += teacherSamples ? teacherScore / teacherSamples : 0;
    // projectedGroupDayLoadDelta уже сам усредняет недели и остаётся отдельным весом.
    score += projectedGroupDayLoadDelta(inst, day, period);

    score += loadCountOnDay(inst.loadId, day, inst.instId) * (inst.pairing === "single" ? 200 : 4);
    // v104: закрытый слот штатного преподавателя — аварийный резерв. Штраф
    // намеренно на порядки выше обычных soft-критериев (окна, время начала,
    // стабильность), но всё же ниже стоимости неразмещённой пары.
    const scoreTeacher = teacherById[inst.teacherId];
    if (!inst.isVacancyTeacher && isStaffTeacher(scoreTeacher) && isTeacherUnavailableForWeeks(scoreTeacher, day, period, inst.weeks)) score += 100000000;
    // v69: не привязываем «первую/последнюю» к номерам 1 и 6.
    // Их положение относительно остальных занятий дня обеспечивается hard constraint выше.
    // Порядок «лекция → практика» уже проверяется как hard constraint при выборе слота.
    // Здесь остаётся бонус за соседство: если возможно, ставим их подряд.
    score += lecturePracticeAdjacencyBonus(inst, day, period);
    score += alternatingLecturePracticeSlotPenalty(inst, day, period);
    score += weeklyStabilitySlotPenalty(inst, day, period);
    // v97: при прочих равных строим день от ~10:00, а не сдвигаем блок на 4–6 пары.
    score += preferredPeriodPenalty(period);
    score += remoteDaySpreadScore(inst, day);
    return score;
  }

  // v77: самые дефицитные занятия строим первыми.
  // Главный сигнал — реальная доступность преподавателя, затем количество
  // допустимых слотов с учётом группы/календаря/окон и число подходящих аудиторий.
  // Это снижает ситуацию, когда гибкие нагрузки занимают последние доступные
  // места преподавателя с узкой доступностью.
  const teacherAvailabilityCache = new Map();
  function teacherAvailabilityProfile(inst) {
    if (inst.isVacancyTeacher) return { slots: activeDays.length * periodsPerDay + 1000, days: activeDays.length + 1000, closedDays: 0 };
    const parityKey = [...new Set((inst.weeks || []).map((w) => Number(w) % 2 ? "o" : "e"))].sort().join("") || "all";
    const key = `${inst.teacherId || `vacancy:${inst.instId}`}|${parityKey}`;
    if (teacherAvailabilityCache.has(key)) return teacherAvailabilityCache.get(key);
    const teacher = teacherById[inst.teacherId];
    let slots = 0, days = 0, closedDays = 0;
    for (const day of activeDays) {
      let daySlots = 0;
      for (let period = 0; period < periodsPerDay; period++) {
        if (!isTeacherUnavailableForWeeks(teacher, day, period, inst.weeks)) { slots++; daySlots++; }
      }
      if (daySlots > 0) days++;
      else closedDays++;
    }
    const profile = { slots, days, closedDays };
    teacherAvailabilityCache.set(key, profile);
    return profile;
  }
  function teacherAvailabilityCount(inst) { return teacherAvailabilityProfile(inst).slots; }

  // v103: MRV пересчитывается на ТЕКУЩЕМ состоянии расписания.
  // Кеш здесь вреден: после каждой поставленной пары дефицит остальных меняется.
  function dynamicFeasibleSlotCount(inst) {
    let count = 0;
    for (const day of activeDays) {
      for (let period = 0; period < periodsPerDay; period++) {
        const halves = inst.halfPair ? [0,1] : [null];
        if (!halves.some((h) => slotFreeForOwners(inst, day, period, {}, h) && (inst.format === "remote" || freeRoomAt(inst, day, period, new Set(), h)))) continue;
        // MRV должен отражать нормальную доступность преподавателя, а не резервные
        // жёлтые слоты штатника. Иначе штатник с закрытыми часами выглядел бы
        // искусственно более свободным и потерял бы приоритет.
        if (!inst.isVacancyTeacher && isTeacherUnavailableForWeeks(teacherById[inst.teacherId], day, period, inst.weeks)) continue;
        if (!candidateDoesNotCreateWindows(inst, day, period)) continue;
        count++;
      }
    }
    return count;
  }

  // v1577: dependencyRank only depends on static instance fields (type, subject,
  // participants) that never change while generateSchedule runs — only the
  // assignment map changes during placement. It was being recomputed from
  // scratch (including an O(n) scan over ALL instances via `.some()`) on every
  // single comparison inside every `.sort(compareByScarcity)` call, which adds
  // up fast for a full multi-group semester. Cache it per instId instead.
  const dependencyRankCache = new Map();
  function dependencyRank(inst) {
    const cached = dependencyRankCache.get(inst.instId);
    if (cached !== undefined) return cached;
    // v79: контрольные и формы зачёта всегда обрабатываются ПОСЛЕ обычных
    // учебных занятий. Они не должны отбирать дефицитные слоты у лекций,
    // практик и лабораторных даже при очень низкой доступности преподавателя.
    const typeName = lessonTypeById[inst.typeId]?.name || "";
    let rank;
    if (isControlLessonTypeName(typeName) || isCreditLessonTypeName(typeName)) rank = 2;
    // Практику/лабораторную по дисциплине с лекциями не ставим раньше,
    // чем у генератора появилась возможность сначала разместить лекционную часть.
    else if (!isPracticalInst(inst)) rank = 0;
    else {
      const hasLecture = instances.some((other) =>
        other.instId !== inst.instId && isLectureInst(other) && sameSubjectParticipantsRegardlessWeeks(inst, other)
      );
      rank = hasLecture ? 1 : 0;
    }
    dependencyRankCache.set(inst.instId, rank);
    return rank;
  }

  function compareByScarcity(a, b) {
    const depA = dependencyRank(a), depB = dependencyRank(b);
    if (depA !== depB) return depA - depB;
    // v91: сначала учитываем число РАБОЧИХ ДНЕЙ преподавателя. Полностью закрытый
    // день (например, преподаватель вообще недоступен в субботу) делает нагрузку
    // заметно дефицитнее, чем несколько отдельных закрытых пар в течение недели.
    // Поэтому 5 доступных дней всегда получают приоритет перед 6 доступными днями,
    // а уже внутри одинакового числа дней сравниваем количество доступных слотов.
    const profileA = teacherAvailabilityProfile(a), profileB = teacherAvailabilityProfile(b);
    const roomsA = a.format === "remote" ? 999 : candidateRooms(a).length;
    const roomsB = b.format === "remote" ? 999 : candidateRooms(b).length;
    // v1586: scarcest-first is the right choice for eventually finding a
    // complete, conflict-free assignment (fail fast on the hard cases while
    // there's still room to manoeuvre). But under a hard time BUDGET
    // (groupPrimaryBudgetMs/prePhaseBudgetMs), it has a real downside: a large
    // pool of narrow-availability instances (e.g. subgroup lessons with a
    // dedicated teacher) can consume the entire budget before the easier,
    // broadly-available "regular" lessons are even attempted once — even
    // though those would have placed instantly. fastMode's whole point is
    // "give me maximum coverage fast, I'll fix the rest by hand", so there the
    // secondary ordering is flipped: widest-availability instances go first
    // within the same dependency rank, maximizing how much gets placed before
    // time runs out. Normal mode is unchanged.
    const dir = fastMode ? -1 : 1;
    if (profileA.days !== profileB.days) return dir * (profileA.days - profileB.days);
    if (profileA.slots !== profileB.slots) return dir * (profileA.slots - profileB.slots);
    if (roomsA !== roomsB) return dir * (roomsA - roomsB);
    const wa = weeksOf(a);
    const wb = weeksOf(b);
    const earliestA = wa.length ? Math.min(...wa) : 999;
    const earliestB = wb.length ? Math.min(...wb) : 999;
    return earliestA - earliestB || String(a.instId).localeCompare(String(b.instId), "ru", {numeric:true});
  }

  // ---- спаривание занятий и одновременные подгруппы (co-schedule) ----
  // Блоки («Да»/«Тройка»/«Четвёрка»/«1 день») ставятся атомарно: все части сразу
  // в один день (подряд для block-режимов, свободно для oneDay). Co-schedule —
  // разные строки нагрузки одной группы, которые обязаны идти в одно и то же
  // время (разные предметы/подгруппы параллельно).
  const placedByGroup = new Set(); // instId уже пристроен блоком/co-schedule — не идёт в обычный проход
  const carriedIds = new Set(); // v92: автоматически поставленные пары прошлой генерации НЕ являются каркасом

  // v92: кнопка «Пересчитать (сохраняя закреплённые)» сохраняет только locked.
  // Старые автоматические назначения не переносим: иначе генератор наследует окна
  // и разреженные дни прошлой попытки и уже не может построить сетку с нуля.
  // Новая нагрузка более поздних недель заполняет свободные слоты вокруг него.
  // Это НЕ ручной lock: если старый слот стал недопустимым из-за доступности,
  // временной блокировки дня, окон или новых жёстких ограничений, он не переносится.
  const coLinkedLoadIds = new Set(loads.filter((l) => l.coScheduleId).map((l) => l.id));
  if (false && prior?.assignment) {
    const previousIds = Object.keys(prior.assignment)
      .filter((id) => instIdSet.has(id) && !lockedIds.has(id))
      .sort((a,b) => {
        const ia=byInst[a], ib=byInst[b];
        // v91: сохранённый каркас тоже восстанавливаем от самых дефицитных.
        // Иначе старые пары свободного преподавателя успевают занять слот ещё до
        // основной scarcity-сортировки и преподаватель без субботы остаётся без места.
        const scarcity = compareByScarcity(ia, ib);
        if (scarcity !== 0) return scarcity;
        const wa=Math.min(...(weeksOf(ia).length?weeksOf(ia):[999]));
        const wb=Math.min(...(weeksOf(ib).length?weeksOf(ib):[999]));
        return wa-wb || String(a).localeCompare(String(b),'ru',{numeric:true});
      });
    for (const instId of previousIds) {
      const inst = byInst[instId];
      if (!inst || inst.blockId || coLinkedLoadIds.has(inst.loadId) || inst.forceUnplaced) continue;
      const a = prior.assignment[instId];
      if (!a || !activeDays.includes(a.day) || a.period < 0 || a.period >= periodsPerDay) continue;
      if (!slotFreeForOwners(inst, a.day, a.period)) continue;
      if (!candidateDoesNotCreateWindows(inst, a.day, a.period)) continue;
      let roomId = null, extraRoomId = null;
      if (inst.format !== 'remote') {
        const oldRoomIds = assignmentRoomIds(a);
        const candidates = candidateRooms(inst);
        const primary = candidates.find((r)=>r.id===a.roomId && !busyRoom.has(`${slotKey(a.day,a.period)}|r|${r.id}`) && !roomBlockedByPractice(inst,a.day,a.period,r.id));
        if (!primary) continue;
        roomId = primary.id;
        if (inst.twoRooms) {
          const second = candidates.find((r)=>r.id===a.extraRoomId && r.id!==roomId && !busyRoom.has(`${slotKey(a.day,a.period)}|r|${r.id}`) && !roomBlockedByPractice(inst,a.day,a.period,r.id));
          if (!second) continue;
          extraRoomId = second.id;
        }
      }
      assign(instId, a.day, a.period, roomId, extraRoomId);
      carriedIds.add(instId);
    }
  }

  function tryPlaceConsecutiveBlock(memberIds) {
    const size = memberIds.length;
    const first = byInst[memberIds[0]];
    let best = null;
    // v1584: this used to scan every day/period for the best-scoring block and
    // only take the first feasible one in fastMode. Now it always takes the
    // first feasible block — scoreSlot is no longer computed here at all,
    // since it was only ever used for a comparison that no longer happens.
    outer: for (const day of activeDays) {
      for (let period = 0; period <= periodsPerDay - size; period++) {
        if (!dailyLoadLimitsOkForPeriods(first, day, Array.from({length:size},(_,k)=>period+k))) continue;
        let ok = true;
        const roomsChosen = {};
        for (let k = 0; k < size; k++) {
          const id = memberIds[k];
          const inst = byInst[id];
          const pp = period + k;
          if (!inst || !slotFreeForOwners(inst, day, pp) || (!fastMode && !candidateDoesNotCreateWindows(inst, day, pp))) { ok = false; break; }
          if (inst.format === "remote") roomsChosen[id] = { roomId: null, extraRoomId: null };
          else {
            const room = freeRoomAt(inst, day, pp);
            if (!room || !room.id) { ok = false; break; }
            roomsChosen[id] = { roomId: room.id, extraRoomId: room.extraRoomId || null };
          }
        }
        if (!ok) continue;
        best = { day, period, roomsChosen };
        break outer;
      }
    }
    if (!best) return false;
    for (let k = 0; k < size; k++) {
      const id = memberIds[k];
      const room = best.roomsChosen[id] || {};
      assign(id, best.day, best.period + k, room.roomId || null, room.extraRoomId || null);
    }
    return true;
  }

  function tryPlaceOneDayBlock(memberIds) {
    const size = memberIds.length;
    const first = byInst[memberIds[0]];
    let best = null;
    const combinations = (arr, k, from = 0, cur = [], out = []) => {
      if (cur.length === k) { out.push(cur.slice()); return out; }
      for (let i = from; i <= arr.length - (k - cur.length); i++) {
        cur.push(arr[i]); combinations(arr, k, i + 1, cur, out); cur.pop();
        if (out.length > 120) break;
      }
      return out;
    };
    // v1584: this used to scan every day/combination for the best-scoring
    // block and only take the first feasible one in fastMode. Now it always
    // takes the first feasible combination — scoreSlot/gapCountForSet are no
    // longer computed here at all, since they were only used for a
    // comparison that no longer happens.
    outer: for (const day of activeDays) {
      const freePeriods = [];
      for (let p = 0; p < periodsPerDay; p++) {
        if (slotFreeForOwners(first, day, p) && (fastMode || candidateDoesNotCreateWindows(first, day, p))) freePeriods.push(p);
      }
      if (freePeriods.length < size) continue;
      for (const chosen of combinations(freePeriods, size)) {
        if (!dailyLoadLimitsOkForPeriods(first, day, chosen)) continue;
        const roomIds = [];
        let ok = true;
        if (first.format !== "remote") {
          for (const p of chosen) {
            const r = freeRoomAt(first, day, p);
            if (!r || !r.id) { ok = false; break; }
            roomIds.push({roomId:r.id,extraRoomId:r.extraRoomId||null});
          }
        } else chosen.forEach(() => roomIds.push({roomId:null,extraRoomId:null}));
        if (!ok) continue;
        best = { day, chosen, roomIds };
        break outer;
      }
    }
    if (!best) return false;
    for (let k = 0; k < size; k++) assign(memberIds[k], best.day, best.chosen[k], best.roomIds[k]?.roomId || null, best.roomIds[k]?.extraRoomId || null);
    return true;
  }

  function tryPlaceCoGroup(memberIds) {
    let best = null;
    // v1584: this used to scan every day/period for the best-scoring slot and
    // only take the first feasible one in fastMode. Now it always takes the
    // first feasible slot — scoreSlot is no longer computed here at all.
    outer: for (const day of activeDays) {
      for (let period = 0; period < periodsPerDay; period++) {
        let ok = true;
        for (const id of memberIds) {
          if (!slotFreeForOwners(byInst[id], day, period) || (!fastMode && !candidateDoesNotCreateWindows(byInst[id], day, period))) { ok = false; break; }
        }
        if (!ok) continue;
        const tempReserved = new Set();
        const roomsChosen = {};
        let roomsOk = true;
        for (const id of memberIds) {
          const inst = byInst[id];
          if (inst.format === "remote") { roomsChosen[id] = {roomId:null,extraRoomId:null}; continue; }
          const found = freeRoomAt(inst, day, period, tempReserved);
          if (!found || !found.id) { roomsOk = false; break; }
          tempReserved.add(found.id);
          if (found.extraRoomId) tempReserved.add(found.extraRoomId);
          roomsChosen[id] = {roomId:found.id,extraRoomId:found.extraRoomId||null};
        }
        if (!roomsOk) continue;
        best = { day, period, roomsChosen };
        break outer;
      }
    }
    if (!best) return false;
    for (const id of memberIds) assign(id, best.day, best.period, best.roomsChosen[id]?.roomId || null, best.roomsChosen[id]?.extraRoomId || null);
    return true;
  }

  const preUnplaced = new Set(instances.filter((i) => i.forceUnplaced).map((i) => i.instId));

  // v1585: auto-расстановка больше не навязывает одновременность для очной
  // связки подгрупп (coScheduleId). Раньше «1) co-schedule группы» ниже
  // строила отдельную фазу, которая перебирала все дни/пары для КАЖДОЙ такой
  // связки, добавляя реальную стоимость к фазе потоков/блоков без явной
  // необходимости — по требованию можно просто ставить подгруппы как обычные
  // независимые занятия. Сама связка (`coScheduleId`, кнопка «связать
  // подгруппы», бейдж кластера) в интерфейсе никуда не делась — это касается
  // только автоматической расстановки. Если понадобится вернуть прежнее
  // поведение, `coJobs` ниже достаточно снова наполнить из `coScheduleGroups`.
  const coJobs = [];

  coJobs.sort((a,b) => {
    const aa = a.map((id)=>byInst[id]).sort(compareByScarcity)[0];
    const bb = b.map((id)=>byInst[id]).sort(compareByScarcity)[0];
    return compareByScarcity(aa, bb);
  });
  const fastMode = !!perfScope?.fastMode;
  // v1578: the co-group ("потоки") and pairing ("блоки") pre-phases below run
  // BEFORE the batched primary MRV loop and previously reported zero progress
  // while doing so — no deadline, no batching, no onBatchProgress call at all.
  // For a full multi-group semester with many stream lessons or multi-pair
  // blocks, this pre-phase alone can take a long time, during which the
  // browser legitimately saw no packet ("сервер не отдаёт даже первый
  // пакет"), because the first onBatchProgress call only happened once the
  // primary loop's first batch finished — i.e. only after this entire
  // pre-phase was already done. Emit progress periodically here too, reusing
  // the same payload shape as the primary batch loop further below.
  // v1582: this pre-phase previously had NO time budget at all, unlike the
  // primary loop. A group whose stream partners or block co-members are hard
  // to satisfy could spend a very long time here — with each attempt an
  // expensive exhaustive day/period/room search — before the main batched
  // loop (which DOES have a deadline) even got a chance to start. Give this
  // phase its own bounded budget: once it runs out, remaining co-group/pairing
  // jobs are treated as failed (same outcome as today) instead of attempted,
  // so the overall recalculation always keeps moving within a predictable
  // total time instead of stalling here indefinitely.
  const prePhaseStartedAt = Date.now();
  const prePhaseBudgetMs = fastMode ? (scopedGroupId ? 3000 : 8000) : (scopedGroupId ? 15000 : 40000);
  const prePhaseExpired = () => Date.now() - prePhaseStartedAt >= prePhaseBudgetMs;
  let lastPrePhaseProgressAt = 0;
  const prePhaseProgressIntervalMs = scopedGroupId ? 0 : 1500;
  const emitPrePhaseProgress = (phase) => {
    if (typeof perfScope?.onBatchProgress !== "function") return;
    const now = Date.now();
    if (!scopedGroupId && now - lastPrePhaseProgressAt < prePhaseProgressIntervalMs) return;
    lastPrePhaseProgressAt = now;
    const progressUnplaced = instances.filter((inst) => !assignment[inst.instId]).map((inst) => inst.instId);
    const pairUnits = (inst) => inst?.halfPair ? 0.5 : 1;
    const totalPairs = instances.reduce((sum, inst) => sum + pairUnits(inst), 0);
    const conflicts = progressUnplaced.reduce((sum, id) => sum + pairUnits(byInst[id]), 0);
    perfScope.onBatchProgress({
      assignment: { ...assignment },
      unplaced: progressUnplaced,
      instances,
      locked: [...lockedIds].filter((id) => assignment[id]),
      stats: { totalPairs, placed: totalPairs - conflicts, conflicts },
      generationMeta: {
        strategy: scopedGroupId ? "group-prephase" : "full-prephase",
        phase,
        prePhaseBudgetExpired: prePhaseExpired(),
        progress: true,
      },
    });
  };
  for (const memberIds of coJobs) {
    if (prePhaseExpired()) { memberIds.forEach((id) => preUnplaced.add(id)); continue; }
    memberIds.forEach((id) => placedByGroup.add(id));
    if (!tryPlaceCoGroup(memberIds)) memberIds.forEach((id) => preUnplaced.add(id));
    emitPrePhaseProgress("co-group");
  }

  // 2) спаривание (блоки внутри одной нагрузки)
  const byLoadForPairing = new Map();
  for (const inst of instances) {
    if (!inWeekScope(inst)) continue;
    if (lockedIds.has(inst.instId) || placedByGroup.has(inst.instId)) continue;
    if (!inst.blockId) continue;
    if (!byLoadForPairing.has(inst.blockId)) byLoadForPairing.set(inst.blockId, []);
    byLoadForPairing.get(inst.blockId).push(inst);
  }
  const pairingJobs = [...byLoadForPairing.values()]
    .map((members) => members.slice().sort((a,b)=>a.blockIndex-b.blockIndex))
    .sort((a,b)=>compareByScarcity(a[0],b[0]));
  for (const members of pairingJobs) {
    const memberIds = members.map((m) => m.instId);
    if (prePhaseExpired()) { memberIds.forEach((id) => preUnplaced.add(id)); continue; }
    memberIds.forEach((id) => placedByGroup.add(id));
    const mode = members[0].blockMode;
    const ok = mode === "oneDay" ? tryPlaceOneDayBlock(memberIds) : tryPlaceConsecutiveBlock(memberIds);
    if (!ok) memberIds.forEach((id) => preUnplaced.add(id));
    emitPrePhaseProgress("pairing");
  }

  const order = instances
    .filter((i) => inWeekScope(i) && !i.forceUnplaced && !lockedIds.has(i.instId) && !carriedIds.has(i.instId) && !placedByGroup.has(i.instId));

  const unplacedSet = new Set(preUnplaced);
  const roomlessSet = new Set();

  // v103: единая фаза «время + аудитория» и динамический MRV.
  // На каждом шаге заново выбираем наиболее дефицитную из ОСТАВШИХСЯ нагрузок.
  // Поэтому гибкая пара больше не может заранее занять единственный слот другой.
  const remaining = order.slice();
  // v1575: dependencyRank is static for a generated instance. Keep three rank
  // buckets instead of rescanning every remaining lesson on every MRV batch.
  // The active-id set lets us cheaply compact only the current rank bucket.
  const remainingIds = new Set(remaining.map((inst) => inst.instId));
  const remainingRankBuckets = [0, 1, 2].map((rank) =>
    remaining.filter((inst) => dependencyRank(inst) === rank).sort(compareByScarcity)
  );
  // v1569: одна группа считается пакетами. Каждые несколько наиболее дефицитных
  // занятий образуют небольшой MRV-пакет; после его размещения домены пересчитываются.
  // Это заметно дешевле, чем пересортировывать весь остаток после каждой одной пары.
  const groupPrimaryStartedAt = Date.now();
  const groupPrimaryBudgetMs = fastMode ? (scopedGroupId ? 20000 : 45000) : (scopedGroupId ? 45000 : 120000);
  // v1571: отдельный deadline на каждый пакет group-only. Даже если один набор
  // занятий оказался патологически сложным, он не должен удерживать весь расчёт
  // до общего 45-секундного лимита. После 6 секунд сохраняем лучший текущий
  // результат пакета и переходим к следующему.
  const groupBatchBudgetMs = fastMode ? (scopedGroupId ? 1500 : 2000) : (scopedGroupId ? 3000 : 5000);
  const groupBatchSize = fastMode ? (scopedGroupId ? 6 : 32) : (scopedGroupId ? 4 : 24);
  let groupBatchesProcessed = 0;
  let groupBatchesTimedOut = 0;
  let primaryBudgetExpired = false;
  if (fastMode) {
    // v1597: hierarchical coverage-first scheduler.
    // Global relations are resolved by the pre-phase above. From here on a
    // normal lesson only sees compact occupancy masks through slotFreeForOwners
    // and a cached room domain; on a full build groups are processed one by one
    // instead of mixing all 86 groups in one continuously re-ranked queue.
    const fastLoadById = new Map(loads.map((l) => [l.id, l]));
    const fastRoomDomainCache = new Map();
    const fastTeacherProfileCache = new Map();
    const fastFailureReasons = new Map();
    const fastRooms = (inst) => {
      if (inst.format === "remote") return [];
      if (!fastRoomDomainCache.has(inst.instId)) fastRoomDomainCache.set(inst.instId, candidateRooms(inst));
      return fastRoomDomainCache.get(inst.instId);
    };
    const fastTeacherProfile = (inst) => {
      if (!fastTeacherProfileCache.has(inst.instId)) fastTeacherProfileCache.set(inst.instId, teacherAvailabilityProfile(inst));
      return fastTeacherProfileCache.get(inst.instId);
    };
    const primaryGroupId = (inst) => {
      const stream = streamGroups(inst);
      if (stream.length) return stream[0];
      if (inst.groupId) return inst.groupId;
      if (Array.isArray(inst.groupIds) && inst.groupIds.length) return inst.groupIds[0];
      return "__ungrouped__";
    };
    const rememberFailure = (inst, reason) => {
      fastFailureReasons.set(inst.instId, reason || "NO_SLOT");
    };
    const fastTier = (inst) => {
      const load = fastLoadById.get(inst.loadId) || {};
      const isStream = (inst.streamGroupIds || []).length > 0 || (inst.streamParticipants || []).length > 0;
      if (isStream) return 0;
      const isCombinedInPerson = (load.format || "inperson") === "auto" && !!load.canBeRemote && inst.format !== "remote";
      if (isCombinedInPerson) return 1;
      const profile = fastTeacherProfile(inst);
      const roomsCount = inst.format === "remote" ? 999 : fastRooms(inst).length;
      if (profile.days <= 3 || profile.slots <= Math.max(8, periodsPerDay * 2) || roomsCount <= 2) return 2;
      return 3;
    };
    const stableHash = (value) => {
      let h = 2166136261;
      for (const ch of String(value || "")) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); }
      return h >>> 0;
    };
    const fastPeriodOrder = [...Array(periodsPerDay).keys()].sort((a,b) => preferredPeriodPenalty(a) - preferredPeriodPenalty(b) || a - b);
    const fastTryPlace = (inst, candidateLimit = 16, rescue = false) => {
      let best = null;
      let bestScore = Infinity;
      let legalSeen = 0;
      let ownerFreeSeen = 0;
      let roomFreeSeen = 0;
      const roomDomain = fastRooms(inst);
      if (inst.format !== "remote" && !roomDomain.length) {
        rememberFailure(inst, "NO_SUITABLE_ROOM");
        return false;
      }
      const dayOffset = activeDays.length ? stableHash(inst.instId) % activeDays.length : 0;
      const days = activeDays.length ? activeDays.map((_, i) => activeDays[(i + dayOffset) % activeDays.length]) : [];
      const periods = rescue ? [...Array(periodsPerDay).keys()] : fastPeriodOrder;
      for (const day of days) {
        for (const period of periods) {
          for (const half of (inst.halfPair ? [0,1] : [null])) {
            if (!slotFreeForOwners(inst, day, period, {}, half)) continue;
            ownerFreeSeen++;
            const room = freeRoomAt(inst, day, period, new Set(), half);
            if (inst.format !== "remote" && !room) continue;
            roomFreeSeen++;
            legalSeen++;
            const score = preferredPeriodPenalty(period)
              + loadCountOnDay(inst.loadId, day, inst.instId) * 2
              + (inst.halfPair ? half * 0.01 : 0);
            if (score < bestScore) {
              bestScore = score;
              best = { day, period, half, roomId: room?.id || null, extraRoomId: room?.extraRoomId || null };
            }
            if (legalSeen >= candidateLimit) break;
          }
          if (legalSeen >= candidateLimit) break;
        }
        if (legalSeen >= candidateLimit) break;
      }
      if (!best) {
        rememberFailure(inst, ownerFreeSeen === 0 ? "OWNER_BUSY_OR_BLOCKED" : (roomFreeSeen === 0 ? "ROOM_BUSY" : "NO_SLOT"));
        return false;
      }
      assign(inst.instId, best.day, best.period, best.roomId, best.extraRoomId, best.half ?? null);
      unplacedSet.delete(inst.instId);
      fastFailureReasons.delete(inst.instId);
      return true;
    };
    const emitFastProgress = (phase, processed, total, rescue = false, groupId = null) => {
      if (typeof perfScope?.onBatchProgress !== "function") return;
      const progressUnplaced = instances.filter((inst) => !assignment[inst.instId]).map((inst) => inst.instId);
      const pairUnits = (inst) => inst?.halfPair ? 0.5 : 1;
      const totalPairs = instances.reduce((sum, inst) => sum + pairUnits(inst), 0);
      const conflicts = progressUnplaced.reduce((sum, id) => sum + pairUnits(byInst[id]), 0);
      const reasonCounts = {};
      for (const id of progressUnplaced) {
        const reason = fastFailureReasons.get(id);
        if (reason) reasonCounts[reason] = (reasonCounts[reason] || 0) + 1;
      }
      perfScope.onBatchProgress({
        assignment: { ...assignment },
        unplaced: progressUnplaced,
        instances,
        locked: [...lockedIds].filter((id) => assignment[id]),
        stats: { totalPairs, placed: totalPairs - conflicts, conflicts },
        generationMeta: {
          strategy: scopedGroupId ? "group-hierarchical" : "full-hierarchical",
          fastMode: true,
          phase,
          groupId,
          rescue,
          processed,
          total,
          batchesProcessed: groupBatchesProcessed,
          primaryElapsedMs: Date.now() - groupPrimaryStartedAt,
          remainingPrimary: progressUnplaced.length,
          unplacedReasonCounts: reasonCounts,
          progress: true,
        },
      });
    };

    // Build independent group queues once. The global/pre-phase already owns
    // stream reservations, so ordinary groups no longer trigger full-semester
    // dependency scans while another group is being placed.
    const grouped = new Map();
    for (const inst of remaining) {
      const gid = scopedGroupId || primaryGroupId(inst);
      if (!grouped.has(gid)) grouped.set(gid, []);
      grouped.get(gid).push(inst);
    }
    const groupQueues = [...grouped.entries()].map(([gid, list]) => {
      list.sort((a,b) => fastTier(a) - fastTier(b) || dependencyRank(a) - dependencyRank(b) || compareByScarcity(a,b));
      const constrained = list.reduce((sum, inst) => {
        const p = fastTeacherProfile(inst);
        const rooms = inst.format === "remote" ? 12 : Math.max(1, fastRooms(inst).length);
        return sum + (1 / Math.max(1, p.slots)) + (1 / rooms) + (4 - fastTier(inst));
      }, 0);
      return { gid, list, constrained };
    }).sort((a,b) => b.constrained - a.constrained || String(a.gid).localeCompare(String(b.gid), "ru"));

    const failed = [];
    const fastPacketSize = scopedGroupId ? 8 : 24;
    let processed = 0;
    const total = remaining.length;
    outerGroups: for (const groupQueue of groupQueues) {
      let localProcessed = 0;
      for (const inst of groupQueue.list) {
        if (Date.now() - groupPrimaryStartedAt >= groupPrimaryBudgetMs) {
          primaryBudgetExpired = true;
          rememberFailure(inst, "PRIMARY_BUDGET");
          failed.push(inst);
          continue;
        }
        const pos = remaining.indexOf(inst);
        if (pos >= 0) remaining.splice(pos, 1);
        remainingIds.delete(inst.instId);
        processed++;
        localProcessed++;
        if (!fastTryPlace(inst, scopedGroupId ? 28 : 18, false)) failed.push(inst);
        if (localProcessed % fastPacketSize === 0) {
          groupBatchesProcessed++;
          emitFastProgress("group-fill", processed, total, false, groupQueue.gid);
        }
      }
      groupBatchesProcessed++;
      emitFastProgress("group-complete", processed, total, false, groupQueue.gid);
      if (primaryBudgetExpired) break outerGroups;
    }

    // Local rescue only. No global annealing: failed blocks get a wider local
    // scan against the already-built occupancy masks, then remain unplaced with
    // a reason instead of forcing expensive semester-wide rearrangements.
    const rescueStartedAt = Date.now();
    const rescueBudgetMs = scopedGroupId ? 8000 : 18000;
    const rescueQueue = failed.filter((inst) => !assignment[inst.instId]).sort((a,b) => fastTier(a)-fastTier(b) || compareByScarcity(a,b));
    let rescueProcessed = 0;
    for (const inst of rescueQueue) {
      if (Date.now() - rescueStartedAt >= rescueBudgetMs) {
        if (!fastFailureReasons.has(inst.instId)) rememberFailure(inst, "RESCUE_BUDGET");
        continue;
      }
      rescueProcessed++;
      fastTryPlace(inst, scopedGroupId ? 96 : 96, true);
      if (rescueProcessed % fastPacketSize === 0) {
        groupBatchesProcessed++;
        emitFastProgress("rescue", rescueProcessed, rescueQueue.length, true, primaryGroupId(inst));
      }
    }
    for (const inst of rescueQueue) if (!assignment[inst.instId]) unplacedSet.add(inst.instId);
    for (const inst of remaining) {
      if (!assignment[inst.instId]) {
        if (!fastFailureReasons.has(inst.instId)) rememberFailure(inst, primaryBudgetExpired ? "PRIMARY_BUDGET" : "NOT_PROCESSED");
        unplacedSet.add(inst.instId);
      }
    }
    perfScope.fastUnplacedReasons = Object.fromEntries(fastFailureReasons.entries());
    remaining.length = 0;
    remainingIds.clear();
    groupBatchesProcessed++;
    emitFastProgress("rescue-final", rescueProcessed, rescueQueue.length, true, scopedGroupId || null);
  } else {
  while (remaining.length) {
    if (Date.now() - groupPrimaryStartedAt >= groupPrimaryBudgetMs) {
      primaryBudgetExpired = true;
      for (const pending of remaining) unplacedSet.add(pending.instId);
      remaining.length = 0;
      break;
    }
    const groupBatchStartedAt = Date.now();
    const groupBatchDeadline = groupBatchStartedAt + groupBatchBudgetMs;
    const groupBatchExpired = () => Date.now() >= groupBatchDeadline;
    let batchTimedOut = false;
    let minDep = -1;
    let eligible = [];
    for (let rank = 0; rank < remainingRankBuckets.length; rank++) {
      const compact = remainingRankBuckets[rank].filter((inst) => remainingIds.has(inst.instId));
      remainingRankBuckets[rank] = compact;
      if (compact.length) { minDep = rank; eligible = compact; break; }
    }
    if (minDep < 0 || !eligible.length) break;
    // v111 performance: полный динамический MRV для каждой оставшейся пары давал
    // квадратичный взрыв времени. Сначала берём небольшой shortlist по дешёвой
    // статической дефицитности, затем уже для него считаем реальный текущий домен.
    // Это сохраняет принцип «сначала дефицитные», но не пересчитывает сотни одинаковых
    // дорогих проверок после каждого одного назначения.
    eligible.sort(compareByScarcity);
    // v1577: the shortlist window must comfortably exceed the actual batch
    // size, or larger batches (e.g. fastMode's 32) get silently truncated by
    // the window itself before batching even applies — fastMode's bigger
    // batch was never fully used because the window ceiling stayed at 28
    // regardless of groupBatchSize. The window now scales with whichever
    // batch size the current mode actually uses, keeping a margin so the
    // domain-based reordering step still has real candidates to choose from.
    const windowCeiling = scopedGroupId ? 18 : Math.max(28, groupBatchSize + 10);
    const mrvWindow = Math.min(windowCeiling, Math.max(10, Math.ceil(Math.sqrt(eligible.length) * 3)));
    const shortlist = eligible.slice(0, mrvWindow);
    const feasibleNow = new Map();
    // v1590: fastMode intentionally skips dynamic MRV. Computing the current
    // domain walks every day/period (and room/owner constraints) for every
    // shortlisted lesson before we even try to place anything. For the fast
    // pass coverage matters more than perfect scarcity ordering, so the cheap
    // static order is used directly. Normal mode keeps full dynamic MRV.
    if (!fastMode) {
      for (const cand of shortlist) {
        if (groupBatchExpired()) { batchTimedOut = true; break; }
        feasibleNow.set(cand.instId, dynamicFeasibleSlotCount(cand));
      }
      // Не вычисленные из-за deadline кандидаты остаются после уже оценённых,
      // но пакет всё равно может сделать полезную работу на найденной части.
      shortlist.sort((a,b) => {
        const fa = feasibleNow.has(a.instId) ? feasibleNow.get(a.instId) : Infinity;
        const fb = feasibleNow.has(b.instId) ? feasibleNow.get(b.instId) : Infinity;
        if (fa !== fb) return fa - fb;
        return compareByScarcity(a,b);
      });
    }
    const batch = shortlist.slice(0, Math.min(groupBatchSize, shortlist.length));
    groupBatchesProcessed++;
    let processedInBatch = 0;
    for (const inst of batch) {
      if (groupBatchExpired()) { batchTimedOut = true; break; }
      const pos = remaining.indexOf(inst);
      if (pos < 0) continue;
      remaining.splice(pos, 1);
      remainingIds.delete(inst.instId);
      processedInBatch++;
      let best = null, bestScore = Infinity;
      // v1590 fast pass: inspect only a small number of feasible positions and
      // accept the first legal one. The expensive soft score (gaps, compactness,
      // preferred time, remote-day spread, stability) is deliberately deferred
      // to the normal/Improve pass. Hard constraints remain untouched.
      let feasibleChecked = 0;
      const fastFeasibleLimit = scopedGroupId ? 18 : 24;
      const preferredPeriods = fastMode
        ? [...Array(periodsPerDay).keys()].sort((a,b) => preferredPeriodPenalty(a) - preferredPeriodPenalty(b))
        : [...Array(periodsPerDay).keys()];
      outerSlots: for (const day of activeDays) {
        for (const period of preferredPeriods) {
          if (groupBatchExpired()) { batchTimedOut = true; break outerSlots; }
          if (!fastMode && !candidateDoesNotCreateWindows(inst, day, period)) continue;
          for (const half of (inst.halfPair ? [0,1] : [null])) {
            if (groupBatchExpired()) { batchTimedOut = true; break outerSlots; }
            if (!slotFreeForOwners(inst, day, period, {}, half)) continue;
            const room = freeRoomAt(inst, day, period, new Set(), half);
            if (inst.format !== "remote" && !room) continue;
            feasibleChecked++;
            if (fastMode) {
              // v1591: fast mode still checks a small legal-slot sample instead of
              // accepting/rejecting the very first candidate. Window avoidance is a
              // soft preference here, never a blocker. This restores coverage while
              // keeping the expensive full scoreSlot() out of the fast pass.
              const s = preferredPeriodPenalty(period) + (inst.halfPair ? half * 0.01 : 0);
              if (s < bestScore) { bestScore = s; best = { day, period, half, roomId: room?.id || null, extraRoomId: room?.extraRoomId || null }; }
              if (feasibleChecked >= fastFeasibleLimit) break outerSlots;
              continue;
            }
            const s = scoreSlot(inst, day, period) + (inst.halfPair ? half * 0.01 : 0);
            if (s < bestScore) { bestScore = s; best = { day, period, half, roomId: room?.id || null, extraRoomId: room?.extraRoomId || null }; }
          }
        }
      }
      // Если deadline сработал после того, как хотя бы один допустимый вариант
      // уже найден, фиксируем именно лучший найденный вариант. Если нет — оставляем
      // занятие неразмещённым для короткой финальной rescue-фазы.
      if (best) assign(inst.instId, best.day, best.period, best.roomId, best.extraRoomId, best.half ?? null);
      else unplacedSet.add(inst.instId);
      if (batchTimedOut) break;
    }
    if (batchTimedOut) {
      groupBatchesTimedOut++;
      // Гарантируем движение вперёд: если весь budget ушёл ещё на MRV-оценку и
      // пакет не успел обработать ни одного занятия, одно самое дефицитное занятие
      // переносим в unplaced. Иначе тот же кандидат мог бы снова съесть следующий batch.
      if (processedInBatch === 0 && batch.length) {
        const fallback = batch[0];
        const pos = remaining.indexOf(fallback);
        if (pos >= 0) remaining.splice(pos, 1);
        if (fallback?.instId) { remainingIds.delete(fallback.instId); unplacedSet.add(fallback.instId); }
      }
    }

    // v1570: group generation can publish a lightweight intermediate schedule
    // after every completed MRV batch. The caller converts this full in-memory
    // snapshot into a target-group-only patch, so the browser can display/save
    // progress without downloading the whole 86-group semester each time.
    if (typeof perfScope?.onBatchProgress === "function") {
      const progressUnplaced = instances.filter((inst) => !assignment[inst.instId]).map((inst) => inst.instId);
      const pairUnits = (inst) => inst?.halfPair ? 0.5 : 1;
      const totalPairs = instances.reduce((sum, inst) => sum + pairUnits(inst), 0);
      const conflicts = progressUnplaced.reduce((sum, id) => sum + pairUnits(byInst[id]), 0);
      perfScope.onBatchProgress({
        assignment: { ...assignment },
        unplaced: progressUnplaced,
        instances,
        locked: [...lockedIds].filter((id) => assignment[id]),
        stats: { totalPairs, placed: totalPairs - conflicts, conflicts },
        generationMeta: {
          strategy: scopedGroupId ? "group-batches" : "full-batches",
          fastMode,
          batchSize: groupBatchSize,
          batchesProcessed: groupBatchesProcessed,
          batchesTimedOut: groupBatchesTimedOut,
          batchBudgetMs: groupBatchBudgetMs,
          lastBatchTimedOut: batchTimedOut,
          lastBatchElapsedMs: Date.now() - groupBatchStartedAt,
          primaryBudgetExpired: false,
          primaryElapsedMs: Date.now() - groupPrimaryStartedAt,
          remainingPrimary: remaining.length,
          progress: true,
        },
      });
    }
  }
  }

  // v1569: локальная оценка качества для group-only не должна каждый раз
  // пересчитывать метрики всех 86 групп. Двигаются только занятия выбранной группы.
  // Для группы считаем только её сетку; для преподавателей — выбранную группу плюс
  // уже стоящие занятия тех преподавателей, которые реально ведут эту группу.
  const groupMetricInstances = scopedGroupId
    ? instances.filter((inst) => belongsToGroup(inst, scopedGroupId))
    : instances;
  const scopedTeacherMetricIds = scopedGroupId
    ? new Set(groupMetricInstances.map((inst) => inst.teacherId).filter(Boolean))
    : null;
  const teacherMetricInstances = scopedGroupId
    ? instances.filter((inst) => scopedTeacherMetricIds.has(inst.teacherId))
    : instances;

  // v70: статистика компактности строится по группе/преподавателю + неделе + дню.
  // Нельзя смешивать чётные и нечётные недели в одну сетку: окно существует только
  // если оно реально есть у группы в конкретную календарную неделю.
  function weeklyCompactnessMetrics() {
    const groupTrackSets = new Map(); // gid|subgroup|week|day
    const groupUnionSets = new Map(); // gid|week|day — только для нагрузки/времени дня
    const teacherSets = new Map();
    const add = (map, key, period) => {
      if (!map.has(key)) map.set(key, new Set());
      map.get(key).add(period);
    };
    for (const inst of groupMetricInstances) {
      const a = assignment[inst.instId];
      if (!a) continue;
      const weeks = weeksOf(inst);
      for (const week of weeks) {
        for (const gid of streamGroups(inst)) {
          if (scopedGroupId && gid !== scopedGroupId) continue;
          add(groupUnionSets, `${gid}|${week}|${a.day}`, a.period);
          for (const sg of affectedSubgroupsForGroup(inst, gid)) add(groupTrackSets, `${gid}|${sg}|${week}|${a.day}`, a.period);
        }
      }
    }
    for (const inst of teacherMetricInstances) {
      const a = assignment[inst.instId];
      if (!a || !inst.teacherId) continue;
      for (const week of weeksOf(inst)) add(teacherSets, `${inst.teacherId}|${week}|${a.day}`, a.period);
    }
    // Для headline «окна у группы» считаем худшую подгруппу в конкретный день,
    // чтобы одно общее окно не удваивалось, но окно одной подгруппы не исчезало.
    const gapsByBaseDay = new Map();
    for (const [key, set] of groupTrackSets.entries()) {
      const [gid, sg, week, day] = key.split('|');
      const base = `${gid}|${week}|${day}`;
      gapsByBaseDay.set(base, Math.max(gapsByBaseDay.get(base) || 0, gapCountForSet(set)));
    }
    let groupGaps = [...gapsByBaseDay.values()].reduce((a,b)=>a+b,0);
    let teacherGaps = 0, groupSingletons = 0, teacherSingletons = 0, groupDays = 0, teacherDays = 0, groupTargetPenalty = 0, groupTimePenalty = 0;
    for (const [key, set] of groupUnionSets.entries()) {
      if (!set.size) continue;
      groupDays++;
      if (set.size === 1) groupSingletons++;
      const gid = key.split('|')[0];
      groupTargetPenalty += groupDayLoadPenalty(set.size, gid);
      groupTimePenalty += dayStartPenaltyForSet(set);
    }
    for (const set of teacherSets.values()) {
      if (!set.size) continue;
      teacherGaps += gapCountForSet(set);
      teacherDays++;
      if (set.size === 1) teacherSingletons++;
    }
    return { gaps: groupGaps + teacherGaps, groupGaps, teacherGaps, groupSingletons, teacherSingletons, groupDays, teacherDays, groupTargetPenalty, groupTimePenalty };
  }

  function sameDayDuplicates() {
    const seen = new Map();
    for (const inst of groupMetricInstances) {
      const a = assignment[inst.instId];
      if (!a) continue;
      const key = `${inst.loadId}_${a.day}`;
      seen.set(key, (seen.get(key) || 0) + 1);
    }
    let dup = 0;
    for (const c of seen.values()) if (c > 1) dup += c - 1;
    return dup;
  }
  function availabilityViolations() {
    let v = 0;
    for (const inst of groupMetricInstances) {
      const a = assignment[inst.instId];
      if (!a) continue;
      if (isTeacherUnavailableForWeeks(teacherById[inst.teacherId], a.day, a.period, inst.weeks)) {
        const et = teacherById[inst.teacherId]?.employmentType;
        v += et === "partTime" || et === "contract" ? 2.5 : 1;
      }
    }
    return v;
  }
  function roomlessCount() {
    let c = 0;
    for (const inst of groupMetricInstances) {
      const a = assignment[inst.instId];
      if (a && inst.format !== "remote" && !a.roomId) c++;
    }
    return c;
  }
  const lectureIdsByPractical = new Map();
  const lectureInstancesStatic = groupMetricInstances.filter(isLectureInst);
  for (const pr of groupMetricInstances.filter(isPracticalInst)) {
    const relevant = lectureInstancesStatic.filter((lec) => sameSubjectParticipantsRegardlessWeeks(lec, pr));
    const remoteLead = relevant.filter(isRemoteLectureLead);
    const inPerson = relevant.filter((lec) => lec.format !== "remote");
    // v1599: если у комбинированной дисциплины есть обе лекционные стадии,
    // практика (если она вообще есть) должна идти после обеих.
    const gates = remoteLead.length && inPerson.length ? [...remoteLead, ...inPerson] : (remoteLead.length ? remoteLead : relevant);
    lectureIdsByPractical.set(pr.instId, gates.map((lec) => lec.instId));
  }
  function lecturePracticeOrderViolations() {
    let violations = 0;
    // ДО-потоковая лекция обязана предшествовать очной лекции той же группы.
    for (const lec of lectureInstancesStatic.filter((x) => x.format !== "remote")) {
      const a = assignment[lec.instId];
      if (!a) continue;
      const remoteLead = lectureInstancesStatic.filter((other) => other.instId !== lec.instId && isRemoteLectureLead(other) && sameSubjectParticipantsRegardlessWeeks(lec, other));
      if (!remoteLead.length) continue;
      const orders = remoteLead.map((other) => { const oa=assignment[other.instId]; return oa ? occurrenceOrder(other,oa.day,oa.period,"min") : Infinity; });
      const remoteFirst=Math.min(...orders);
      if (!Number.isFinite(remoteFirst) || occurrenceOrder(lec,a.day,a.period,"min") <= remoteFirst) violations++;
    }
    for (const pr of groupMetricInstances.filter(isPracticalInst)) {
      const pa = assignment[pr.instId];
      if (!pa) continue;
      const lectureIds = lectureIdsByPractical.get(pr.instId) || [];
      if (!lectureIds.length) continue;
      const gateOrders=[];
      let missing=false;
      for (const lecId of lectureIds) {
        const lec = byInst[lecId];
        const la = assignment[lecId];
        if (!la) { missing=true; break; }
        gateOrders.push(occurrenceOrder(lec, la.day, la.period, "min"));
      }
      if (missing || !gateOrders.length || occurrenceOrder(pr, pa.day, pa.period, "min") <= Math.max(...gateOrders)) violations++;
    }
    return violations;
  }
  function weeklyTemplateVariation() {
    const total = totalSemesterWeeks(config);
    if (!total || total < 2) return 0;
    const byGroupWeek = new Map();
    const keyFor = (gid, week) => `${gid}|${week}`;
    for (const inst of groupMetricInstances) {
      const a = assignment[inst.instId];
      if (!a) continue;
      const slot = `${a.day}:${a.period}`;
      for (const gid of streamGroups(inst)) {
        if (scopedGroupId && gid !== scopedGroupId) continue;
        for (const week of weeksOf(inst)) {
          const key = keyFor(gid, week);
          if (!byGroupWeek.has(key)) byGroupWeek.set(key, new Set());
          byGroupWeek.get(key).add(slot);
        }
      }
    }
    let variation = 0;
    const metricGroups = scopedGroupId ? groups.filter((g) => g.id === scopedGroupId) : groups;
    for (const group of metricGroups) {
      // v66: сравниваем только недели одной чётности: 1↔3↔5 и 2↔4↔6.
      // Числитель и знаменатель не обязаны копировать друг друга.
      for (let week = 1; week + 2 <= total; week++) {
        const a = byGroupWeek.get(keyFor(group.id, week)) || new Set();
        const b = byGroupWeek.get(keyFor(group.id, week + 2)) || new Set();
        if (!a.size || !b.size) continue;
        for (const slot of a) if (!b.has(slot)) variation++;
        for (const slot of b) if (!a.has(slot)) variation++;
      }
    }
    return variation;
  }

  function cost() {
    // v70: все ключевые штрафы компактности считаются на реальных неделях.
    // Окно остаётся самым дорогим нарушением. Одиночный день группы существенно
    // хуже, чем одиночный день преподавателя, поэтому генератор старается собрать
    // студентам непрерывные дни в пределах заданных Мин/Макс пар.
    const compact = weeklyCompactnessMetrics();
    // v103: жёсткие дефекты должны доминировать над эстетикой. Очная пара без
    // аудитории почти равна неразмещённой; затем идут порядок/доступность, окна
    // студентов, окна преподавателей и только потом мягкая эстетика дня.
    return unplacedSet.size * 1000000000000 + roomlessCount() * 900000000000 +
      lecturePracticeOrderViolations() * 500000000000 + availabilityViolations() * 400000000000 +
      compact.groupGaps * 1000000000 + compact.teacherGaps * 50000000 +
      compact.groupTargetPenalty * 3000 + compact.groupTimePenalty * 1800 +
      compact.groupSingletons * 20000000 + compact.teacherSingletons * 300000 +
      compact.groupDays * 250000 + compact.teacherDays * 14000 + weeklyTemplateVariation() * 2500 +
      remoteDayDeficitTotal() * 12000 + sameDayDuplicates() * 50;
  }

  const inPerfScope = (inst) => inWeekScope(inst) && (!scopedGroupId || (belongsToGroup(inst, scopedGroupId) && streamGroups(inst).every((gid) => gid === scopedGroupId)));
  const localSearchIds = instances.filter((inst) => inPerfScope(inst) && !inst.blockId && !inst.halfPair && !placedByGroup.has(inst.instId)).map((inst) => inst.instId);
  const movableIds = () => localSearchIds.filter((id) => assignment[id] && !lockedIds.has(id));
  const unplacedIds = () => [...unplacedSet].filter((id) => inPerfScope(byInst[id]) && !byInst[id].blockId && !byInst[id].halfPair && !placedByGroup.has(id));

  // v143 performance: for group-only recalculation, optimize only that group's
  // movable lessons; other groups remain occupancy constraints but do not consume
  // thousands of local-search iterations.
  const searchScale = scopedGroupId ? Math.max(1, localSearchIds.length) : instances.length;
  // v1568: пересчёт одной группы обязан оставаться интерактивным. Раньше даже
  // 1000–1800 итераций могли быть очень дорогими, потому что cost() включает
  // семестровые метрики. Ограничиваем число попыток и, главное, вводим deadline:
  // возвращаем лучший уже найденный вариант вместо ожидания серверного timeout.
  const largeFullMode = !scopedGroupId && instances.length >= 900;
  const iterations = fastMode
    ? 0
    : (scopedGroupId ? clamp(searchScale * 8, 120, 650) : (largeFullMode ? clamp(searchScale * 2, 300, 1200) : clamp(searchScale * 6, 500, 2800)));
  const optimizationStartedAt = Date.now();
  // v1569: после пакетной первичной расстановки оставляем короткую доводку.
  // Если первичная стадия уже была сложной, не начинаем ещё один длинный поиск.
  const optimizationBudgetMs = fastMode
    ? 0
    : (scopedGroupId ? (primaryBudgetExpired ? 2500 : 9000) : (largeFullMode ? 30000 : 60000));
  const optimizationDeadline = optimizationStartedAt + optimizationBudgetMs;
  const budgetExpired = () => Date.now() >= optimizationDeadline;
  // v103: температура задаётся в относительной шкале качества. Раньше T=4 при
  // штрафах в миллиарды означала фактически нулевую вероятность выхода из локального минимума.
  const T0 = 0.08, Tend = 0.0008;
  let curCost = fastMode ? 0 : cost();
  let bestAssignment = JSON.parse(JSON.stringify(assignment));
  let bestUnplaced = new Set(unplacedSet);
  let bestCost = curCost;
  let lastBestIter = 0;

  for (let iter = 0; iter < iterations; iter++) {
    if (budgetExpired()) break;
    // v111: прекращаем annealing, если долго нет ни одного улучшения лучшего решения.
    if (iter - lastBestIter > (largeFullMode ? Math.max(120, Math.floor(iterations * 0.16)) : Math.max(500, Math.floor(iterations * 0.28)))) break;
    const T = T0 * Math.pow(Tend / T0, iter / iterations);
    const r = Math.random();
    let changed = false;

    if (r < 0.3 && unplacedIds().length > 0) {
      const ids = unplacedIds();
      const instId = ids[Math.floor(Math.random() * ids.length)];
      const inst = byInst[instId];
      const day = activeDays[Math.floor(Math.random() * activeDays.length)];
      const period = Math.floor(Math.random() * periodsPerDay);
      if (slotFreeForOwners(inst, day, period) && candidateDoesNotCreateWindows(inst, day, period)) {
        const room = freeRoomAt(inst, day, period);
        if (room) {
          assign(instId, day, period, room.id, room.extraRoomId || null);
          unplacedSet.delete(instId);
          const nc = cost();
          if (nc <= curCost || Math.random() < Math.exp(-((nc - curCost) / Math.max(1, Math.abs(curCost))) / T)) { curCost = nc; changed = true; }
          else { unassign(instId); unplacedSet.add(instId); }
        }
      }
    } else if (r < 0.7) {
      const ids = movableIds();
      if (ids.length > 0) {
        const instId = ids[Math.floor(Math.random() * ids.length)];
        const inst = byInst[instId];
        const orig = { ...assignment[instId] };
        if (!removalDoesNotCreateWindows(inst, orig.day, orig.period)) continue;
        unassign(instId);
        const day = activeDays[Math.floor(Math.random() * activeDays.length)];
        const period = Math.floor(Math.random() * periodsPerDay);
        if (slotFreeForOwners(inst, day, period) && candidateDoesNotCreateWindows(inst, day, period)) {
          const room = freeRoomAt(inst, day, period);
          if (room) {
            assign(instId, day, period, room.id, room.extraRoomId || null);
            const nc = cost();
            if (nc <= curCost || Math.random() < Math.exp(-((nc - curCost) / Math.max(1, Math.abs(curCost))) / T)) { curCost = nc; changed = true; }
            else { unassign(instId); assign(instId, orig.day, orig.period, orig.roomId, orig.extraRoomId || null); }
          } else assign(instId, orig.day, orig.period, orig.roomId, orig.extraRoomId || null);
        } else assign(instId, orig.day, orig.period, orig.roomId, orig.extraRoomId || null);
      }
    } else {
      const ids = movableIds();
      if (ids.length >= 2) {
        const i1 = Math.floor(Math.random() * ids.length);
        let i2 = Math.floor(Math.random() * ids.length);
        while (i2 === i1) i2 = Math.floor(Math.random() * ids.length);
        const idA = ids[i1], idB = ids[i2];
        const instA = byInst[idA], instB = byInst[idB];
        const origA = { ...assignment[idA] }, origB = { ...assignment[idB] };
        if (!removalDoesNotCreateWindows(instA, origA.day, origA.period) ||
            !removalDoesNotCreateWindows(instB, origB.day, origB.period)) continue;
        unassign(idA); unassign(idB);
        let ok = true, roomA = null, roomB = null;
        if (slotFreeForOwners(instA, origB.day, origB.period) && candidateDoesNotCreateWindows(instA, origB.day, origB.period)) roomA = freeRoomAt(instA, origB.day, origB.period);
        if (!roomA) ok = false;
        if (ok) assign(idA, origB.day, origB.period, roomA.id, roomA.extraRoomId || null);
        if (ok && slotFreeForOwners(instB, origA.day, origA.period) && candidateDoesNotCreateWindows(instB, origA.day, origA.period)) roomB = freeRoomAt(instB, origA.day, origA.period);
        if (ok && !roomB) ok = false;
        if (ok) assign(idB, origA.day, origA.period, roomB.id, roomB.extraRoomId || null);
        if (!ok) {
          if (assignment[idA]) unassign(idA);
          if (assignment[idB]) unassign(idB);
          assign(idA, origA.day, origA.period, origA.roomId, origA.extraRoomId || null);
          assign(idB, origB.day, origB.period, origB.roomId, origB.extraRoomId || null);
        } else {
          const nc = cost();
          if (nc <= curCost || Math.random() < Math.exp(-((nc - curCost) / Math.max(1, Math.abs(curCost))) / T)) { curCost = nc; changed = true; }
          else {
            unassign(idA); unassign(idB);
            assign(idA, origA.day, origA.period, origA.roomId, origA.extraRoomId || null);
            assign(idB, origB.day, origB.period, origB.roomId, origB.extraRoomId || null);
          }
        }
      }
    }

    if (changed && curCost < bestCost) {
      bestCost = curCost;
      bestAssignment = JSON.parse(JSON.stringify(assignment));
      bestUnplaced = new Set(unplacedSet);
      lastBestIter = iter;
    }
  }

  // v89: восстанавливаем лучший найденный вариант и выполняем отдельный
  // детерминированный проход уплотнения. Его цель — при том же количестве
  // размещённых пар закрыть прежде всего окна групп, затем окна преподавателей,
  // и только потом улучшать равномерность. Дневной максимум группы здесь мягкий:
  // превышение получает штраф, но не блокирует перенос, если это позволяет закрыть окно.
  const finalCostBeforeCompact = cost();
  const chosenAssignment = JSON.parse(JSON.stringify(finalCostBeforeCompact <= bestCost ? assignment : bestAssignment));
  const chosenUnplaced = new Set(finalCostBeforeCompact <= bestCost ? unplacedSet : bestUnplaced);

  for (const id of Object.keys(assignment)) unassign(id);
  for (const [id, a] of Object.entries(chosenAssignment)) assign(id, a.day, a.period, a.roomId || null, a.extraRoomId || null, a.half ?? null);
  unplacedSet.clear();
  for (const id of chosenUnplaced) unplacedSet.add(id);

  let compactMoves = 0;
  const compactCandidatesFor = (inst, orig) => {
    const out = [];
    for (const day of activeDays) {
      for (let period = 0; period < periodsPerDay; period++) {
        if (day === orig.day && period === orig.period) continue;
        // Сначала рассматриваем текущий день и слоты рядом с уже существующим
        // блоком группы; это делает проход предсказуемым и быстрее закрывает окна.
        let proximity = 100 + Math.abs(day - orig.day) * 10 + Math.abs(period - orig.period);
        for (const gid of streamGroups(inst)) {
          for (const week of weeksOf(inst)) {
            const set = groupWeekDayPeriods(gid, week, day, inst.instId);
            if (set.has(period - 1) || set.has(period + 1)) proximity -= 40;
            if (set.size && period > Math.min(...set) && period < Math.max(...set)) proximity -= 80;
          }
        }
        if (day === orig.day) proximity -= 20;
        out.push({ day, period, proximity });
      }
    }
    // v111: для уплотнения нет смысла проверять все 6×N слотов — сначала
    // достаточно ближайших кандидатов, которые реально способны закрыть окно.
    return out.sort((a,b)=>a.proximity-b.proximity).slice(0, 24);
  };

  for (let pass = 0; pass < (scopedGroupId ? 3 : 9); pass++) {
    if (budgetExpired()) break;
    let improved = false;
    const ids = movableIds().slice().sort((a,b)=>compareByScarcity(byInst[a], byInst[b]));
    for (const instId of ids) {
      if (budgetExpired()) break;
      const inst = byInst[instId];
      const orig = assignment[instId] ? { ...assignment[instId] } : null;
      if (!orig) continue;
      const beforeCost = cost();
      unassign(instId);
      let best = orig, bestMoveCost = beforeCost;
      for (const cand of compactCandidatesFor(inst, orig)) {
        if (!slotFreeForOwners(inst, cand.day, cand.period)) continue;
        if (!dailyLoadLimitsOk(inst, cand.day, cand.period) || !dailyEdgePositionOk(inst, cand.day, cand.period) || !lecturePracticeOrderOk(inst, cand.day, cand.period)) continue;
        const room = freeRoomAt(inst, cand.day, cand.period);
        if (!room) continue;
        assign(instId, cand.day, cand.period, room.id, room.extraRoomId || null);
        const nc = cost();
        if (nc < bestMoveCost) {
          bestMoveCost = nc;
          best = { day:cand.day, period:cand.period, roomId:room.id, extraRoomId:room.extraRoomId || null };
        }
        unassign(instId);
      }
      assign(instId, best.day, best.period, best.roomId || null, best.extraRoomId || null);
      if (bestMoveCost < beforeCost) { compactMoves++; improved = true; }
    }
    if (!improved) break;
  }

  // v92: второй детерминированный этап — обмен двумя занятиями. Обычный move
  // не способен закрыть окно, если нужный слот занят другой подвижной парой.
  // Перебираем ограниченное число пар и принимаем только строго улучшающий cost.
  for (let pass = 0; pass < (scopedGroupId ? 1 : 3); pass++) {
    if (budgetExpired()) break;
    let improved = false;
    // v111: O(n²) обмен ограничиваем наиболее дефицитными/проблемными парами.
    const ids = movableIds().slice().sort((a,b)=>compareByScarcity(byInst[a], byInst[b])).slice(0, scopedGroupId ? 32 : 90);
    for (let i = 0; i < ids.length; i++) {
      if (budgetExpired()) break;
      const idA = ids[i], a0 = assignment[idA];
      if (!a0) continue;
      for (let j = i + 1; j < ids.length; j++) {
        if (budgetExpired()) break;
        const idB = ids[j], b0 = assignment[idB];
        if (!b0 || (a0.day === b0.day && a0.period === b0.period)) continue;
        const instA = byInst[idA], instB = byInst[idB];
        const before = cost();
        const origA = { ...a0 }, origB = { ...b0 };
        unassign(idA); unassign(idB);
        let ok = slotFreeForOwners(instA, origB.day, origB.period) && dailyLoadLimitsOk(instA, origB.day, origB.period) && dailyEdgePositionOk(instA, origB.day, origB.period) && lecturePracticeOrderOk(instA, origB.day, origB.period);
        let roomA = ok ? freeRoomAt(instA, origB.day, origB.period) : null;
        if (ok && (instA.format === 'remote' || roomA)) assign(idA, origB.day, origB.period, roomA?.id || null, roomA?.extraRoomId || null);
        else ok = false;
        let roomB = null;
        if (ok) {
          ok = slotFreeForOwners(instB, origA.day, origA.period) && dailyLoadLimitsOk(instB, origA.day, origA.period) && dailyEdgePositionOk(instB, origA.day, origA.period) && lecturePracticeOrderOk(instB, origA.day, origA.period);
          roomB = ok ? freeRoomAt(instB, origA.day, origA.period) : null;
          if (ok && (instB.format === 'remote' || roomB)) assign(idB, origA.day, origA.period, roomB?.id || null, roomB?.extraRoomId || null);
          else ok = false;
        }
        const after = ok ? cost() : Infinity;
        if (ok && after < before) { compactMoves++; improved = true; break; }
        if (assignment[idA]) unassign(idA);
        if (assignment[idB]) unassign(idB);
        assign(idA, origA.day, origA.period, origA.roomId || null, origA.extraRoomId || null);
        assign(idB, origB.day, origB.period, origB.roomId || null, origB.extraRoomId || null);
      }
      if (improved) break;
    }
    if (!improved) break;
  }

  // v103: ограниченный 3-way swap. Он позволяет выйти из ситуации A→B, B→C, C→A,
  // которую одиночный move и обмен двух пар не решают. Перебираем только небольшой
  // набор наиболее проблемных подвижных занятий, чтобы не взорвать время расчёта.
  for (let pass = 0; pass < 1 && !budgetExpired(); pass++) {
    let improved = false;
    // v111: 32³ комбинаций было главным источником зависания на больших планах.
    // Для локального пересчёта группы уменьшаем ядро ещё сильнее.
    const ids = movableIds().slice().sort((a,b)=>compareByScarcity(byInst[a], byInst[b])).slice(0, scopedGroupId ? 8 : 16);
    outer3:
    for (let i = 0; i < ids.length; i++) for (let j = 0; j < ids.length; j++) for (let k = 0; k < ids.length; k++) {
      if (i === j || j === k || i === k) continue;
      const idA = ids[i], idB = ids[j], idC = ids[k];
      const a0 = assignment[idA], b0 = assignment[idB], c0 = assignment[idC];
      if (!a0 || !b0 || !c0) continue;
      const before = cost();
      const origA = {...a0}, origB = {...b0}, origC = {...c0};
      unassign(idA); unassign(idB); unassign(idC);
      const placements = [
        [idA, byInst[idA], origB],
        [idB, byInst[idB], origC],
        [idC, byInst[idC], origA],
      ];
      let ok = true;
      for (const [id, inst, target] of placements) {
        if (!slotFreeForOwners(inst, target.day, target.period) || !dailyLoadLimitsOk(inst, target.day, target.period) || !dailyEdgePositionOk(inst, target.day, target.period) || !lecturePracticeOrderOk(inst, target.day, target.period)) { ok = false; break; }
        const room = freeRoomAt(inst, target.day, target.period);
        if (inst.format !== 'remote' && !room) { ok = false; break; }
        assign(id, target.day, target.period, room?.id || null, room?.extraRoomId || null);
      }
      const after = ok ? cost() : Infinity;
      if (ok && after < before) { compactMoves++; improved = true; break outer3; }
      for (const id of [idA,idB,idC]) if (assignment[id]) unassign(id);
      assign(idA, origA.day, origA.period, origA.roomId || null, origA.extraRoomId || null);
      assign(idB, origB.day, origB.period, origB.roomId || null, origB.extraRoomId || null);
      assign(idC, origC.day, origC.period, origC.roomId || null, origC.extraRoomId || null);
    }
    if (!improved) break;
  }

  const finalAssignment = JSON.parse(JSON.stringify(assignment));
  const finalUnplaced = new Set(unplacedSet);
  // v1600: a locked instance that couldn't be restored (see the pre-place
  // loop above) is deliberately kept out of `assignment` and out of the
  // normal placement pool, so it needs to be added to the reported unplaced
  // set explicitly here — otherwise it has no assignment, isn't in
  // `unplacedSet` (nothing ever added it there), and silently disappears
  // from both `assignment` and `unplaced` in the result instead of surfacing
  // as the conflict it actually is.
  for (const id of lockedIds) if (!finalAssignment[id]) finalUnplaced.add(id);

  // v104: финальный hard-pass удаляет назначения на закрытое время только
  // у совместителей/ГПХ. У штатных такие назначения допустимы как аварийный
  // резерв и остаются в сетке с жёлтым предупреждением.
  for (const inst of instances) {
    const a = finalAssignment[inst.instId];
    if (!a || lockedIds.has(inst.instId) || inst.isVacancyTeacher) continue;
    const finalTeacher = teacherById[inst.teacherId];
    if (isTeacherUnavailableForWeeks(finalTeacher, a.day, a.period, inst.weeks) && !isStaffTeacher(finalTeacher)) {
      delete finalAssignment[inst.instId];
      finalUnplaced.add(inst.instId);
    }
  }

  // v68: закрытый день группы — абсолютный запрет для автоматической расстановки.
  // Финальная проверка не даёт старому/оптимизированному автоназначению пережить блокировку.
  // Ручные locked-пары остаются допустимым осознанным исключением.
  for (const inst of instances) {
    const a = finalAssignment[inst.instId];
    if (!a || lockedIds.has(inst.instId)) continue;
    if (streamGroups(inst).some((gid) => groupDayBlockedForInstance(gid, inst, a.day))) {
      delete finalAssignment[inst.instId];
      finalUnplaced.add(inst.instId);
    }
  }

  // v1622: закрытый день блокирует только НОВУЮ автоматическую расстановку.
  // Уже стоящие и закреплённые занятия являются каркасом и не удаляются.
  // Это соответствует подписи кнопки «закрыть день» и позволяет доводить
  // расписание по группам без разрушения уже согласованной сетки.
  for (const inst of instances) {
    const a = finalAssignment[inst.instId];
    if (!a) continue;
    if (lockedIds.has(inst.instId)) continue;
    const blocked = streamGroups(inst).some((gid) => groupDayBlockedForInstance(gid, inst, a.day));
    if (blocked || !dailyLoadLimitsOk(inst, a.day, a.period)) {
      delete finalAssignment[inst.instId];
      finalUnplaced.add(inst.instId);
    }
  }

  // v140: заблокированный временной слот — жёсткий запрет для автоназначений.
  // Ручные locked-пары остаются допустимым исключением.
  for (const inst of instances) {
    const a = finalAssignment[inst.instId];
    if (!a || lockedIds.has(inst.instId)) continue;
    if (streamGroups(inst).some((gid) => groupTimeSlotBlockedForInstance(gid, inst, a.day, a.period))) {
      delete finalAssignment[inst.instId];
      finalUnplaced.add(inst.instId);
    }
  }

  // v1696: два слоя ОДНОЙ строки графика (например weeklyPairs=2 -> L1/L2)
  // никогда не могут занимать одну и ту же физическую пару на пересекающихся
  // неделях. Это структурное правило сильнее locked: lock фиксирует место
  // корректного занятия, но не является разрешением склеить L1 и L2 в один слот.
  // Именно такой legacy/graph-locked дубль давал две одинаковые карточки
  // «Экономика организации» в одной ячейке.
  const sameLoadSlotBuckets1696 = new Map();
  const sameLoadAssigned1696 = instances
    .filter((inst) => finalAssignment[inst.instId] && inst.loadId)
    .sort((a,b) => {
      const al = lockedIds.has(a.instId) ? 0 : 1;
      const bl = lockedIds.has(b.instId) ? 0 : 1;
      if (al !== bl) return al - bl;
      return String(a.instId).localeCompare(String(b.instId), 'ru', { numeric:true });
    });
  for (const inst of sameLoadAssigned1696) {
    const a = finalAssignment[inst.instId];
    if (!a) continue;
    let conflict = false;
    for (const week of weeksOf(inst)) {
      const base = `${inst.loadId}|${week}|${a.day}|${a.period}`;
      const keys = a.half == null ? [`${base}|full`, `${base}|0`, `${base}|1`] : [`${base}|full`, `${base}|${Number(a.half)}`];
      if (keys.some((k)=>(sameLoadSlotBuckets1696.get(k)||[]).length)) { conflict = true; break; }
    }
    if (conflict) {
      delete finalAssignment[inst.instId];
      finalUnplaced.add(inst.instId);
      lockedIds.delete(inst.instId);
      continue;
    }
    for (const week of weeksOf(inst)) {
      const k = `${inst.loadId}|${week}|${a.day}|${a.period}|${a.half == null ? 'full' : Number(a.half)}`;
      if (!sameLoadSlotBuckets1696.has(k)) sameLoadSlotBuckets1696.set(k, []);
      sameLoadSlotBuckets1696.get(k).push(inst.instId);
    }
  }

  // v1635: финальный абсолютный барьер двойной занятости преподавателя.
  // В нескольких специальных ветках (co-schedule/потоки/оптимизация) кандидат
  // мог попасть в assignment, минуя обычную проверку busyTeacherOccupants.
  // Поэтому перед выдачей результата ещё раз проверяем ФАКТИЧЕСКИЕ недели.
  // Автоматически разрешено только одно ранее согласованное исключение:
  // один преподаватель + две разные подгруппы ОДНОЙ группы + та же дисциплина/вид.
  // Locked/manual занятия никогда не снимаем: если авто столкнулось с locked,
  // снимается именно авто. Если столкнулись два авто — сохраняем первое
  // детерминированно, второе возвращаем в «Не размещено».
  const teacherFinalBuckets = new Map();
  const finalAssignedInstances = instances
    .filter((inst) => finalAssignment[inst.instId] && !inst.isVacancyTeacher && inst.teacherId)
    .sort((a, b) => {
      const al = lockedIds.has(a.instId) ? 0 : 1;
      const bl = lockedIds.has(b.instId) ? 0 : 1;
      if (al !== bl) return al - bl;
      return String(a.instId).localeCompare(String(b.instId), 'ru', { numeric:true });
    });
  for (const inst of finalAssignedInstances) {
    const a = finalAssignment[inst.instId];
    if (!a) continue;
    const weeks = weeksOf(inst);
    let conflict = false;
    for (const week of weeks) {
      const key = `${inst.teacherId}|${week}|${a.day}|${a.period}|${a.half == null ? 'full' : Number(a.half)}`;
      // Полная пара конфликтует и с full, и с любой половиной; половина — с full
      // и с той же половиной. Проверяем соседние ключи явно.
      const keys = a.half == null
        ? [`${inst.teacherId}|${week}|${a.day}|${a.period}|full`, `${inst.teacherId}|${week}|${a.day}|${a.period}|0`, `${inst.teacherId}|${week}|${a.day}|${a.period}|1`]
        : [`${inst.teacherId}|${week}|${a.day}|${a.period}|full`, key];
      for (const k of keys) {
        for (const otherId of (teacherFinalBuckets.get(k) || [])) {
          const other = byInst[otherId];
          if (!other || sameTeacherSiblingSubgroupsCompatible(inst, other)) continue;
          conflict = true;
          break;
        }
        if (conflict) break;
      }
      if (conflict) break;
    }
    if (conflict && !lockedIds.has(inst.instId)) {
      delete finalAssignment[inst.instId];
      finalUnplaced.add(inst.instId);
      continue;
    }
    // Если это locked и конфликтует с другим locked, ничего не удаляем: это
    // осознанный ручной конфликт, который должен быть виден пользователю.
    for (const week of weeks) {
      const k = `${inst.teacherId}|${week}|${a.day}|${a.period}|${a.half == null ? 'full' : Number(a.half)}`;
      if (!teacherFinalBuckets.has(k)) teacherFinalBuckets.set(k, []);
      teacherFinalBuckets.get(k).push(inst.instId);
    }
  }

  // v89: финальная статистика окон тоже считается по реальным неделям.
  // Нельзя объединять числитель и знаменатель: это создавало фантомные окна.
  const gTrackDp = new Map(), tDp = new Map();
  for (const inst of instances) {
    const a = finalAssignment[inst.instId];
    if (!a) continue;
    for (const week of weeksOf(inst)) {
      for (const gid of streamGroups(inst)) {
        for (const sg of affectedSubgroupsForGroup(inst, gid)) {
          const gk = `${gid}|${sg}|${week}|${a.day}`;
          if (!gTrackDp.has(gk)) gTrackDp.set(gk, new Set());
          gTrackDp.get(gk).add(a.period);
        }
      }
      const tk = `${inst.teacherId}|${week}|${a.day}`;
      if (!tDp.has(tk)) tDp.set(tk, new Set());
      tDp.get(tk).add(a.period);
    }
  }
  const finalGroupGapByBase = new Map();
  for (const [key, set] of gTrackDp.entries()) {
    const [gid, sg, week, day] = key.split('|');
    const base = `${gid}|${week}|${day}`;
    finalGroupGapByBase.set(base, Math.max(finalGroupGapByBase.get(base) || 0, gapCountForSet(set)));
  }
  let groupGaps = 0, teacherGaps = 0;
  groupGaps = [...finalGroupGapByBase.values()].reduce((a,b)=>a+b,0);
  for (const set of tDp.values()) {
    if (set.size === 0) continue;
    const arr = [...set];
    teacherGaps += Math.max(...arr) - Math.min(...arr) + 1 - set.size;
  }
  const gaps = groupGaps + teacherGaps;
  let availabilityConflicts = 0;
  let roomlessFinal = 0;
  let remoteCount = 0;
  let preferInPersonRemoteViolations = 0;
  for (const inst of instances) {
    const a = finalAssignment[inst.instId];
    if (a && isTeacherUnavailableForWeeks(teacherById[inst.teacherId], a.day, a.period, inst.weeks)) availabilityConflicts++;
    if (a && inst.format !== "remote" && !a.roomId) roomlessFinal++;
    if (a && inst.format === "remote") {
      remoteCount++;
      for (const gid of streamGroups(inst)) {
        if (groupById[gid]?.preferInPerson) preferInPersonRemoteViolations++;
      }
    }
  }

  const totalPairUnits = instances.reduce((sum, inst) => sum + (inst.halfPair ? 0.5 : 1), 0);
  const unplacedPairUnits = [...finalUnplaced].reduce((sum, id) => sum + (byInst[id]?.halfPair ? 0.5 : 1), 0);
  const stats = {
    totalPairs: totalPairUnits,
    placed: totalPairUnits - unplacedPairUnits,
    conflicts: unplacedPairUnits,
    gaps,
    groupGaps,
    teacherGaps,
    compactMoves,
    availabilityConflicts,
    roomless: roomlessFinal,
    remoteCount,
    remotePercent: totalPairUnits > 0 ? Math.round((instances.filter((i)=>finalAssignment[i.instId] && i.format === "remote").reduce((sum,i)=>sum+(i.halfPair?0.5:1),0) / totalPairUnits) * 100) : 0,
    preferInPersonRemoteViolations,
    remoteDayShortfalls: remoteDayDeficitTotal(),
    remoteDayPreferenceShortfalls: remoteDayDeficitTotal(),
  };

  return {
    assignment: finalAssignment,
    unplaced: [...finalUnplaced],
    stats,
    instances,
    locked: [...lockedIds].filter((id) => finalAssignment[id]),
    generationMeta: (scopedGroupId || fastMode) ? {
      strategy: fastMode ? (scopedGroupId ? "group-hierarchical" : "full-hierarchical") : (scopedGroupId ? "group-batches" : "full-batches"),
      batchSize: groupBatchSize,
      batchBudgetMs: groupBatchBudgetMs,
      batchesProcessed: groupBatchesProcessed,
      batchesTimedOut: groupBatchesTimedOut,
      primaryBudgetExpired,
      primaryElapsedMs: Date.now() - groupPrimaryStartedAt,
      ...(fastMode ? { unplacedReasons: perfScope?.fastUnplacedReasons || {} } : {}),
    } : undefined,
  };
}

function mergeGroupScopedResult(prior, groupId, result) {
  const targetIds = new Set((result.instances || [])
    .filter((inst) => belongsToGroup(inst, groupId) && streamGroups(inst).every((gid) => gid === groupId))
    .map((inst) => inst.instId));
  const mergedAssignment = { ...(result.assignment || {}) };
  for (const inst of result.instances || []) {
    if (targetIds.has(inst.instId)) continue;
    if (prior.assignment?.[inst.instId]) mergedAssignment[inst.instId] = prior.assignment[inst.instId];
    else delete mergedAssignment[inst.instId];
  }
  const mergedUnplaced = (result.instances || []).filter((inst) => !mergedAssignment[inst.instId]).map((inst) => inst.instId);
  const pairUnits = (inst) => inst?.halfPair ? 0.5 : 1;
  const byId = new Map((result.instances || []).map((inst) => [inst.instId, inst]));
  const totalPairs = (result.instances || []).reduce((sum, inst) => sum + pairUnits(inst), 0);
  const conflicts = mergedUnplaced.reduce((sum, id) => sum + pairUnits(byId.get(id)), 0);
  return {
    ...result,
    assignment: mergedAssignment,
    unplaced: mergedUnplaced,
    stats: { ...(result.stats || {}), totalPairs, placed: totalPairs - conflicts, conflicts },
  };
}

function groupProgressPatch(prior, groupId, result, seq = 0) {
  const priorInstances = Array.isArray(prior?.instances) ? prior.instances : [];
  const resultInstances = Array.isArray(result?.instances) ? result.instances : [];
  const isExclusiveTarget = (inst) => belongsToGroup(inst, groupId) && streamGroups(inst).every((gid) => gid === groupId);
  const priorTargetIds = new Set(priorInstances.filter(isExclusiveTarget).map((inst) => String(inst.instId)));
  const currentTargetInstances = resultInstances.filter(isExclusiveTarget);
  const currentTargetIds = new Set(currentTargetInstances.map((inst) => String(inst.instId)));
  const replaceIds = [...new Set([...priorTargetIds, ...currentTargetIds])];
  const instancesSet = Object.fromEntries(currentTargetInstances.map((inst) => [String(inst.instId), inst]));
  const instancesRemove = [...priorTargetIds].filter((id) => !currentTargetIds.has(id));
  const assignmentSet = {};
  const assignmentRemove = [];
  for (const id of replaceIds) {
    const value = result.assignment?.[id];
    if (value && currentTargetIds.has(id)) assignmentSet[id] = value;
    else assignmentRemove.push(id);
  }
  const unplacedTarget = (result.unplaced || []).map(String).filter((id) => currentTargetIds.has(id));
  const lockedTarget = (result.locked || []).map(String).filter((id) => currentTargetIds.has(id));
  return {
    seq,
    groupId,
    patch: {
      replaceIds,
      instancesSet,
      instancesRemove,
      assignmentSet,
      assignmentRemove,
      unplacedTarget,
      lockedTarget,
      stats: result.stats || {},
    },
    generationMeta: result.generationMeta || {},
  };
}

function fullProgressPatch(previous, result, seq = 0) {
  const prevInstances = Array.isArray(previous?.instances) ? previous.instances : [];
  const nextInstances = Array.isArray(result?.instances) ? result.instances : [];
  const prevById = new Map(prevInstances.map((inst) => [String(inst?.instId || ""), inst]).filter(([id]) => id));
  const nextById = new Map(nextInstances.map((inst) => [String(inst?.instId || ""), inst]).filter(([id]) => id));
  const allIds = new Set([...prevById.keys(), ...nextById.keys(), ...Object.keys(previous?.assignment || {}), ...Object.keys(result?.assignment || {})]);
  const instancesSet = {}, instancesRemove = [], assignmentSet = {}, assignmentRemove = [];
  const same = (a,b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
  for (const id of allIds) {
    const prevInst = prevById.get(id), nextInst = nextById.get(id);
    if (!same(prevInst, nextInst)) {
      if (nextInst) instancesSet[id] = nextInst;
      else if (prevInst) instancesRemove.push(id);
    }
    const pa = previous?.assignment?.[id], na = result?.assignment?.[id];
    if (!same(pa, na)) {
      if (na && typeof na === "object") assignmentSet[id] = na;
      else assignmentRemove.push(id);
    }
  }
  const prevUnplaced = new Set((previous?.unplaced || []).map(String));
  const nextUnplaced = new Set((result?.unplaced || []).map(String));
  const prevLocked = new Set((previous?.locked || []).map(String));
  const nextLocked = new Set((result?.locked || []).map(String));
  return {
    seq,
    patch: {
      scope: "full",
      instancesSet, instancesRemove, assignmentSet, assignmentRemove,
      unplacedSet: [...nextUnplaced].filter((id) => !prevUnplaced.has(id)),
      unplacedRemove: [...prevUnplaced].filter((id) => !nextUnplaced.has(id)),
      lockedSet: [...nextLocked].filter((id) => !prevLocked.has(id)),
      lockedRemove: [...prevLocked].filter((id) => !nextLocked.has(id)),
      stats: result?.stats || {},
    },
    generationMeta: result?.generationMeta || {},
  };
}

function generateFullSchedule(data, prior, progressCallback = null, fastMode = false) {
  let progressSeq = 0;
  let previous = prior || { instances: [], assignment: {}, unplaced: [], locked: [] };
  const onBatchProgress = typeof progressCallback === "function" ? (rawProgress) => {
    const patch = fullProgressPatch(previous, rawProgress, ++progressSeq);
    progressCallback(patch);
    previous = rawProgress;
  } : null;
  const result = generateSchedule(data, prior, { onBatchProgress, fastMode });
  // Финальная оптимизация идёт уже после последнего primary-пакета. Отдаём ещё
  // одну дельту относительно последнего опубликованного состояния, чтобы её
  // изменения тоже были сразу сохранены, а не потерялись при завершении job.
  if (typeof progressCallback === "function") {
    progressCallback(fullProgressPatch(previous, result, ++progressSeq));
  }
  return result;
}

function generateGroupSchedule(data, prior, groupId, progressCallback = null, fastMode = false) {
  if (!groupId || !prior?.assignment || !Array.isArray(prior?.instances)) return generateSchedule(data, prior, { fastMode });
  // Preserve every placement that belongs to another group. Shared stream lessons
  // are also protected because moving them would silently alter the timetable of
  // their other participant groups. Only lessons exclusive to target group may move.
  const protectedIds = prior.instances
    .filter((inst) => prior.assignment?.[inst.instId] && (!belongsToGroup(inst, groupId) || streamGroups(inst).some((gid) => gid !== groupId)))
    .map((inst) => inst.instId);
  const scopedPrior = { ...prior, locked: [...new Set([...(prior.locked || []), ...protectedIds])] };
  let progressSeq = 0;
  const onBatchProgress = typeof progressCallback === "function" ? (rawProgress) => {
    const merged = mergeGroupScopedResult(prior, groupId, rawProgress);
    merged.locked = [...new Set([...(prior.locked || []), ...protectedIds])].filter((id) => merged.assignment[id]);
    progressCallback(groupProgressPatch(prior, groupId, merged, ++progressSeq));
  } : null;
  const result = generateSchedule(data, scopedPrior, { groupId, onBatchProgress, fastMode });
  const merged = mergeGroupScopedResult(prior, groupId, result);
  merged.locked = [...new Set([...(prior.locked || []), ...protectedIds])].filter((id) => merged.assignment[id]);
  return merged;
}


function generateWeekSchedule(data, prior, weekNumber, groupId = "", progressCallback = null, fastMode = true) {
  const week = Math.max(1, Number(weekNumber || 0));
  if (!prior?.assignment || !Array.isArray(prior?.instances) || !week) return generateSchedule(data, prior, { groupId, weekNumber: week, fastMode });
  const inTarget = (inst) => weekNumbersForInstance(data.config, inst).map(Number).includes(week) && (!groupId || belongsToGroup(inst, groupId));
  const targetIds = new Set((prior.instances || []).filter(inTarget).map((inst)=>String(inst.instId)));
  const protectedAssigned = (prior.instances || []).filter((inst)=>!targetIds.has(String(inst.instId)) && prior.assignment?.[inst.instId]).map((inst)=>inst.instId);
  const scopedPrior = { ...prior, locked:[...new Set([...(prior.locked || []), ...protectedAssigned])] };
  const mergeResult = (raw) => {
    const rawById = new Map((raw?.instances || []).map((inst)=>[String(inst.instId), inst]));
    const instances = (prior.instances || []).map((inst)=>targetIds.has(String(inst.instId)) && rawById.has(String(inst.instId)) ? rawById.get(String(inst.instId)) : inst);
    const assignment = { ...(prior.assignment || {}) };
    for (const id of targetIds) delete assignment[id];
    for (const id of targetIds) if (raw?.assignment?.[id]) assignment[id] = raw.assignment[id];
    const unplaced = [...new Set([...(prior.unplaced || []).filter((id)=>!targetIds.has(String(id))), ...(raw?.unplaced || []).filter((id)=>targetIds.has(String(id)))])];
    const locked = [...new Set([...(prior.locked || []).filter((id)=>!targetIds.has(String(id))), ...(raw?.locked || []).filter((id)=>targetIds.has(String(id)))])].filter((id)=>assignment[id]);
    return { ...raw, instances, assignment, unplaced, locked, generationMeta:{ ...(raw?.generationMeta || {}), weekNumber:week, weeklyMode:true } };
  };
  let seq = 0;
  let previous = prior;
  const onBatchProgress = typeof progressCallback === "function" ? (raw) => {
    const merged = mergeResult(raw);
    progressCallback(fullProgressPatch(previous, merged, ++seq));
    previous = merged;
  } : null;
  const raw = generateSchedule(data, scopedPrior, { groupId, weekNumber:week, onBatchProgress, fastMode });
  const merged = mergeResult(raw);
  if (typeof progressCallback === "function") progressCallback(fullProgressPatch(previous, merged, ++seq));
  return merged;
}

function emptyStats() {
  return { totalPairs: 0, placed: 0, conflicts: 0, gaps: 0, groupGaps: 0, teacherGaps: 0, compactMoves: 0, availabilityConflicts: 0, roomless: 0 };
}


function syncScheduleInstancesToGraph(data, nextLoads) {
  const prior = data?.schedule;
  if (!prior || !Array.isArray(prior.instances)) return prior;
  const safeLoads = ensureUniqueIds(nextLoads || [], "load");
  const source = { ...data, loads: safeLoads };
  const skeletonRaw = generateSchedule(source, null, { instancesOnly: true });
  // Защитный слой на случай старых/импортированных данных: в каноническом
  // наборе не должно быть двух экземпляров с одним instId. Иначе assignment
  // одного занятия начинает использоваться другим.
  const skeleton = normalizeScheduleInstanceIds(skeletonRaw);
  let instances = (skeleton.instances || []).map((i) => ({ ...i, customWeeks: [...(i.customWeeks || [])] }));

  // Ручные расщепления v145.6 живут только в расписании. Сохраняем размещённые
  // куски, пока соответствующие недели всё ещё присутствуют в графике нагрузки.
  const byId = new Map(instances.map((i) => [i.instId, i]));
  const splitChildren = (prior.instances || []).filter((i) => i?.manualSplit && !i?.manualRecurringSplit && i?.splitFromInstId && i.instId !== i.splitFromInstId);
  for (const old of splitChildren) {
    const base = byId.get(old.splitFromInstId);
    if (!base) continue;
    const allowed = new Set(weekNumbersForInstance(source.config, base));
    const keepWeeks = weekNumbersForInstance(source.config, old).filter((w) => allowed.has(w));
    if (!keepWeeks.length) continue;
    if (base.weekPattern === 'custom') base.customWeeks = (base.customWeeks || []).filter((w) => !keepWeeks.includes(Number(w)));
    const child = { ...old, customWeeks: keepWeeks, weekPattern: 'custom' };
    instances.push(child);
    byId.set(child.instId, child);
  }
  instances = instances.filter((i) => i.weekPattern !== 'custom' || (i.customWeeks || []).length > 0);

  // v1567: preserve manual splits made from a visually grouped recurring block.
  // Each fragment remembers the original generated source IDs. We rebuild those
  // fragments only while the same graph-generated sources still exist and remain
  // structurally compatible. If the graph changes, the split is dissolved safely.
  const recurringSplitRows = (prior.instances || []).filter((i)=>i?.manualRecurringSplit && Array.isArray(i?.recurringSourceIds) && i.recurringSourceIds.length > 1);
  const recurringSplitGroups = new Map();
  for (const row of recurringSplitRows) {
    const sourceIds = [...new Set((row.recurringSourceIds || []).map(String).filter(Boolean))].sort();
    const key = sourceIds.join('|');
    if (!recurringSplitGroups.has(key)) recurringSplitGroups.set(key, { sourceIds, rows:[] });
    recurringSplitGroups.get(key).rows.push(row);
  }
  for (const { sourceIds, rows } of recurringSplitGroups.values()) {
    const currentById = new Map(instances.map((i)=>[String(i.instId),i]));
    const sourceRows = sourceIds.map((id)=>currentById.get(id)).filter(Boolean);
    if (sourceRows.length !== sourceIds.length) continue;
    const structural = (inst) => JSON.stringify({
      loadId:inst?.loadId||"", groupId:inst?.groupId||"", teacherId:inst?.teacherId||"",
      subjectId:inst?.subjectId||"", typeId:inst?.typeId||"", subgroup:Number(inst?.subgroup||0),
      format:inst?.format||"inperson", halfPair:!!inst?.halfPair,
      streamGroupIds:[...(inst?.streamGroupIds||[])].map(String).sort(),
      streamParticipants:(inst?.streamParticipants||[]).map((p)=>`${p.groupId||""}:${Number(p.subgroup||0)}:${p.loadId||""}`).sort(),
    });
    if (rows.some((old)=>sourceRows.some((src)=>structural(src)!==structural(old)))) continue;
    const allowedWeeks = new Set(sourceRows.flatMap((row)=>weekNumbersForInstance(source.config,row)).map(Number));
    const rebuilt = [];
    const usedWeeks = new Set();
    for (const old of rows) {
      const keepWeeks = weekNumbersForInstance(source.config,old).map(Number).filter((w)=>allowedWeeks.has(w) && !usedWeeks.has(w)).sort((a,b)=>a-b);
      if (!keepWeeks.length) continue;
      keepWeeks.forEach((w)=>usedWeeks.add(w));
      rebuilt.push({ ...old, weekPattern:'custom', customWeeks:keepWeeks, manualRecurringSplit:true, recurringSourceIds:[...sourceIds] });
    }
    if (!rebuilt.length) continue;
    const sourceSet = new Set(sourceIds);
    instances = instances.filter((row)=>!sourceSet.has(String(row.instId)));
    instances.push(...rebuilt);
  }

  // v1566: preserve a recurring block that the user explicitly placed as one
  // grouped card. The graph is still authoritative: consolidation survives only
  // while every original generated instance still exists and their week union is
  // exactly the same. Any graph change dissolves the consolidation and therefore
  // returns the changed requirement to «Не размещено».
  const generatedById = new Map(instances.map((i)=>[i.instId, i]));
  const recurringMerges = (prior.instances || []).filter((i)=>i?.manualRecurringMerge && Array.isArray(i?.mergedFromInstIds) && i.mergedFromInstIds.length > 1);
  for (const old of recurringMerges) {
    const sourceIds = [...new Set((old.mergedFromInstIds || []).map(String).filter(Boolean))];
    const sourceRows = sourceIds.map((id)=>generatedById.get(id)).filter(Boolean);
    if (sourceRows.length !== sourceIds.length) continue;
    const structural = (inst) => JSON.stringify({
      loadId:inst?.loadId||"", groupId:inst?.groupId||"", teacherId:inst?.teacherId||"",
      subjectId:inst?.subjectId||"", typeId:inst?.typeId||"", subgroup:Number(inst?.subgroup||0),
      format:inst?.format||"inperson", halfPair:!!inst?.halfPair,
      streamGroupIds:[...(inst?.streamGroupIds||[])].map(String).sort(),
      streamParticipants:(inst?.streamParticipants||[]).map((p)=>`${p.groupId||""}:${Number(p.subgroup||0)}:${p.loadId||""}`).sort(),
    });
    if (sourceRows.some((row)=>structural(row)!==structural(old))) continue;
    const currentWeeks = [...new Set(sourceRows.flatMap((row)=>weekNumbersForInstance(source.config,row)).map(Number))].sort((a,b)=>a-b);
    const oldWeeks = [...new Set(weekNumbersForInstance(source.config,old).map(Number))].sort((a,b)=>a-b);
    if (JSON.stringify(currentWeeks) !== JSON.stringify(oldWeeks)) continue;
    const sourceSet = new Set(sourceIds);
    instances = instances.filter((row)=>!sourceSet.has(String(row.instId)));
    const keep = { ...old, weekPattern:'custom', customWeeks:currentWeeks, manualRecurringMerge:true, mergedFromInstIds:sourceIds };
    instances.push(keep);
    for (const id of sourceIds) generatedById.delete(id);
    generatedById.set(keep.instId, keep);
  }

  const validIds = new Set(instances.map((i) => i.instId));
  const assignment = {};
  const priorById = new Map((prior.instances || []).map((i) => [i.instId, i]));
  const occurrenceSignature = (inst) => {
    if (!inst) return "";
    const weeks = weekNumbersForInstance(source.config, inst).map(Number).sort((a,b)=>a-b);
    const participants = (inst.streamParticipants || [])
      .map((p)=>`${p.groupId || ""}:${Number(p.subgroup || 0)}:${p.loadId || ""}`)
      .sort();
    const streamGroups = [...(inst.streamGroupIds || [])].map(String).sort();
    return JSON.stringify({
      loadId: inst.loadId || "", groupId: inst.groupId || "", teacherId: inst.teacherId || "",
      subjectId: inst.subjectId || "", typeId: inst.typeId || "", subgroup: Number(inst.subgroup || 0),
      format: inst.format || "inperson", halfPair: !!inst.halfPair, weeks,
      streamGroups, participants,
      manualSplit: !!inst.manualSplit, splitFromInstId: inst.splitFromInstId || "",
    });
  };
  // v1551: редактирование графика само по себе НИКОГДА не размещает новые пары.
  // Назначение сохраняется только если соответствующий экземпляр графика вообще
  // не изменился: те же недели, формат, подгруппа и потоковые участники.
  // Если в график добавили/убрали/перенесли неделю, весь изменённый исходный блок
  // остаётся целым и попадает в «Не размещено» до Авто или ручной постановки.
  for (const [id, oldA] of Object.entries(prior.assignment || {})) {
    if (!validIds.has(id) || !oldA) continue;
    const oldInst = priorById.get(id);
    const nextInst = instances.find((i)=>i.instId===id);
    if (!oldInst || !nextInst) continue;
    if (occurrenceSignature(oldInst) !== occurrenceSignature(nextInst)) continue;
    assignment[id] = { ...oldA };
  }

  // v1695: разные слои одной строки графика нельзя хранить в одной физической
  // ячейке. Старое ошибочное состояние L1/L2 в одном day/period разворачиваем:
  // младший слой остаётся, следующий снова становится неразмещённым.
  const instById1695 = new Map(instances.map((x)=>[String(x?.instId||""),x]));
  const layerRank1695 = (inst) => {
    if (inst?.halfPair) return 100000;
    const id=String(inst?.instId||"");
    let m=id.match(/__L(\d+)$/); if(m) return Number(m[1])||1;
    m=id.match(/__(?:LECTURE_STREAM|PRACTICAL_STREAM)_\d+_([^_]+)_/); if(m) return Number(String(m[1]).replace(/^L/,""))||1;
    m=id.match(/__(\d+)$/); if(m) return (Number(m[1])||0)+1;
    return 1;
  };
  const assignedIds1695=Object.keys(assignment).filter((id)=>assignment[id]);
  for(let i=0;i<assignedIds1695.length;i++){
    const aId=assignedIds1695[i], a=assignment[aId], ai=instById1695.get(String(aId));
    if(!a||!ai) continue;
    for(let j=i+1;j<assignedIds1695.length;j++){
      const bId=assignedIds1695[j], b=assignment[bId], bi=instById1695.get(String(bId));
      if(!b||!bi||!ai.loadId||String(ai.loadId)!==String(bi.loadId||"")) continue;
      if(Number(a.day)!==Number(b.day)||Number(a.period)!==Number(b.period)) continue;
      const aw=new Set(weekNumbersForInstance(source.config,ai).map(Number));
      if(!weekNumbersForInstance(source.config,bi).some((w)=>aw.has(Number(w)))) continue;
      const ar=layerRank1695(ai), br=layerRank1695(bi);
      const victim=br>ar?bId:(ar>br?aId:(String(aId)>String(bId)?aId:bId));
      delete assignment[victim];
    }
  }

  // v1546: график — источник истины. Синхронизация структуры расписания
  // никогда не расщепляет и не размещает новые строки автоматически по соседней
  // подгруппе/потоку. Новые экземпляры остаются целыми и неразмещёнными до
  // автогенерации или явного ручного вмешательства пользователя.
  const autoPartnerLocked = new Set();

  const locked = [...new Set([...(prior.locked || []).filter((id) => validIds.has(id) && assignment[id]), ...autoPartnerLocked])];
  const unplaced = [];
  for (const inst of instances) {
    // v1546: unplaced выводится из фактического assignment после синхронизации
    // с графиком. Нельзя одновременно иметь назначение и предупреждение «не размещено».
    if (!assignment[inst.instId]) unplaced.push(inst.instId);
  }
  const occurrenceUnits = (i) => (i.halfPair ? 0.5 : 1) * weekNumbersForInstance(source.config, i).length;
  const totalPairs = instances.reduce((sum, i) => sum + occurrenceUnits(i), 0);
  const placed = instances.reduce((sum, i) => sum + (assignment[i.instId] ? occurrenceUnits(i) : 0), 0);
  const unplacedSet = [...new Set(unplaced)];
  const conflicts = unplacedSet.reduce((sum,id)=>sum+occurrenceUnits(instances.find((i)=>i.instId===id)||{}),0);
  return {
    ...prior,
    instances, assignment, locked, unplaced: unplacedSet,
    stats: { ...(prior.stats || {}), totalPairs, placed, conflicts },
    graphSyncedAt: new Date().toISOString(),
  };
}



// v1564: серверные индексы занятости для быстрой ручной диагностики.
function buildDiagnosticOccupancyIndex(instances, assignment) {
  const byInst = new Map((instances || []).map((inst) => [inst.instId, inst]));
  const groupSlot = new Map(), teacherSlot = new Map(), roomSlot = new Map(), groupDay = new Map(), teacherDay = new Map();
  const add = (map, key, instId) => { let set = map.get(key); if (!set) { set = new Set(); map.set(key, set); } set.add(instId); };
  for (const [instId, a] of Object.entries(assignment || {})) {
    if (!a) continue; const inst = byInst.get(instId); if (!inst) continue;
    const day = Number(a.day), period = Number(a.period), sk = `${day}_${period}`;
    for (const gid of streamGroups(inst)) { add(groupSlot, `${sk}|${gid}`, instId); add(groupDay, `${day}|${gid}`, instId); }
    if (!inst.isVacancyTeacher && inst.teacherId) { add(teacherSlot, `${sk}|${inst.teacherId}`, instId); add(teacherDay, `${day}|${inst.teacherId}`, instId); }
    for (const rid of assignmentRoomIds(a)) add(roomSlot, `${sk}|${rid}`, instId);
  }
  return { byInst, groupSlot, teacherSlot, roomSlot, groupDay, teacherDay };
}

function diagnosticFastCanPlace(data, assignment, index, inst, day, period, roomId) {
  const rooms = data.rooms || [], config = data.config || null;
  const overlaps = (other) => !!other && weeksOverlap(other, inst, config);
  const sameParticipantGroup = (other) => streamGroups(inst).some((gid) => belongsToGroup(other, gid) && participantSubgroupsOverlap(other, inst, gid));
  let selectedRoom = null;
  if (inst.format !== 'remote') {
    selectedRoom = rooms.find((r)=>r.id===roomId);
    if (!selectedRoom) return {ok:false, reason:'Аудитория не найдена'};
    if (inst.dedicatedRoomId && !roomIdsEquivalent(rooms, roomId, inst.dedicatedRoomId)) return {ok:false, reason:'За преподавателем закреплена другая основная аудитория'};
    if (!inst.dedicatedRoomId && inst.allowedRoomIds?.length && !inst.allowedRoomIds.some((rid)=>roomIdsEquivalent(rooms,rid,roomId))) return {ok:false,reason:'Аудитория не входит в список допустимых для занятия'};
    if (!inst.dedicatedRoomId && !inst.allowedRoomIds?.length && inst.roomType !== 'любая' && selectedRoom.typeId !== inst.roomType) return {ok:false,reason:'Нужна аудитория другого типа'};
    if (inst.requiresComputer && !selectedRoom.hasComputers) return {ok:false,reason:'Аудитория должна быть с компьютерами'};
    if (inst.requiresArt && !selectedRoom.isArtRoom) return {ok:false,reason:'Нужен художественный кабинет'};
    if (inst.requiredRoomTypeId && selectedRoom.typeId !== inst.requiredRoomTypeId) return {ok:false,reason:'Дисциплина требует другой тип аудитории'};
    // v1599: вместимость — soft constraint; диагностическая проверка не блокирует слот.
  }
  const dayCandidates = new Set();
  for (const gid of streamGroups(inst)) for (const id of (index.groupDay.get(`${day}|${gid}`)||[])) dayCandidates.add(id);
  if (!inst.isVacancyTeacher && inst.teacherId) for (const id of (index.teacherDay.get(`${day}|${inst.teacherId}`)||[])) dayCandidates.add(id);
  for (const id of dayCandidates) { const other=index.byInst.get(id); if (!other || !overlaps(other) || other.format===inst.format) continue;
    if (sameParticipantGroup(other)) return {ok:false,reason:`В этот день у группы уже стоят занятия ${other.format==='remote'?'дистанционно':'очно'} на пересекающихся неделях — весь день недели должен быть в одном формате`};
    if (!inst.isVacancyTeacher && !other.isVacancyTeacher && other.teacherId===inst.teacherId) return {ok:false,reason:`В этот день у преподавателя уже стоят занятия ${other.format==='remote'?'дистанционно':'очно'} на пересекающихся неделях — весь день недели должен быть в одном формате`};
  }
  const sk=`${day}_${period}`, candidates=new Set();
  for (const gid of streamGroups(inst)) for (const id of (index.groupSlot.get(`${sk}|${gid}`)||[])) candidates.add(id);
  if (!inst.isVacancyTeacher && inst.teacherId) for (const id of (index.teacherSlot.get(`${sk}|${inst.teacherId}`)||[])) candidates.add(id);
  if (selectedRoom) for (const r of rooms) if (roomIdsEquivalent(rooms,r.id,selectedRoom.id)) for (const id of (index.roomSlot.get(`${sk}|${r.id}`)||[])) candidates.add(id);
  const halfCandidates = inst.halfPair ? [0,1] : [null];
  let firstReason='Конфликт расписания';
  for (const half of halfCandidates) { let reason='';
    const teacherPartners=[...candidates].filter((id)=>{ const other=index.byInst.get(id), a=assignment?.[id]; if(!other||!a||!overlaps(other)) return false; if(!(a.half==null||half==null||Number(a.half)===Number(half))) return false; return !inst.isVacancyTeacher&&!other.isVacancyTeacher&&other.teacherId===inst.teacherId; });
    const allowedSiblingTeacherId=teacherPartners.length===1&&sameTeacherSiblingSubgroupsCompatible(inst,index.byInst.get(teacherPartners[0]))?teacherPartners[0]:null;
    for (const id of candidates) { const other=index.byInst.get(id), a=assignment?.[id]; if (!other || !a || !overlaps(other)) continue;
      if (!(a.half==null || half==null || Number(a.half)===Number(half))) continue;
      if (!inst.isVacancyTeacher && !other.isVacancyTeacher && other.teacherId===inst.teacherId && id!==allowedSiblingTeacherId) {reason='Преподаватель занят в это время на пересекающихся неделях';break;}
      if (sameParticipantGroup(other)) {reason='У группы/подгруппы уже есть занятие в это время на пересекающихся неделях';break;}
      if (id!==allowedSiblingTeacherId && selectedRoom && assignmentRoomIds(a).some((rid)=>roomIdsEquivalent(rooms,rid,selectedRoom.id))) {reason='Аудитория занята в это время на пересекающихся неделях';break;}
    }
    if (!reason) return {ok:true,half}; firstReason=reason;
  }
  return {ok:false,reason:firstReason};
}

function diagnosePlacementOptions(data, prior, payload = {}) {
  const schedule = prior || data.schedule || {};
  const sourceInstances = Array.isArray(schedule.instances) ? schedule.instances : [];
  const sourceById = new Map(sourceInstances.map((i)=>[i.instId,i]));
  const requestedIds = Array.isArray(payload.instIds) ? payload.instIds.map(String) : [];
  const firstId = requestedIds[0] || String(payload.instId || '');
  const baseRaw = sourceById.get(firstId);
  const base = baseRaw ? enrichInstanceStreamGroups(data, baseRaw) : null;
  if (!base) return { tested:0, reasons:[{key:'other',label:'занятие не найдено',count:1}], options:[], optionCount:0 };
  const requestedWeeks = Array.isArray(payload.weeks) && payload.weeks.length ? payload.weeks : requestedIds.flatMap((id)=>{const i=sourceById.get(id);return i?weekNumbersForInstance(data.config,i):[];});
  const weeks = [...new Set(requestedWeeks.map(Number).filter(Boolean))].sort((a,b)=>a-b);
  const diagId = `__DIAG__${firstId}`;
  const diagInst = { ...base, instId:diagId, weekPattern:'custom', customWeeks:weeks, manualSplit:false, userManualSplit:false, splitFromInstId:'' };
  const excluded = new Set(requestedIds);
  const instances = sourceInstances.filter((i)=>!excluded.has(i.instId)).concat(diagInst);
  const assignment = {};
  for (const [id,a] of Object.entries(schedule.assignment || {})) if (!excluded.has(id) && a) assignment[id] = a;
  const occupancyIndex = buildDiagnosticOccupancyIndex(instances, assignment);
  const groupDayBlockIndex = new Set((data.groupDayBlocks || []).map((b)=>`${b.groupId}|${b.date}`));
  const teacherById = new Map((data.teachers || []).map((t)=>[t.id,t]));
  const targetDay = Number.isInteger(Number(payload.targetDay)) && payload.targetDay !== null && payload.targetDay !== undefined ? Number(payload.targetDay) : null;
  const targetPeriod = Number.isInteger(Number(payload.targetPeriod)) && payload.targetPeriod !== null && payload.targetPeriod !== undefined ? Number(payload.targetPeriod) : null;
  const configuredDays = (data.config?.activeDays || []).map((on,i)=>on?i:null).filter((x)=>x!==null);
  const activeDays = targetDay === null ? configuredDays : configuredDays.filter((d)=>d===targetDay);
  const periods = Number(data.config?.periodsPerDay || 6);
  const counters = {teacher:0,group:0,room:0,roomType:0,calendar:0,other:0};
  const options = [];
  const classify = (reason='') => { const r=String(reason).toLowerCase(); if(r.includes('преподав'))return 'teacher'; if(r.includes('групп')||r.includes('подгруп'))return 'group'; if(r.includes('аудитор')||r.includes('мест'))return r.includes('тип')||r.includes('компьют')||r.includes('художе')?'roomType':'room'; return 'other'; };
  let tested=0;
  for (const day of activeDays) {
    const periodValues = targetPeriod === null ? Array.from({length: periods}, (_,i)=>i) : (targetPeriod >= 0 && targetPeriod < periods ? [targetPeriod] : []);
    for (const period of periodValues) {
      tested++;
      const teacher = teacherById.get(diagInst.teacherId);
      const teacherUnavailable = !!teacher && !diagInst.isVacancyTeacher && isTeacherUnavailableForWeeks(teacher, day, period, weeks);
      const dates = weeks.map((week)=>addDays(addDays(mondayOf(data.config.semesterStart), (week - 1) * 7), day))
        .filter((date)=>date >= data.config.semesterStart && date <= data.config.semesterEnd && instanceAppliesToDate(data.config, diagInst, date));
      const participantGroupIds = streamGroups(diagInst);
      const dayBlocked = dates.some((date)=>participantGroupIds.some((gid)=>groupDayBlockIndex.has(`${gid}|${date}`)));
      const softWarnings = [];
      if (dayBlocked) softWarnings.push('день закрыт для группы');
      if (teacherUnavailable) softWarnings.push('преподаватель вне доступности');
      if (diagInst.format === 'remote') {
        const check = diagnosticFastCanPlace(data, assignment, occupancyIndex, diagInst, day, period, null);
        if (check.ok) options.push({day,period,roomId:null,roomName:'ДО',warning:softWarnings.length>0,warnings:softWarnings});
        else counters[classify(check.reason)]++;
        continue;
      }
      let found=null, firstReason='';
      const candidateRooms = diagInst.dedicatedRoomId
        ? (data.rooms || []).filter((room)=>roomIdsEquivalent(data.rooms || [], room.id, diagInst.dedicatedRoomId))
        : (data.rooms || []);
      for (const room of candidateRooms) {
        const check = diagnosticFastCanPlace(data, assignment, occupancyIndex, diagInst, day, period, room.id);
        if (check.ok) { found=room; break; }
        if (!firstReason) firstReason=check.reason || '';
      }
      if (found) options.push({day,period,roomId:found.id,roomName:found.name || '',warning:softWarnings.length>0,warnings:softWarnings});
      else counters[classify(firstReason || (diagInst.dedicatedRoomId ? 'закреплённая аудитория недоступна' : 'нет свободной аудитории'))]++;
    }
  }
  const labels={teacher:'преподаватель занят',group:'группа/подгруппа занята',room:'нет свободной аудитории',roomType:'нет подходящей аудитории',calendar:'день недоступен',other:'другое ограничение'};
  const reasons=Object.entries(counters).filter(([,count])=>count>0).map(([key,count])=>({key,label:labels[key],count}));
  return {tested,reasons,options:options.slice(0,24),optionCount:options.length};
}



import fs from "node:fs";
import readline from "node:readline";

function executeSchedulerPayload(payload, preparedData = null) {
  const data = preparedData || normalizeStoredData({ ...buildSeed(), ...(payload.data || {}) });
  const prior = payload.prior ?? data.schedule ?? null;
  const started = Date.now();
  if (payload.mode === "options") {
    const result = diagnosePlacementOptions(data, prior, payload);
    return { ok: true, elapsedMs: Date.now() - started, result };
  }
  const groupId = String(payload.groupId || "").trim();
  const progressFile = String(payload.progressFile || "").trim();
  const writeProgress = progressFile ? (progress) => {
    try {
      fs.appendFileSync(progressFile, `${JSON.stringify(progress)}\n`, "utf8");
    } catch (err) {
      process.stderr.write(`progress-write-warning: ${String(err?.message || err)}\n`);
    }
  } : null;
  const fastMode = !!payload.fastMode;
  const weekNumber = Math.max(0, Number(payload.weekNumber || 0));
  const result = weekNumber
    ? generateWeekSchedule(data, prior, weekNumber, groupId, writeProgress, fastMode)
    : (groupId ? generateGroupSchedule(data, prior, groupId, writeProgress, fastMode) : generateFullSchedule(data, prior, writeProgress, fastMode));
  const finalPatch = weekNumber ? fullProgressPatch(prior || {}, result, 0) : (groupId ? groupProgressPatch(prior || {}, groupId, result, 0) : undefined);
  return { ok: true, elapsedMs: Date.now() - started, result, finalPatch };
}

async function workerMain() {
  // v1575: long-lived scheduler worker. It never writes canonical storage itself;
  // it only caches normalized input snapshots in RAM and returns calculations.
  // Restarting or killing the worker therefore cannot delete project data.
  const cache = new Map();
  const CACHE_MAX = Math.max(1, Number(process.env.SCHEDULER_WORKER_CACHE_MAX || 2));
  const remember = (key, value) => {
    if (!key) return;
    if (cache.has(key)) cache.delete(key);
    cache.set(key, value);
    while (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
  };
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of rl) {
    if (!line.trim()) continue;
    let requestId = "";
    try {
      const msg = JSON.parse(line);
      requestId = String(msg.requestId || "");
      const cacheKey = String(msg.cacheKey || "");
      let data = cacheKey ? cache.get(cacheKey) : null;
      if (!data && msg.data && typeof msg.data === "object") {
        data = normalizeStoredData({ ...buildSeed(), ...msg.data });
        remember(cacheKey, data);
      }
      if (!data) throw new Error("worker cache miss: canonical data snapshot was not supplied");
      const payload = { ...(msg.payload || {}) };
      const response = executeSchedulerPayload(payload, data);
      process.stdout.write(`${JSON.stringify({ requestId, ...response })}\n`);
    } catch (err) {
      process.stdout.write(`${JSON.stringify({ requestId, ok:false, error:String(err?.stack || err || "Unknown scheduler error") })}\n`);
    }
  }
}

async function main() {
  if (process.argv.includes("--worker")) {
    await workerMain();
    return;
  }
  const inputText = fs.readFileSync(0, "utf8");
  const payload = JSON.parse(inputText || "{}");
  const response = executeSchedulerPayload(payload);
  process.stdout.write(JSON.stringify(response));
}
main().catch((err) => { process.stderr.write(String(err?.stack || err || "Unknown scheduler error")); process.exit(1); });
