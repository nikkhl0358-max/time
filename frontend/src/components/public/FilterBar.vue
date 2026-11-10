<template>
  <div class="filter-bar">
    <!-- Блок текущей выбранной сущности и кнопка смены -->
    <div class="current-selection-box">
      <div class="entity-info">
        <span class="entity-type-label">{{ entityRoleLabel }}</span>
        <div class="entity-name-row">
          <h2 class="entity-name">{{ selectedName || 'Не выбрано' }}</h2>
          <span v-if="entitySubtitle" class="entity-subtitle-badge" :title="entitySubtitle">{{ entitySubtitle }}</span>
        </div>
      </div>

      <button 
        type="button" 
        class="change-entity-btn"
        @click="handleChangeClick"
        :title="`Сменить ${entityRoleGenitive}`"
      >
        <svg class="change-icon-svg" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="23 4 23 10 17 10"></polyline>
          <polyline points="1 20 1 14 7 14"></polyline>
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
        </svg>
        <span>Сменить {{ entityRoleGenitive }}</span>
      </button>
    </div>

    <!-- Управление неделями и экспорт -->
    <div class="actions-group">
      <div class="week-navigation">
        <button 
          class="week-nav-btn" 
          :disabled="!canPrevWeek"
          @click="stepWeek(-1)"
          title="Предыдущая неделя"
          aria-label="Предыдущая неделя"
        >
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="15 18 9 12 15 6"></polyline>
          </svg>
        </button>

        <div class="week-pill">
          <span class="week-label">Учебная неделя</span>
          <span class="week-number">{{ selectedWeek }}</span>
        </div>

        <button 
          class="week-nav-btn" 
          :disabled="!canNextWeek"
          @click="stepWeek(1)"
          title="Следующая неделя"
          aria-label="Следующая неделя"
        >
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="9 18 15 12 9 6"></polyline>
          </svg>
        </button>

        <button 
          v-if="currentCalWeek && selectedWeek !== currentCalWeek"
          type="button" 
          class="current-week-quick-btn" 
          @click="emit('update:selectedWeek', currentCalWeek)" 
          title="Перейти к текущей календарной неделе"
        >
          Сегодня ({{ currentCalWeek }})
        </button>
      </div>

      <div class="action-buttons-row">
        <button 
          class="export-btn" 
          :disabled="!canExport"
          @click="$emit('export-csv')" 
          title="Экспорт в CSV / Excel"
        >
          <svg class="btn-svg" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
          <span>Скачать CSV</span>
        </button>

        <button 
          class="print-btn" 
          :disabled="!canExport"
          @click="$emit('print')" 
          title="Распечатать расписание"
        >
          <svg class="btn-svg" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="6 9 6 2 18 2 18 9"></polyline>
            <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
            <rect x="6" y="14" width="12" height="8"></rect>
          </svg>
          <span>Печать</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { calculateCurrentSemesterWeek } from '../../composables/useRouteSync.js';

const props = defineProps({
  role: { type: String, default: 'group' },
  items: { type: Array, default: () => [] },
  selectedId: { type: [String, Number], default: '' },
  selectedName: { type: String, default: '' },
  entitySubtitle: { type: String, default: '' },
  selectedWeek: { type: Number, default: 10 },
  hasSchedule: { type: Boolean, default: true },
  publishedWeeks: { type: Array, default: () => [] },
  publicWeekSelectionEnabled: { type: Boolean, default: true },
  semesterStart: { type: String, default: '' }
});

const emit = defineEmits(['change-selection', 'update:selectedWeek', 'export-csv', 'print']);

const hasItems = computed(() => Boolean(props.selectedId));
const canExport = computed(() => Boolean(props.selectedId) && props.hasSchedule);
const currentCalWeek = computed(() => calculateCurrentSemesterWeek(props.semesterStart));

const availableWeeks = computed(() => {
  if (props.publicWeekSelectionEnabled && Array.isArray(props.publishedWeeks) && props.publishedWeeks.length) {
    const set = new Set(props.publishedWeeks.map(Number));
    if (props.selectedWeek) set.add(Number(props.selectedWeek));
    if (currentCalWeek.value) set.add(Number(currentCalWeek.value));
    return [...set].filter(n => Number.isFinite(n) && n >= 1 && n <= 24).sort((a, b) => a - b);
  }
  return Array.from({ length: 24 }, (_, i) => i + 1);
});

const canPrevWeek = computed(() => availableWeeks.value.some(w => w < props.selectedWeek));
const canNextWeek = computed(() => availableWeeks.value.some(w => w > props.selectedWeek));

// 1. Отображение типа сущности
const entityRoleLabel = computed(() => {
  if (props.role === 'teacher') return 'Преподаватель';
  if (props.role === 'room') return 'Аудитория';
  return 'Учебная группа';
});

// 2. Родительный падеж для кнопки смены
const entityRoleGenitive = computed(() => {
  if (props.role === 'teacher') return 'преподавателя';
  if (props.role === 'room') return 'аудиторию';
  return 'группу';
});

// 3. Смена учебной недели
function stepWeek(direction) {
  const list = availableWeeks.value;
  if (!list.length) return;
  let target = null;
  if (direction < 0) {
    target = [...list].reverse().find(w => w < props.selectedWeek) ?? null;
  } else {
    target = list.find(w => w > props.selectedWeek) ?? null;
  }
  if (target !== null) emit('update:selectedWeek', target);
}

