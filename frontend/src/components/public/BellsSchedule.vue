<template>
  <div class="bells-container">
    <div class="bells-tabs" role="tablist">
      <button 
        type="button" 
        :class="['bell-tab-btn', { active: currentTab === 'standard' }]"
        @click="currentTab = 'standard'"
      >
        🏛 Главный корпус
      </button>
      <button 
        type="button" 
        :class="['bell-tab-btn', { active: currentTab === 'voz' }]"
        @click="currentTab = 'voz'"
      >
        🏢 Вознесенский пр.
      </button>
      <button 
        type="button" 
        :class="['bell-tab-btn', { active: currentTab === 'short' }]"
        @click="currentTab = 'short'"
      >
        ⏱ Сокращённый
      </button>
    </div>

    <div class="bells-list">
      <div 
        v-for="bell in displayedBells" 
        :key="bell.period"
        :class="['bell-row', { active: activePeriodNumber === bell.period }]"
      >
        <div class="period-circle">
          {{ bell.period }}
        </div>
        <div class="time-range">
          <span class="bell-time">{{ bell.start }} — {{ bell.end }}</span>
          <span v-if="activePeriodNumber === bell.period" class="active-badge">
            Идет сейчас
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import { STANDARD_BELLS, VOZNESENSKY_BELLS, SHORTENED_BELLS } from '../../composables/useCurrentTime.js';

const props = defineProps({
  bells: { type: Array, default: () => [] },
  semesterConfig: { type: Object, default: () => ({}) },
  activePeriodNumber: { type: Number, default: null }
});

const currentTab = ref('standard');

function padTime(t) {
  if (!t) return '';
  const [h, m] = t.trim().split(':');
  return `${h.padStart(2, '0')}:${m || '00'}`;
}

function parsePeriodString(str, periodNum) {
  if (!str) return null;
  const clean = String(str).replace('–', '-');
  const parts = clean.split('-').map(s => s.trim());
  if (parts.length < 2 || !parts[0] || !parts[1]) return null;
  return { period: periodNum, start: padTime(parts[0]), end: padTime(parts[1]) };
}

function parsePeriodList(list, fallback) {
  if (!Array.isArray(list) || !list.length) return fallback;
  const parsed = list
    .map((str, idx) => parsePeriodString(str, idx + 1))
    .filter(Boolean);
  return parsed.length ? parsed : fallback;
}

const displayedBells = computed(() => {
  const cfg = props.semesterConfig || {};
  if (currentTab.value === 'voz') {
    return parsePeriodList(cfg.voznesenskyPeriodTimes, VOZNESENSKY_BELLS);
  }
  if (currentTab.value === 'short') {
    return parsePeriodList(cfg.shortenedPeriodTimes, SHORTENED_BELLS);
  }
  return parsePeriodList(cfg.periodTimes, props.bells?.length ? props.bells : STANDARD_BELLS);
});
</script>

<style scoped>
.bells-container {
  display: flex;
  flex-direction: column;
}

.bells-tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 14px;
  background: var(--blue-50, #eff6ff);
  padding: 4px;
  border-radius: 10px;
}

.bell-tab-btn {
  flex: 1;
  padding: 7px 10px;
  border: none;
  background: transparent;
  font-size: 12px;
  font-weight: 700;
  color: var(--ink-secondary, #64748b);
  border-radius: 7px;
  cursor: pointer;
  transition: all 0.18s ease;
  white-space: nowrap;
}

.bell-tab-btn.active {
  background: #ffffff;
  color: var(--blue-700, #1d4ed8);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.06);
}

.bells-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.bell-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  background: #ffffff;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  transition: all 0.2s ease;
}

.bell-row.active {
  background: var(--orange-50);
  border-color: var(--orange-500);
  box-shadow: 0 4px 14px rgba(249, 115, 22, 0.15);
}

.period-circle {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--blue-100);
  color: var(--blue-700);
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
}

.bell-row.active .period-circle {
  background: var(--orange-500);
  color: #ffffff;
}

.time-range {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 1;
}

.bell-time {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-primary);
}

.active-badge {
  font-size: 11px;
  font-weight: 700;
  color: var(--orange-700);
  background: var(--orange-100);
  padding: 3px 8px;
  border-radius: 6px;
}
</style>
