<template>
  <div class="schedule-viewer">
    <div v-if="loading" class="schedule-loading-state">
      <div class="spinner"></div>
      <p>Загрузка расписания...</p>
    </div>

    <div v-else-if="!hasAnyWeeklyContent" class="schedule-empty-state">
      <div class="empty-icon">
        <svg class="empty-icon-svg" viewBox="0 0 24 24" width="38" height="38" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
          <line x1="16" y1="2" x2="16" y2="6"></line>
          <line x1="8" y1="2" x2="8" y2="6"></line>
          <line x1="3" y1="10" x2="21" y2="10"></line>
        </svg>
      </div>
      <h3>Нет занятий на выбранную неделю</h3>
      <p>Для выбранного расписания на этой неделе пары не запланированы или идут каникулы.</p>
    </div>

    <div v-else class="schedule-content-wrap">
      <!-- Баннер официальных объявлений колледжа -->
      <PublicNotices :announcements="announcements" :publicIntroText="publicIntroText" />

      <!-- Панель быстрого фильтра по дням недели (по центру) -->
      <nav class="day-filter-bar" aria-label="Фильтр дней недели">
        <button
          type="button"
          :class="['day-pill-btn', { active: activeDayIndex === -1 }]"
          @click="activeDayIndex = -1"
        >
          Вся неделя
        </button>
        <button
          v-for="(dayName, idx) in DAY_NAMES"
          :key="idx"
          type="button"
          :class="['day-pill-btn', { active: activeDayIndex === idx }]"
          @click="activeDayIndex = idx"
        >
          <span>{{ SHORT_DAYS[idx] }}</span>
          <span 
            v-if="getLessonsForDay(idx).length" 
            class="day-pill-counter"
          >
            {{ getLessonsForDay(idx).length }}
          </span>
        </button>
      </nav>

      <!-- Симметричная центрированная сетка дней расписания -->
      <div 
        class="days-grid" 
        :class="{ 'single-column-center': activeDayIndex !== -1 }"
      >
        <div 
          v-for="dayObj in displayedDays" 
          :key="dayObj.index"
          class="day-column"
        >
          <div class="day-header">
            <span class="day-title">{{ dayObj.name }}</span>
            <span v-if="dayObj.dateFormatted" class="day-date">{{ dayObj.dateFormatted }}</span>
            <span v-if="isShortenedDay(dayObj.dateISO)" class="public-shortened-day"> · ⏱ сокращённый день</span>
          </div>

          <!-- Свободные пары для кабинета (по скриншоту 1) -->
          <div 
            v-if="role === 'room' && getFreePeriodsForDay(dayObj.index).length" 
            class="room-free-bar"
            title="Пары, на которых кабинет свободен по опубликованному расписанию"
          >
            <span class="room-free-check">✓</span>
            <span class="room-free-text">Свободно: {{ formatFreePeriods(getFreePeriodsForDay(dayObj.index)) }}</span>
          </div>
          <div 
            v-else-if="role !== 'room' && getGapsForDay(dayObj.index).length" 
            class="day-gaps-bar"
            title="Свободное время между занятиями в этот день"
          >
            <span class="day-gaps-icon">🕳</span>
            <span class="day-gaps-text">{{ getGapsForDay(dayObj.index).length === 1 ? 'Окно' : 'Окна' }}: {{ formatFreePeriods(getGapsForDay(dayObj.index)) }}</span>
          </div>

          <div class="day-lessons">
            <div v-if="isHolidayDay(dayObj.dateISO)" class="day-holiday-banner">
              🎉 Праздничный день — занятий нет
            </div>
            <div v-else-if="getGroupPractice(dayObj.dateISO)" class="practice-card">
              <div class="practice-title">🧰 {{ getGroupPractice(dayObj.dateISO).name || 'Производственная/учебная практика' }}</div>
              <div v-if="getGroupPractice(dayObj.dateISO).teacherName" class="practice-row">
                <span class="practice-icon">👤</span> Руководитель: {{ getGroupPractice(dayObj.dateISO).teacherName }}
              </div>
              <div v-if="getGroupPractice(dayObj.dateISO).note" class="practice-row practice-note">
                {{ getGroupPractice(dayObj.dateISO).note }}
              </div>
            </div>
            <template v-else-if="getLessonsForDay(dayObj.index).length || getRetakesForDay(dayObj.dateISO).length">
              <LessonCard
                v-for="inst in getLessonsForDay(dayObj.index)"
                :key="inst.instId || inst.id"
                :role="role"
                :instance="inst"
                :period="Number(inst.period) || 1"
                :dateISO="dayObj.dateISO"
                :semesterConfig="semesterConfig"
                :teachers="teachers"
                :rooms="rooms"
                :subjectName="getSubjectName(inst.subjectId)"
                :teacherName="getTeacherName(inst.teacherId)"
                :roomName="getRoomName(inst.roomId || inst.dedicatedRoomId || inst.preferredRoomIds?.[0])"
                :substitution="findSub(inst, dayObj.dateISO, dayObj.index, inst.period || 1)"
                :getGroupName="getGroupName"
                :getTeacherName="getTeacherName"
                :getRoomName="getRoomName"
                :getRoomDetails="getRoomDetails"
                :getRoomLocation="getRoomLocation"
                :getSubjectName="getSubjectName"
                :getLessonTypeName="getLessonTypeName"
                :getPeriodTime="getPeriodTime"
                @select-teacher="handleSelectTeacher"
                @select-group="handleSelectGroup"
              />

              <div 
                v-for="retake in getRetakesForDay(dayObj.dateISO)" 
                :key="retake.id" 
                class="public-event-retake"
              >
                <div class="retake-time">
                  🕐 {{ retake.period != null ? getPeriodTime(retake.period + 1, { groupId: retake.groupId, dayIndex: dayObj.index, dateStr: dayObj.dateISO, roomId: retake.roomId }) : 'время уточняется' }}
                </div>
                <div class="retake-title">
                  🔁 {{ retake.kind === 'transfer' ? 'Перенос' : 'Пересдача' }}: {{ getSubjectName(retake.subjectId) || retake.subject || 'Консультация' }}
                </div>
                <div v-if="retake.roomId" class="retake-row">
                  📍 ауд. {{ getRoomName(retake.roomId) }}
                </div>
                <div class="retake-row">
                  <span v-if="role === 'group'">👤 {{ getTeacherName(retake.teacherId) }}</span>
                  <span v-else>👥 {{ getGroupName(retake.groupId) }}</span>
                </div>
                <div v-if="retake.note" class="retake-note">
                  {{ retake.note }}
                </div>
              </div>
            </template>
            <div v-else class="day-no-lessons">
              <span class="no-lessons-label">Нет запланированных пар</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import LessonCard from './LessonCard.vue';