// 5. Клик по кнопке смены сущности
function handleChangeClick() {
  emit('change-selection');
}
</script>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  background: #ffffff;
  padding: 14px 24px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-light);
  box-shadow: var(--shadow-subtle);
  margin-bottom: 24px;
  flex-wrap: wrap;
  width: 100%;
  box-sizing: border-box;
}

.current-selection-box {
  display: flex;
  align-items: center;
  gap: 16px;
  flex: 1;
  min-width: 260px;
}

.entity-info {
  display: flex;
  flex-direction: column;
}

.entity-type-label {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  color: var(--blue-700);
  letter-spacing: 0.04em;
  margin-bottom: 2px;
}

.entity-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.entity-subtitle-badge {
  font-size: 12px;
  font-weight: 600;
  color: #0369a1;
  background: #e0f2fe;
  border: 1px solid #bae6fd;
  padding: 3px 8px;
  border-radius: 6px;
  white-space: nowrap;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.entity-icon {
  font-size: 18px;
}

.entity-name {
  font-size: 18px;
  font-weight: 800;
  color: var(--ink-primary);
  margin: 0;
  line-height: 1.2;
}

.change-entity-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 8px 14px;
  background: var(--blue-50);
  border: 1px solid var(--blue-200);
  border-radius: 10px;
  color: var(--blue-700);
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s ease;
  white-space: nowrap;
}

.change-entity-btn:hover {
  background: var(--blue-600);
  color: #ffffff;
  border-color: var(--blue-600);
  box-shadow: 0 2px 8px rgba(2, 132, 199, 0.25);
}

.change-entity-btn:active {
  transform: scale(0.97);
  background: var(--blue-700);
  border-color: var(--blue-700);
  color: #ffffff;
}

.change-icon-svg {
  flex-shrink: 0;
  display: inline-block;
  vertical-align: middle;
  transition: transform 0.25s ease;
}

.change-entity-btn:hover .change-icon-svg {
  transform: rotate(180deg);
}

.btn-icon {
  font-size: 12px;
}

.actions-group {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.week-navigation {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--blue-50);
  padding: 4px;
  border-radius: 12px;
  border: 1px solid var(--blue-100);
}

.week-nav-btn {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: #ffffff;
  color: var(--blue-700);
  font-size: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  transition: all 0.18s ease;
}

.week-nav-btn:hover:not(:disabled) {
  background: var(--blue-600);
  color: #ffffff;
  box-shadow: 0 2px 6px rgba(2, 132, 199, 0.3);
}

.week-nav-btn:active:not(:disabled) {
  transform: scale(0.92);
  background: var(--blue-700);
  color: #ffffff;
}

.week-nav-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
  background: #f1f5f9;
  color: var(--ink-muted);
  border-color: var(--border-light);
  box-shadow: none;
}

.week-pill {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 0 10px;
}

.current-week-quick-btn {
  padding: 5px 9px;
  background: var(--orange-500, #f97316);
  color: #ffffff;
  border: none;
  border-radius: 7px;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
  box-shadow: 0 2px 6px rgba(249, 115, 22, 0.25);
  margin-left: 2px;
}

.current-week-quick-btn:hover {
  background: var(--orange-600, #ea580c);
  transform: translateY(-1px);
  box-shadow: 0 4px 10px rgba(249, 115, 22, 0.35);
}

.current-week-quick-btn:active {
  transform: scale(0.96);
}

.week-label {
  font-size: 10px;
  color: var(--ink-muted);
  text-transform: uppercase;
  font-weight: 700;
  letter-spacing: 0.03em;
}

.week-number {
  font-size: 14px;
  font-weight: 800;
  color: var(--blue-800);
  line-height: 1;
}

.action-buttons-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.export-btn, .print-btn {
  padding: 8px 14px;
  border-radius: 10px;
  border: 1px solid var(--border-light);
  background: #ffffff;
  font-size: 12px;
  font-weight: 700;
  color: var(--ink-secondary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: all 0.2s ease;
}

.export-btn:hover:not(:disabled), .print-btn:hover:not(:disabled) {
  border-color: var(--blue-500);
  color: var(--blue-700);
  background: var(--blue-50);
}

.export-btn:active:not(:disabled), .print-btn:active:not(:disabled) {
  transform: scale(0.97);
  background: var(--blue-100);
}

.export-btn:disabled, .print-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  background: #f8fafc;
  border-color: #e2e8f0;
  color: var(--ink-muted);
  box-shadow: none;
}

.btn-svg {
  flex-shrink: 0;
  display: inline-block;
  vertical-align: middle;
}

@media (max-width: 900px) {
  .filter-bar {
    flex-direction: column;
    align-items: stretch;
    padding: 14px 16px;
    gap: 14px;
  }
  .current-selection-box {
    justify-content: space-between;
    width: 100%;
  }
  .actions-group {
    justify-content: space-between;
    width: 100%;
  }
}

@media (max-width: 600px) {
  .actions-group {
    flex-direction: column;
    gap: 10px;
  }
  .week-navigation {
    width: 100%;
    justify-content: space-between;
  }
  .action-buttons-row {
    width: 100%;
  }
  .export-btn, .print-btn {
    flex: 1;
    justify-content: center;
    padding: 10px;
    min-height: 44px;
  }
}

@media (max-width: 480px) {
  .filter-bar {
    padding: 10px 12px;
    border-radius: 12px;
    gap: 10px;
  }
  .entity-name {
    font-size: 16px;
  }
  .change-entity-btn {
    padding: 6px 10px;
    font-size: 12px;
  }
}
</style>
