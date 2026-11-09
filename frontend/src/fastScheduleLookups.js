// v1876: immutable-array indexes for hot read paths. These are presentation-only
// lookups: they never change a lesson, graph, assignment, substitution or storage.
// All indexes are keyed by the original array identity and automatically fall out
// of use when React receives a new immutable project snapshot.
const calendarByArray = new WeakMap();
const holidayByArray = new WeakMap();
const practiceByArray = new WeakMap();
const substitutionsByArray = new WeakMap();
const movedSourceByArray = new WeakMap();

function firstByDate(rows) {
  if (!Array.isArray(rows)) return null;
  let index = calendarByArray.get(rows);
  if (!index) {
    index = new Map();
    for (const row of rows) if (row && !index.has(row.date)) index.set(row.date, row);
    calendarByArray.set(rows, index);
  }
  return index;
}

// Match the old concatenated-array .find semantics: the first exception in
// data.calendarExceptions wins over any matching config.calendarExceptions row.
export function calendarExceptionForFast(data, date) {
  const primary = firstByDate(data?.calendarExceptions);
  if (primary?.has(date)) return primary.get(date);
  return firstByDate(data?.config?.calendarExceptions)?.get(date) || null;
}

export function isHolidayFast(config, date) {
  const holidays = config?.holidays;
  if (!Array.isArray(holidays)) return !!holidays?.includes?.(date);
  let index = holidayByArray.get(holidays);
  if (!index) { index = new Set(holidays); holidayByArray.set(holidays, index); }
  return index.has(date);
}

// Preserve .find's original first-match rule for overlapping practice periods.
// Raw group ids are Map keys (no coercion), matching the former strict ===.
export function groupPracticeForDateFast(data, groupId, date) {
  const practices = data?.practices;
  if (!Array.isArray(practices)) return null;
  let byGroup = practiceByArray.get(practices);
  if (!byGroup) {
    byGroup = new Map();
    for (const practice of practices) {
      if (!practice) continue;
      if (!byGroup.has(practice.groupId)) byGroup.set(practice.groupId, []);
      byGroup.get(practice.groupId).push(practice);
    }
    practiceByArray.set(practices, byGroup);
  }
  return (byGroup.get(groupId) || []).find((p) => p.dateStart <= date && date <= p.dateEnd) || null;
}

// A published weekly split has a visible id and possibly a source id. If both
// have edits on one date, the EARLIEST substitution in the original array wins.
export function findSubstitutionFast(substitutions, date, visibleId, sourceId = "") {
  if (!Array.isArray(substitutions)) return null;
  let byDate = substitutionsByArray.get(substitutions);
  if (!byDate) {
    byDate = new Map();
    substitutions.forEach((sub, order) => {
      if (!sub) return;
      if (!byDate.has(sub.date)) byDate.set(sub.date, new Map());
      const dateMap = byDate.get(sub.date);
      const key = String(sub.instId || "");
      if (!dateMap.has(key)) dateMap.set(key, { sub, order });
    });
    substitutionsByArray.set(substitutions, byDate);
  }
  const dateMap = byDate.get(date);
  if (!dateMap) return null;
  const direct = dateMap.get(String(visibleId || ""));
  const source = sourceId ? dateMap.get(String(sourceId)) : null;
  if (!direct) return source?.sub || null;
  if (!source) return direct.sub;
  return direct.order <= source.order ? direct.sub : source.sub;
}

// Reused by the public semester renderer and the factual-teaching report.
export function indexMovedSubstitutionsByDate(substitutions) {
  const index = new Map();
  for (const sub of (substitutions || [])) {
    if (!sub || sub.type !== "moved" || !sub.targetDate) continue;
    if (!index.has(sub.targetDate)) index.set(sub.targetDate, []);
    index.get(sub.targetDate).push(sub);
  }
  return index;
}

export function indexRowsByDate(rows) {
  const index = new Map();
  for (const row of (rows || [])) {
    if (!row) continue;
    if (!index.has(row.date)) index.set(row.date, []);
    index.get(row.date).push(row);
  }
  return index;
}

// Assignment values and instance objects are kept verbatim. In particular,
// string legacy day values must NOT become numeric (the old filter used ===).
export function indexAssignedInstancesByDay(instances, assignment) {
  const index = new Map();
  for (const inst of (instances || [])) {
    if (!inst) continue;
    const a = assignment?.[inst.instId];
    if (!a) continue;
    if (!index.has(a.day)) index.set(a.day, []);
    index.get(a.day).push({ inst, a });
  }
  return index;
}

// A moved lesson's instId may be the working id or _publishedSourceInstId.
// Keep the first matching instance in original array order, like Array.find.
export function findMovedSourceInstanceFast(instances, sourceId) {
  if (!Array.isArray(instances)) return null;
  let index = movedSourceByArray.get(instances);
  if (!index) {
    index = new Map();
    for (const inst of instances) {
      if (!inst) continue;
      for (const id of [inst.instId, inst._publishedSourceInstId]) {
        const key = String(id || "");
        if (!index.has(key)) index.set(key, inst);
      }
    }
    movedSourceByArray.set(instances, index);
  }
  return index.get(String(sourceId || "")) || null;
}

export function isShortenedDate(config, dateStr) {
  return Boolean(dateStr && Array.isArray(config?.shortenedDates) && config.shortenedDates.includes(dateStr));
}

export function compactWeekNumbers(nums = []) {
  const xs = [...new Set((nums || []).map(Number).filter((n) => Number.isFinite(n) && n > 0))].sort((a, b) => a - b);
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

export function weekNumbersForInstance(config, inst) {
  const total = Number(config?.semesterWeeks || config?.totalWeeks || 24);
  const pattern = inst?.weekPattern || "all";
  if (pattern === "custom") {
    return [...new Set((inst.customWeeks || []).map(Number).filter((n) => Number.isFinite(n) && n >= 1 && n <= total))].sort((a, b) => a - b);
  }
  if (pattern === "perWeek") {
    const wp = inst?.weeklyPairs || {};
    return Object.keys(wp).map(Number).filter((n) => n >= 1 && n <= total && Number(wp[n] || wp[String(n)] || 0) > 0).sort((a, b) => a - b);
  }
  if (pattern === "all") return Array.from({ length: total }, (_, i) => i + 1);
  return Array.from({ length: total }, (_, i) => i + 1).filter((n) => (n % 2 === 1 ? "odd" : "even") === pattern);
}

export function weekNumbersLabel(config, inst) {
  const nums = weekNumbersForInstance(config, inst);
  const compact = compactWeekNumbers(nums);
  return compact ? `недели: ${compact}` : "";
}

export function isNoClassDateFast(data, dateStr) {
  if (!dateStr) return false;
  if (isHolidayFast(data?.config, dateStr)) return true;
  const exc = calendarExceptionForFast(data, dateStr);
  return Boolean(exc?.noClasses);
}

export function groupCourseNumberFast(group) {
  const sem = Number(group?.curriculumSemester || 0);
  if (sem > 0) return Math.ceil(sem / 2);
  const m = String(group?.name || "").match(/^(\d+)/);
  return m ? Number(m[1]) : 1;
}

