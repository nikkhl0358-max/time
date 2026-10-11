// Central policy for deliberate manual timetable exceptions.
// Auto-scheduling MUST NOT use these allowances.
export const MANUAL_POLICY = Object.freeze({
  gym: Object.freeze({
    enabled: true,
    maxConcurrentGroups: 2,
    roomNamePattern: /спорт|физкульт|\bgym\b/i,
  }),
  sameTeacherMultiRoom: Object.freeze({ enabled: true, maxConcurrentGroups: 2 }),
  sameSubjectTeacherSameRoom: Object.freeze({ enabled: true, maxConcurrentGroups: 2 }),
  potentialStream: Object.freeze({ enabled: true }),
  siblingSubgroupsSameRoom: Object.freeze({ enabled: true }),
  occupiedRoomOverride: Object.freeze({ enabled: true }),
  mixedDayFormat: Object.freeze({ enabled: true }),
  forceOverlap: Object.freeze({ enabled: true }),
});

export function isGymRoomByPolicy(room, roomTypes = []) {
  if (!MANUAL_POLICY.gym.enabled || !room) return false;
  const type = (roomTypes || []).find((x) => String(x?.id) === String(room?.typeId));
  const haystack = `${type?.name || ""} ${room?.name || ""}`.trim();
  return MANUAL_POLICY.gym.roomNamePattern.test(haystack);
}

export function manualPolicyFlags(check = {}) {
  return {
    ...(check.manualPotentialStream ? { manualPotentialStream: true } : {}),
    ...(check.manualTeacherMultiRoom ? { manualTeacherMultiRoom: true } : {}),
    ...(check.manualSameSubjectTeacherSameRoom ? { manualSameSubjectTeacherSameRoom: true } : {}),
    ...(check.manualMultiGroupRoom ? { manualMultiGroupRoom: true } : {}),
    ...(check.manualOccupiedRoomOverride ? { manualOccupiedRoomOverride: true } : {}),
    ...(check.manualMixedDayFormat ? { manualMixedDayFormat: true } : {}),
    ...(check.manualSharedRoom ? { manualSharedRoom: true } : {}),
    ...(check.manualForceOverlap ? { manualForceOverlap: true } : {}),
  };
}
