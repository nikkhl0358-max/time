<template>
  <div v-if="hasNotices" class="public-notices" aria-label="Объявления">
    <div v-if="publicIntroText" class="notice-box intro-box">
      <div class="notice-content">{{ publicIntroText }}</div>
    </div>
    <div 
      v-for="a in announcements" 
      :key="a.id || a.date" 
      class="notice-box announcement-box"
    >
      <div v-if="a.date" class="notice-date">
        📅 {{ formatDate(a.date) }}
      </div>
      <div class="notice-content">{{ a.text }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  announcements: { type: Array, default: () => [] },
  publicIntroText: { type: String, default: '' }
});

const hasNotices = computed(() => {
  return Boolean(props.publicIntroText || (props.announcements && props.announcements.length > 0));
});

function formatDate(dateStr) {
  if (!dateStr) return '';
  const [y, m, d] = String(dateStr).split('-');
  if (!y || !m || !d) return dateStr;
  const months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  const monthName = months[Number(m) - 1] || m;
  return `${Number(d)} ${monthName} ${y} г.`;
}
</script>

<style scoped>
.public-notices {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 20px;
}

.notice-box {
  background: var(--paper-2, #ffffff);
  border: 1px solid var(--line, #e2e8f0);
  border-left: 4px solid var(--accent, #3b82f6);
  border-radius: 8px;
  padding: 14px 18px;
  font-size: 14px;
  line-height: 1.55;
  white-space: pre-wrap;
  color: var(--ink-primary, #0f172a);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.announcement-box {
  border-left-color: #f59e0b;
  background: #fffbeb;
}

.notice-date {
  font-size: 12px;
  font-weight: 700;
  color: #b45309;
  margin-bottom: 6px;
  letter-spacing: 0.02em;
}

.notice-content {
  color: var(--ink-primary, #1e293b);
  word-break: break-word;
}
</style>
