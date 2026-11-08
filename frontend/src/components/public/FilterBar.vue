<template>
  <div class="filter-bar">
    <!-- Блок текущей выбранной сущности и кнопка смены -->
    <div class="current-selection-box">
      <div class="entity-info">
        <span class="entity-type-label">{{ entityRoleLabel }}</span>
        <div class="entity-name-row">
          <h2 class="entity-name">{{ selectedName || 'Не выбрано' }}</h2>
        </div>
      </div>

      <button 
        type="button" 
        class="change-entity-btn"
        @click="handleChangeClick"
        :title="`Сменить ${entityRoleGenitive}`"
      >
        <span>Сменить {{ entityRoleGenitive }}</span>
      </button>
    </div>

    <!-- Управление неделями и экспорт -->
    <div class="actions-group">
      <div class="week-navigation">
        <button 
          class="week-nav-btn" 
          :disabled="selectedWeek <= 1"
          @click="changeWeek(selectedWeek - 1)"
          title="Предыдущая неделя"
          aria-label="Предыдущая неделя"
        >
          ◀
        </button>

        <div class="week-pill">
          <span class="week-label">Учебная неделя</span>
          <span class="week-number">{{ selectedWeek }}</span>
        </div>

        <button 
          class="week-nav-btn" 
          :disabled="selectedWeek >= 24"
          @click="changeWeek(selectedWeek + 1)"
          title="Следующая неделя"
          aria-label="Следующая неделя"
        >
          ▶
        </button>
      </div>

      <div class="action-buttons-row">
        <button 
          class="export-btn" 
          :disabled="!hasItems"
          @click="$emit('export-csv')" 
          title="Экспорт в CSV / Excel"
        >
          Скачать CSV
        </button>

        <button 
          class="print-btn" 
          @click="$emit('print')" 
          title="Распечатать расписание"
        >
          Печать
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  role: { type: String, default: 'group' },
  items: { type: Array, default: () => [] },
  selectedId: { type: [String, Number], default: '' },
  selectedName: { type: String, default: '' },
  selectedWeek: { type: Number, default: 10 }
});

const emit = defineEmits(['change-selection', 'update:selectedWeek', 'export-csv', 'print']);

const hasItems = computed(() => Boolean(props.selectedId));

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
function changeWeek(newWeek) {
  const clamped = Math.max(1, Math.min(24, newWeek));
  emit('update:selectedWeek', clamped);
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
  gap: 6px;
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
  border: none;
  background: #ffffff;
  color: var(--blue-700);
  font-size: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  transition: all 0.2s ease;
}

.week-nav-btn:hover:not(:disabled) {
  background: var(--blue-600);
  color: #ffffff;
}

.week-nav-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.week-pill {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 0 10px;
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
  display: flex;
  align-items: center;
  gap: 5px;
  transition: all 0.2s ease;
}

.export-btn:hover:not(:disabled), .print-btn:hover {
  border-color: var(--blue-500);
  color: var(--blue-700);
  background: var(--blue-50);
}

.export-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