import PublicNotices from './PublicNotices.vue';
import { isShortenedDate, isNoClassDateFast } from '../../fastScheduleLookups.js';
import { collectMovedLessonsForDate, calculateFreePeriods, calculateDayGaps, collapsePublicSiblingSubgroups, dedupePublicDuplicateLessons } from '../../utils/scheduleSubstitutions.js';

const DAY_NAMES = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
const SHORT_DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

const props = defineProps({
  role: { type: String, default: 'group' },
  selectedId: { type: String, default: '' },
  instances: { type: Array, default: () => [] },
  rawInstances: { type: Array, default: () => [] },
  loads: { type: Array, default: () => [] },
  substitutions: { type: Array, default: () => [] },
  subjects: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  selectedWeek: { type: Number, default: 10 },
  semesterStart: { type: String, default: '' },
  semesterConfig: { type: Object, default: () => ({}) },
  announcements: { type: Array, default: () => [] },
  publicIntroText: { type: String, default: '' },
  practices: { type: Array, default: () => [] },
  retakes: { type: Array, default: () => [] },
  teachers: { type: Array, default: () => [] },
  rooms: { type: Array, default: () => [] },
  groups: { type: Array, default: () => [] },
  getGroupName: { type: Function, default: () => '' },
  getSubjectName: { type: Function, required: true },
  getTeacherName: { type: Function, required: true },
  getRoomName: { type: Function, required: true },
  getRoomDetails: { type: Function, default: null },
  getRoomLocation: { type: Function, default: () => '' },
  getLessonTypeName: { type: Function, default: () => '' },
  getPeriodTime: { type: Function, default: () => '' },
  findSub: { type: Function, required: true }
});

