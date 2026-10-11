// v1876: immutable date/practice lookup for the hot weekly grid.
// The original helpers scan all calendar exceptions and practices for every
// lesson occurrence. Build those indexes once per calendar/practice revision.
export function buildScheduleDateLookup(config = {}, calendarExceptions = [], practices = [], totalWeeks = 0, addDays, mondayOf) {
  const firstExceptionByDate = new Map();
  // calendarExceptionFor uses the FIRST matching exception from these two lists.
  for (const row of [...(calendarExceptions || []), ...(config.calendarExceptions || [])]) {
    if (row && !firstExceptionByDate.has(row.date)) firstExceptionByDate.set(row.date, row);
  }
  const holidays = new Set(config.holidays || []);
  const practiceRanges = new Map();
  for (const practice of (practices || [])) {
    if (!practice) continue;
    const gid = practice.groupId; // preserve strict equality with old isGroupOnPractice
    if (!practiceRanges.has(gid)) practiceRanges.set(gid, []);
    practiceRanges.get(gid).push(practice);
  }
  const monday = config.semesterStart ? mondayOf(config.semesterStart) : null;
  const dateByWeekDay = new Map();
  if (monday) {
    for (let week = 1; week <= totalWeeks; week++) {
      for (let day = 0; day < 7; day++) {
        dateByWeekDay.set(`${week}:${day}`, addDays(monday, (week - 1) * 7 + day));
      }
    }
  }
  return {
    date: (week, day) => dateByWeekDay.get(`${week}:${day}`) || (monday ? addDays(addDays(monday, (week - 1) * 7), day) : null),
    isNoClass: (date) => holidays.has(date) || !!firstExceptionByDate.get(date)?.noClasses,
    isGroupOnPractice: (groupId, date) => (practiceRanges.get(groupId) || []).some((p) => p.dateStart <= date && date <= p.dateEnd),
  };
}
