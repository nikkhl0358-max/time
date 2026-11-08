<template>
  <header class="app-navbar">
    <div class="nav-left">
      <div 
        class="brand-logo" 
        role="button"
        tabindex="0"
        title="Сбросить фильтры и вернуться на главную"
        @click="$emit('reset-view')"
        @keydown.enter="$emit('reset-view')"
        @keydown.space.prevent="$emit('reset-view')"
      >
        <div class="brand-icon-3d">
          <div class="brand-cube-face face-front"></div>
          <div class="brand-cube-face face-side"></div>
        </div>
        <div class="brand-text">
          <span class="brand-title">КТМУ Расписание</span>
          <span class="brand-sub">Колледж технологии, моделирования и управления</span>
        </div>
      </div>

      <nav class="role-selector" aria-label="Выбор вида расписания">
        <button 
          :class="['role-btn', { active: activeRole === 'group' }]"
          @click="handleRoleClick('group')"
        >
          Группы
        </button>
        <button 
          :class="['role-btn', { active: activeRole === 'teacher' }]"
          @click="handleRoleClick('teacher')"
        >
          Преподаватели
        </button>
        <button 
          :class="['role-btn', { active: activeRole === 'room' }]"
          @click="handleRoleClick('room')"
        >
          Аудитории
        </button>
      </nav>
    </div>

    <div class="nav-right">
      <div 
        v-if="activePair" 
        class="live-pair-pill"
        role="button"
        tabindex="0"
        title="Текущая пара. Нажмите, чтобы открыть звонки"
        @click="handleBellsClick"
        @keydown.enter="handleBellsClick"
      >
        <span class="pulse-indicator"></span>
        <span class="pair-text">Пара {{ activePair.period }}</span>
        <span class="pair-timer">
          {{ activePair.minutesLeft > 0 ? activePair.minutesLeft + ' мин' : 'завершается' }}
        </span>
      </div>

      <button 
        class="nav-action-btn" 
        @click="handleBellsClick"
        title="Посмотреть расписание звонков"
      >
        Звонки
      </button>

      <button 
        class="nav-action-btn orange" 
        @click="handleSubsClick"
        title="Посмотреть оперативные замены"
      >
        Замены
        <span v-if="substitutionsCount > 0" class="sub-counter">{{ substitutionsCount }}</span>
      </button>
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  role: { type: String, default: 'group' },
  currentRole: { type: String, default: '' },
  activePair: { type: Object, default: null },
  substitutionsCount: { type: Number, default: 0 }
});

const emit = defineEmits([
  'reset-view',
  'select-role',
  'update:role',
  'toggle-bells',
  'open-bells',
  'toggle-substitutions',
  'open-subs'
]);

const activeRole = computed(() => {
  return props.currentRole || props.role || 'group';
});

function handleRoleClick(newRole) {
  emit('select-role', newRole);
  emit('update:role', newRole);
}

function handleBellsClick() {
  emit('toggle-bells');
  emit('open-bells');
}

function handleSubsClick() {
  emit('toggle-substitutions');
  emit('open-subs');
}
</script>

<style scoped>
.app-navbar {
  height: 68px;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-light);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 32px;
  position: sticky;
  top: 0;
  z-index: 50;
}

.nav-left, .nav-right {
  display: flex;
  align-items: center;
  gap: 20px;
}

.brand-logo {
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  user-select: none;
  border-radius: 8px;
  padding: 4px;
  transition: opacity 0.2s ease;
}

.brand-logo:hover {
  opacity: 0.88;
}

.brand-icon-3d {
  width: 28px;
  height: 28px;
  position: relative;
  transform: perspective(600px) rotateX(25deg) rotateY(-30deg);
  transform-style: preserve-3d;
}

.brand-cube-face {
  position: absolute;
  inset: 0;
  border-radius: 6px;
}

.face-front {
  background: var(--blue-600);
  box-shadow: 0 4px 10px rgba(2, 132, 199, 0.35);
}

.face-side {
  background: var(--orange-500);
  transform: translateZ(-8px) translateX(6px) translateY(4px);
  opacity: 0.85;
}

.brand-text {
  display: flex;
  flex-direction: column;
}

.brand-title {
  font-size: 15px;
  font-weight: 800;
  color: var(--ink-primary);
  letter-spacing: -0.02em;
}

.brand-sub {
  font-size: 10px;
  color: var(--ink-secondary);
  font-weight: 500;
  white-space: nowrap;
}

.role-selector {
  display: flex;
  background: var(--blue-50);
  padding: 3px;
  border-radius: 12px;
  border: 1px solid var(--blue-100);
}

.role-btn {
  padding: 6px 14px;
  border-radius: 9px;
  border: none;
  background: transparent;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-secondary);
  cursor: pointer;
  transition: all 0.2s ease;
}

.role-btn.active {
  background: var(--bg-card);
  color: var(--blue-700);
  box-shadow: 0 2px 6px rgba(2, 132, 199, 0.15);
}

.live-pair-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: var(--orange-50);
  border: 1px solid var(--orange-200);
  border-radius: 20px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.live-pair-pill:hover {
  background: var(--orange-100);
  border-color: var(--orange-400);
  transform: translateY(-1px);
}

.pulse-indicator {
  width: 8px;
  height: 8px;
  background: var(--orange-500);
  border-radius: 50%;
  animation: pulse-ring 1.8s infinite;
}

@keyframes pulse-ring {
  0% { transform: scale(0.9); opacity: 1; }
  50% { transform: scale(1.3); opacity: 0.6; }
  100% { transform: scale(0.9); opacity: 1; }
}

.pair-text {
  font-weight: 700;
  color: var(--orange-700);
}

.pair-timer {
  color: var(--ink-muted);
  font-size: 11px;
}

.nav-action-btn {
  padding: 7px 14px;
  background: #ffffff;
  border: 1px solid var(--border-light);
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.2s ease;
}

.nav-action-btn:hover {
  border-color: var(--blue-500);
  color: var(--blue-600);
}

.nav-action-btn.orange:hover {
  border-color: var(--orange-500);
  color: var(--orange-600);
}

.sub-counter {
  padding: 1px 6px;
  background: var(--orange-500);
  color: #fff;
  border-radius: 10px;
  font-size: 10px;
  font-weight: 700;
}

@media (max-width: 900px) {
  .app-navbar {
    padding: 10px 16px;
    height: auto;
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
  .nav-left {
    justify-content: space-between;
    width: 100%;
    gap: 12px;
  }
  .nav-right {
    justify-content: space-between;
    width: 100%;
    gap: 8px;
  }
  .role-selector {
    flex: 1;
    display: flex;
    justify-content: center;
  }
  .role-btn {
    flex: 1;
    text-align: center;
    padding: 8px 6px;
    min-height: 40px;
  }
}

@media (max-width: 520px) {
  .app-navbar {
    padding: 8px 12px;
  }
  .brand-sub {
    display: none;
  }
  .brand-title {
    font-size: 14px;
  }
  .role-btn {
    font-size: 11.5px;
    padding: 6px 4px;
  }
  .nav-right {
    flex-wrap: wrap;
    gap: 6px;
  }
  .live-pair-pill {
    flex: 1 1 100%;
    justify-content: center;
    order: -1;
    margin-bottom: 2px;
    padding: 5px 8px;
    font-size: 11px;
  }
  .nav-action-btn {
    flex: 1;
    justify-content: center;
    padding: 6px 10px;
    font-size: 12px;
    min-height: 40px;
  }
}
</style>