const emit = defineEmits(['select-teacher', 'select-group']);

const MONTH_NAMES = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
];

function computeScheduleDate(startStr, weekNum, dayIdx) {
  if (!startStr || !weekNum || dayIdx === undefined || dayIdx === null) return null;
  const p = String(startStr).split('-').map(Number);
  if (p.length < 3 || p.some(isNaN)) return null;
  const [y, m, d] = p;
  if (m < 1 || m > 12 || d < 1 || d > 31) return null;
  const dt = new Date(y, m - 1, d, 12, 0, 0);
  if (isNaN(dt.getTime()) || dt.getMonth() !== m - 1 || dt.getDate() !== d) return null;
  const numWeek = Number(weekNum);
  const numDay = Number(dayIdx);
  if (isNaN(numWeek) || isNaN(numDay)) return null;
  const mondayOffset = (dt.getDay() + 6) % 7;
  dt.setDate(dt.getDate() - mondayOffset + (numWeek - 1) * 7 + numDay);
  return isNaN(dt.getTime()) ? null : dt;
}

function formatCalendarDate(startStr, weekNum, dayIdx) {
  const dt = computeScheduleDate(startStr, weekNum, dayIdx);
  return dt ? `${dt.getDate()} ${MONTH_NAMES[dt.getMonth()]}` : '';
}

