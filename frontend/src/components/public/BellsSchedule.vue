<template>
  <div class="bells-container">
    <div class="bells-list">
      <div 
        v-for="bell in bells" 
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
defineProps({
  bells: { type: Array, required: true },
  activePeriodNumber: { type: Number, default: null }
});
</script>

<style scoped>
.bells-container {
  display: flex;
  flex-direction: column;
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
