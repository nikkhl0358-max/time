// v1872: pure helpers for graph-quantity diagnostics.
// Graph capacity is consumed by visible placed lesson facts, not by technical
// instance rows. Two instance rows in the same week/slot/half for the same
// semantic bucket count as one actual lesson.
export function buildUniquePlacedFactUnits(occurrences = []) {
  const remainingUnits = new Map();
  const actualUnits = new Map();
  const seenFactsByBucket = new Map();
  for (const occ of occurrences) {
    if (!occ || occ.allZero || !occ.visibleFact) continue;
    for (const bucket of (occ.buckets || [])) {
      if (!seenFactsByBucket.has(bucket)) seenFactsByBucket.set(bucket, new Set());
      const seen = seenFactsByBucket.get(bucket);
      if (seen.has(occ.visibleFact)) continue;
      seen.add(occ.visibleFact);
      const unit = Number(occ.unit || 0);
      remainingUnits.set(bucket, Number(remainingUnits.get(bucket) || 0) + unit);
      actualUnits.set(bucket, Number(actualUnits.get(bucket) || 0) + unit);
    }
  }
  return { remainingUnits, actualUnits };
}

export function markSurplusPlacedOccurrences(occurrences = [], remainingUnits, expectedUnitsByBucket) {
  const surplus = new Set();
  const removedFactsByBucket = new Map();
  const expectedFor = (bucket) => Number(expectedUnitsByBucket?.get?.(bucket) || 0);
  for (const occ of occurrences) {
    if (!occ?.buckets?.length || occ.allZero || !occ.visibleFact) continue;
    const removable = occ.buckets.every((bucket) => {
      if (!removedFactsByBucket.has(bucket)) removedFactsByBucket.set(bucket, new Set());
      if (removedFactsByBucket.get(bucket).has(occ.visibleFact)) return false;
      const current = Number(remainingUnits?.get?.(bucket) || 0);
      return current - Number(occ.unit || 0) >= expectedFor(bucket) - 1e-9;
    });
    if (!removable) continue;
    surplus.add(occ.occurrenceKey);
    for (const bucket of occ.buckets) {
      removedFactsByBucket.get(bucket).add(occ.visibleFact);
      remainingUnits.set(bucket, Math.max(0, Number(remainingUnits.get(bucket) || 0) - Number(occ.unit || 0)));
    }
  }
  return surplus;
}