function formatISODate(startStr, weekNum, dayIdx) {
  const dt = computeScheduleDate(startStr, weekNum, dayIdx);
  if (!dt || isNaN(dt.getTime())) return '';
  const y = dt.getFullYear();
  const m = String(dt.getMonth() + 1).padStart(2, '0');
  const d = String(dt.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function handleSelectTeacher(teacherId) {
  emit('select-teacher', teacherId);
}

function handleSelectGroup(groupId) {
  emit('select-group', groupId);
}

const activeDayIndex = ref(-1);

const safeInstances = computed(() => {
  return Array.isArray(props.instances) ? props.instances : [];
});

const displayedDays = computed(() => {
  if (activeDayIndex.value >= 0 && activeDayIndex.value < DAY_NAMES.length) {
    const idx = activeDayIndex.value;
    const dateFormatted = formatCalendarDate(props.semesterStart, props.selectedWeek, idx);
    const dateISO = formatISODate(props.semesterStart, props.selectedWeek, idx);
    return [{ name: DAY_NAMES[idx], index: idx, dateFormatted, dateISO }];
  }
  return DAY_NAMES.map((name, index) => ({
    name,
    index,
    dateFormatted: formatCalendarDate(props.semesterStart, props.selectedWeek, index),
    dateISO: formatISODate(props.semesterStart, props.selectedWeek, index)
  }));
});

function parseClockMinutes(timeStr) {
  if (!timeStr) return null;
  const match = String(timeStr).match(/(\d{1,2}):(\d{2})/);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function getLessonOrder(inst, getPeriodTimeFn, dayIdx, dateStr = '') {
  if (inst.eventStartTime) {
    const mins = parseClockMinutes(inst.eventStartTime);
    if (mins !== null) return mins;
  }
  const period = Number(inst.period) || 1;
  const time = getPeriodTimeFn ? getPeriodTimeFn(period, { groupId: inst.groupId, dayIndex: dayIdx, roomId: inst.roomId, dateStr }) : '';
  const timeMins = parseClockMinutes(time);
  return timeMins !== null ? timeMins : period * 100;
}

function sortDayLessons(lessons, getPeriodTimeFn, dayIdx, dateStr = '') {
  if (lessons.length <= 1) return lessons;
  const decorated = lessons.map(inst => ({
    inst,
    order: getLessonOrder(inst, getPeriodTimeFn, dayIdx, dateStr)
  }));
  decorated.sort((a, b) => a.order - b.order);
  return decorated.map(d => d.inst);
}

function shouldIncludeInstanceInView(inst, role) {
  if (inst._publicMoveSupportOnly) return false;
  if (role === 'room' && ['remote', 'distance', 'dist'].includes(inst.format)) {
    return false;
  }
  return true;
}

function populateBaseInstances(safeInsts, role) {
  const map = { 0: [], 1: [], 2: [], 3: [], 4: [], 5: [] };
  safeInsts.forEach(inst => {
    if (!shouldIncludeInstanceInView(inst, role)) return;
    const d = Number(inst.day !== undefined ? inst.day : inst.blockIndex);
    if (map[d]) map[d].push(inst);
  });
  return map;
}

function injectMovedLessons(map, p) {
  const source = (Array.isArray(p.rawInstances) && p.rawInstances.length) ? p.rawInstances : p.instances;
  for (let dayIdx = 0; dayIdx < 6; dayIdx += 1) {
    const dateISO = formatISODate(p.semesterStart, p.selectedWeek, dayIdx);
    const moved = collectMovedLessonsForDate(p.substitutions, source, p.role, p.selectedId, dateISO, dayIdx, p.subjects, p.loads);
    if (moved.length) map[dayIdx].push(...moved);
  }
}

const lessonsByDay = computed(() => {
  const map = populateBaseInstances(safeInstances.value, props.role);
  injectMovedLessons(map, props);
  Object.keys(map).forEach(key => {
    const dayIdx = Number(key);
    const dateISO = formatISODate(props.semesterStart, props.selectedWeek, dayIdx);
    const sorted = sortDayLessons(map[key], props.getPeriodTime, dayIdx, dateISO);
    const deduped = dedupePublicDuplicateLessons(sorted, props.role);
    map[key] = collapsePublicSiblingSubgroups(deduped);
  });
  return map;
});

const hasAnyWeeklyContent = computed(() => {
  const regularCount = safeInstances.value.filter(i => !i._publicMoveSupportOnly).length;
  if (regularCount > 0) return true;
  for (let dayIdx = 0; dayIdx < 6; dayIdx += 1) {
    if ((lessonsByDay.value[dayIdx] || []).length > 0) return true;
    const dateISO = formatISODate(props.semesterStart, props.selectedWeek, dayIdx);
    if (dateISO && getRetakesForDay(dateISO).length > 0) return true;
  }
  return false;
});

function getLessonsForDay(dayIdx) {
  return lessonsByDay.value[dayIdx] || [];
}

function isLessonOccupyingRoom(inst, sub, role) {
  if (role !== 'room') return true;
  if (['remote', 'distance', 'dist'].includes(inst.format)) return false;
  if (['remote', 'distance', 'dist'].includes(sub?.newFormat)) return false;
  return true;
}

function getActiveOccupiedPeriods(dayIdx) {
  const dayLessons = getLessonsForDay(dayIdx);
  const dateISO = formatISODate(props.semesterStart, props.selectedWeek, dayIdx);
  return dayLessons
    .filter(inst => {
      const sub = props.findSub ? props.findSub(inst, dateISO, dayIdx, inst.period || 1) : null;
      const isCancelled = sub && ['cancel', 'cancel_assignment', 'pending_move', 'moved', 'excursion'].includes(sub.type) && !sub.isMovedTarget;
      if (isCancelled) return false;
      return isLessonOccupyingRoom(inst, sub, props.role);
    })
    .map(inst => Number(inst.period) || 1);
}

function getFreePeriodsForDay(dayIdx) {
  if (props.role !== 'room') return [];
  const occupied = getActiveOccupiedPeriods(dayIdx);
  return calculateFreePeriods(occupied, 7);
}

function getGapsForDay(dayIdx) {
  if (props.role === 'room') return [];
  const occupied = getActiveOccupiedPeriods(dayIdx);
  return calculateDayGaps(occupied);
}

function formatFreePeriods(periods) {
  return (periods || []).map(p => `${p} пара`).join(', ');
}

function isShortenedDay(dateISO) {
  return isShortenedDate(props.semesterConfig, dateISO);
}

function isHolidayDay(dateISO) {
  return isNoClassDateFast({ config: props.semesterConfig, calendarExceptions: props.semesterConfig?.calendarExceptions }, dateISO);
}

function getGroupPractice(dateISO) {
  if (props.role !== 'group' || !dateISO) return null;
  const p = (props.practices || []).find(x => String(x.groupId) === String(props.selectedId) && x.dateStart <= dateISO && dateISO <= x.dateEnd);
  if (!p) return null;
  const t = props.getTeacherName ? props.getTeacherName(p.teacherId) : '';
  return { ...p, teacherName: t };
}

function getRetakesForDay(dateISO) {
  if (!dateISO || !Array.isArray(props.retakes)) return [];
  return props.retakes.filter(r => {
    if (String(r.date) !== String(dateISO)) return false;
    if (props.role === 'group') return String(r.groupId) === String(props.selectedId);
    if (props.role === 'teacher') return String(r.teacherId) === String(props.selectedId);
    return String(r.roomId || '') === String(props.selectedId);
  }).sort((a, b) => (Number(a.period) || 0) - (Number(b.period) || 0));
}
</script>

<style scoped>
.schedule-viewer {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.schedule-content-wrap {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
}

/* Панель выбора дней недели по центру */
.day-filter-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 8px;
  background: #ffffff;
  padding: 6px 10px;
  border-radius: 16px;
  border: 1px solid var(--border-light);
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
  margin-bottom: 22px;
}

.day-pill-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 11px;
  border: none;
  background: transparent;
  font-size: 13px;
  font-weight: 700;
  color: var(--ink-secondary);
  cursor: pointer;
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
  min-height: 38px;
  user-select: none;
}

.day-pill-btn:hover {
  background: var(--blue-50);
  color: var(--blue-700);
}

.day-pill-btn:active {
  transform: scale(0.96);
}

.day-pill-btn.active {
  background: var(--blue-600);
  color: #ffffff;
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.3);
}

.day-pill-counter {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.25);
  color: inherit;
  font-weight: 800;
}

