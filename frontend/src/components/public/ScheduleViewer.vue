<template>
  <div class="schedule-viewer">
    <div v-if="loading" class="schedule-loading-state">
      <div class="spinner"></div>
      <p>Загрузка расписания...</p>
    </div>

    <div v-else-if="!safeInstances.length" class="schedule-empty-state">
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
          </div>

          <div class="day-lessons">
            <template v-if="getLessonsForDay(dayObj.index).length">
              <LessonCard
                v-for="inst in getLessonsForDay(dayObj.index)"
                :key="inst.instId || inst.id"
                :instance="inst"
                :period="Number(inst.period) || 1"
                :subjectName="getSubjectName(inst.subjectId)"
                :teacherName="getTeacherName(inst.teacherId)"
                :roomName="getRoomName(inst.roomId || inst.dedicatedRoomId || inst.preferredRoomIds?.[0])"
                :substitution="findSub(inst, dayObj.index, inst.period || 1)"
                :getTeacherName="getTeacherName"
                :getRoomName="getRoomName"
                :getRoomLocation="getRoomLocation"
                :getSubjectName="getSubjectName"
                :getLessonTypeName="getLessonTypeName"
                :getPeriodTime="getPeriodTime"
                @select-teacher="handleSelectTeacher"
              />
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

const DAY_NAMES = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
const SHORT_DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

const props = defineProps({
  instances: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  getSubjectName: { type: Function, required: true },
  getTeacherName: { type: Function, required: true },
  getRoomName: { type: Function, required: true },
  getRoomLocation: { type: Function, default: () => '' },
  getLessonTypeName: { type: Function, default: () => '' },
  getPeriodTime: { type: Function, default: () => '' },
  findSub: { type: Function, required: true }
});

const emit = defineEmits(['select-teacher']);

function handleSelectTeacher(teacherId) {
  emit('select-teacher', teacherId);
}

const activeDayIndex = ref(-1);

const safeInstances = computed(() => {
  return Array.isArray(props.instances) ? props.instances : [];
});

const displayedDays = computed(() => {
  if (activeDayIndex.value >= 0 && activeDayIndex.value < DAY_NAMES.length) {
    return [{ name: DAY_NAMES[activeDayIndex.value], index: activeDayIndex.value }];
  }
  return DAY_NAMES.map((name, index) => ({ name, index }));
});

function parseClockMinutes(timeStr) {
  if (!timeStr) return null;
  const match = String(timeStr).match(/(\d{1,2}):(\d{2})/);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function getLessonOrder(inst, getPeriodTimeFn) {
  if (inst.eventStartTime) {
    const mins = parseClockMinutes(inst.eventStartTime);
    if (mins !== null) return mins;
  }
  const period = Number(inst.period) || 1;
  const periodTime = getPeriodTimeFn ? getPeriodTimeFn(period) : '';
  const timeMins = parseClockMinutes(periodTime);
  return timeMins !== null ? timeMins : period * 100;
}

const lessonsByDay = computed(() => {
  const map = { 0: [], 1: [], 2: [], 3: [], 4: [], 5: [] };
  const getOrder = (inst) => getLessonOrder(inst, props.getPeriodTime);
  safeInstances.value.forEach(inst => {
    const d = Number(inst.day !== undefined ? inst.day : inst.blockIndex);
    if (map[d]) {
      map[d].push(inst);
    }
  });
  Object.keys(map).forEach(key => {
    map[key].sort((a, b) => getOrder(a) - getOrder(b));
  });
  return map;
});

function getLessonsForDay(dayIdx) {
  return lessonsByDay.value[dayIdx] || [];
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
}

.day-pill-btn:hover {
  background: var(--blue-50);
  color: var(--blue-700);
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
    border-radius: 12px;
    gap: 4px;
    padding: 4px;
    width: 100%;
    max-width: 100%;
    box-sizing: border-box;
  }
  .day-pill-btn {
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
    gap: 3px;
    padding: 3px;
  }
  .day-pill-btn {
    padding: 5px 8px;
    font-size: 11px;
    min-height: 32px;
  }
}
</style>
