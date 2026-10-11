// v1868: graph quantity is about real lessons, not raw technical instance rows.
// Normalize already-expanded rows and identify instance ids that participate in
// the same factual lesson occurrence for at least one student participant.
export function duplicateFactualOccurrenceIds(rows = []) {
  const buckets = new Map();
  for (const row of rows || []) {
    const id = String(row?.id || "");
    if (!id) continue;
    const participants = [...new Set((row?.participants || []).map(String).filter(Boolean))];
    if (!participants.length) continue;
    const teachers = [...new Set((row?.teachers || []).map(String).filter(Boolean))].sort().join(",");
    const type = String(row?.lessonTypeId || "");
    const half = String(row?.halfKey || "full");
    const format = String(row?.format || "inperson");
    for (const participant of participants) {
      const key = [Number(row?.week), Number(row?.day), Number(row?.period), participant,
        String(row?.subjectId || ""), teachers, type, format, half].join("|");
      if (!buckets.has(key)) buckets.set(key, new Set());
      buckets.get(key).add(id);
    }
  }
  const out = new Set();
  for (const ids of buckets.values()) {
    if (ids.size < 2) continue;
    for (const id of ids) out.add(id);
  }
  return out;
}