.day-pill-btn:not(.active) .day-pill-counter {
  background: var(--blue-100);
  color: var(--blue-700);
}

/* Симметричная центрированная сетка */
.days-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(320px, 420px));
  justify-content: center;
  gap: 20px;
  width: 100%;
  max-width: 1340px;
  margin: 0 auto;
}

.days-grid.single-column-center {
  grid-template-columns: minmax(0, 680px);
}

.day-column {
  background: #ffffff;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 16px 18px;
  box-shadow: var(--shadow-subtle);
  display: flex;
  flex-direction: column;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.day-column:hover {
  border-color: var(--blue-200);
  box-shadow: 0 8px 24px rgba(2, 132, 199, 0.08);
}

.day-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  margin-bottom: 14px;
  border-bottom: 2px solid var(--blue-50);
}

.day-title {
  font-size: 16px;
  font-weight: 800;
  color: var(--blue-700);
  letter-spacing: -0.01em;
}

.day-date {
  font-size: 13px;
  font-weight: 700;
  color: #f97316;
  background: rgba(249, 115, 22, 0.08);
  padding: 3px 8px;
  border-radius: 8px;
  border: 1px solid rgba(249, 115, 22, 0.2);
}

.room-free-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
  background: #fefce8;
  border: 1px solid #fef08a;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
  color: #854d0e;
}

.room-free-check {
  color: #16a34a;
  font-weight: 800;
  font-size: 13px;
}

.day-gaps-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}

.day-count {
  font-size: 11px;
  font-weight: 700;
  color: var(--blue-700);
  background: var(--blue-50);
  border: 1px solid var(--blue-100);
  padding: 2px 10px;
  border-radius: 12px;
}

.day-lessons {
  display: flex;
  flex-direction: column;
  flex: 1;
}

.day-no-lessons {
  padding: 36px 14px;
  text-align: center;
  color: var(--ink-muted);
  font-size: 13px;
  background: var(--bg-app);
  border-radius: 12px;
  border: 1px dashed var(--border-light);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: 4px 0 10px;
}

.no-lessons-icon {
  font-size: 24px;
}

.no-lessons-label {
  font-weight: 600;
}

.schedule-loading-state, .schedule-empty-state {
  text-align: center;
  padding: 60px 20px;
  background: #ffffff;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-light);
  color: var(--ink-muted);
  width: 100%;
  max-width: 600px;
  margin: 0 auto;
}

.spinner {
  width: 36px;
  height: 36px;
  border: 3px solid var(--blue-100);
  border-top-color: var(--blue-600);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 16px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.empty-icon {
  font-size: 40px;
  margin-bottom: 12px;
}

/* Адаптивность сетки под экраны */
@media (max-width: 1140px) {
  .days-grid {
    grid-template-columns: repeat(2, minmax(300px, 440px));
    gap: 16px;
  }
}

@media (max-width: 768px) {
  .days-grid {
    grid-template-columns: minmax(0, 1fr);
    width: 100%;
    max-width: 100%;
    gap: 14px;
  }
  .day-column {
    padding: 14px 14px;
    width: 100%;
    max-width: 100%;
    min-width: 0;
    box-sizing: border-box;
  }
  .day-filter-bar {
    justify-content: flex-start;
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
    border-radius: 12px;
    gap: 6px;
    padding: 6px 8px;
    width: 100%;
    max-width: 100%;
    box-sizing: border-box;
  }
  .day-filter-bar::-webkit-scrollbar {
    display: none;
  }
  .day-pill-btn {
    flex-shrink: 0;
    white-space: nowrap;
    padding: 6px 10px;
    font-size: 12px;
  }
}

@media (max-width: 480px) {
  .days-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
  .day-column {
    padding: 12px 10px;
    border-radius: 12px;
  }
  .day-filter-bar {
    gap: 4px;
    padding: 4px 6px;
  }
  .day-pill-btn {
    padding: 5px 8px;
    font-size: 11px;
    min-height: 32px;
  }
}
.public-shortened-day {
  font-size: 11px;
  font-weight: 700;
  color: #d97706;
  background: #fef3c7;
  padding: 2px 6px;
  border-radius: 6px;
  margin-left: 6px;
}

.day-holiday-banner {
  background: #eff6ff;
  border: 1px dashed #60a5fa;
  color: #1d4ed8;
  padding: 16px 14px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 700;
  text-align: center;
  margin: 6px 0 12px;
}

.practice-card {
  background: #f8fafc;
  border: 1px solid #cbd5e1;
  border-left: 4px solid #0284c7;
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 4px 0 10px;
}

.practice-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink-primary);
}

.practice-row {
  font-size: 13px;
  color: var(--ink-secondary);
}

.practice-note {
  font-style: italic;
  font-size: 12px;
}

.public-event-retake {
  background: #fbf3e3;
  border: 1px solid #ead9a0;
  border-left: 4px solid #f59e0b;
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-bottom: 8px;
  font-size: 13px;
}

.retake-time {
  font-size: 11px;
  font-weight: 700;
  color: #b45309;
}

.retake-title {
  font-weight: 700;
  color: #92400e;
}

.retake-row {
  color: var(--ink-secondary);
}

.retake-note {
  font-size: 11px;
  color: var(--ink-soft);
  font-style: italic;
}
</style>
