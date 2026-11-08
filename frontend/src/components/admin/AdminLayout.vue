<template>
  <div class="admin-layout">
    <!-- Баннер уведомлений о публикации -->
    <div v-if="publishNotification" :class="['publish-alert', publishNotification.type]">
      <span>{{ publishNotification.message }}</span>
      <button type="button" class="close-alert-btn" @click="publishNotification = null">✕</button>
    </div>

    <div class="admin-topbar">
      <div class="admin-title-wrap">
        <span class="admin-tag">РЕЖИМ СОСТАВИТЕЛЯ</span>
        <h2>Панель управления расписанием</h2>
      </div>

      <div class="admin-actions">
        <button 
          :disabled="isPublishing"
          class="publish-btn"
          @click="handlePublish"
        >
          <span v-if="isPublishing" class="publishing-wrap">
            <span class="mini-spinner"></span>
            Публикация...
          </span>
          <span v-else>🚀 Опубликовать в онлайн</span>
        </button>

        <button class="exit-admin-btn" @click="$emit('exit')">
          Выйти в публичный портал
        </button>
      </div>
    </div>

    <!-- Табы панели -->
    <div class="admin-tabs" role="tablist">
      <button 
        v-for="tab in TABS" 
        :key="tab.key"
        :class="['admin-tab-btn', { active: currentTab === tab.key }]"
        role="tab"
        :aria-selected="currentTab === tab.key"
        @click="currentTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- Содержимое активного таба -->
    <div class="admin-tab-content">
      <!-- Замены -->
      <AdminSubstitutionsEditor
        v-if="currentTab === 'substitutions'"
        :substitutions="substitutions"
        :groups="groups"
        :teachers="teachers"
        :rooms="rooms"
        :subjects="subjects"
        @add-sub="handleAddSub"
        @remove-sub="handleRemoveSub"
      />

      <!-- Расписание -->
      <div v-else-if="currentTab === 'schedule'" class="admin-placeholder-pane">
        <h3>Редактор сетки занятий</h3>
        <p>Интерактивная матрица распределения нагрузки по дням недели и периодам.</p>
        <div class="stats-grid">
          <div class="stat-card">
            <span class="stat-num">{{ (groups || []).length }}</span>
            <span class="stat-desc">Групп</span>
          </div>
          <div class="stat-card">
            <span class="stat-num">{{ (teachers || []).length }}</span>
            <span class="stat-desc">Преподавателей</span>
          </div>
          <div class="stat-card">
            <span class="stat-num">{{ (rooms || []).length }}</span>
            <span class="stat-desc">Аудиторий</span>
          </div>
        </div>
      </div>

      <!-- Справочники: Преподаватели/Группы/Аудитории -->
      <div v-else class="admin-entities-table">
        <div class="table-header">
          <h3>Справочник: {{ getActiveTabTitle() }}</h3>
          <span class="table-count">Всего: {{ getActiveList().length }}</span>
        </div>
        <div class="entities-list">
          <div 
            v-for="item in getActiveList().slice(0, 30)" 
            :key="item.id"
            class="entity-row"
          >
            <span class="entity-name">{{ item.name || item.number }}</span>
            <span class="entity-id">ID: {{ item.id }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import AdminSubstitutionsEditor from './AdminSubstitutionsEditor.vue';
import { storagePublicationCommit } from '../../storage.js';

const TABS = [
  { key: 'schedule', label: 'Расписание' },
  { key: 'substitutions', label: 'Управление заменами' },
  { key: 'groups', label: 'Группы' },
  { key: 'teachers', label: 'Преподаватели' },
  { key: 'rooms', label: 'Кабинеты' }
];

const props = defineProps({
  groups: { type: Array, default: () => [] },
  teachers: { type: Array, default: () => [] },
  rooms: { type: Array, default: () => [] },
  subjects: { type: Array, default: () => [] },
  substitutions: { type: Array, default: () => [] }
});

const emit = defineEmits(['exit', 'update-substitutions']);

const currentTab = ref('substitutions');
const isPublishing = ref(false);
const publishNotification = ref(null);

function getActiveTabTitle() {
  const t = TABS.find(tab => tab.key === currentTab.value);
  return t ? t.label : '';
}

function getActiveList() {
  if (currentTab.value === 'groups') return props.groups || [];
  if (currentTab.value === 'teachers') return props.teachers || [];
  if (currentTab.value === 'rooms') return props.rooms || [];
  return [];
}

function handleAddSub(newSub) {
  const updated = [...(props.substitutions || []), { ...newSub, id: `sub_${Date.now()}` }];
  emit('update-substitutions', updated);
}

function handleRemoveSub(subId) {
  const updated = (props.substitutions || []).filter(s => s.id !== subId);
  emit('update-substitutions', updated);
}

async function handlePublish() {
  isPublishing.value = true;
  publishNotification.value = null;
  try {
    await storagePublicationCommit('schedule-data-v2', { scope: 'all' });
    publishNotification.value = {
      type: 'success',
      message: '✅ Расписание и замены успешно опубликованы на публичном портале!'
    };
  } catch (err) {
    publishNotification.value = {
      type: 'error',
      message: '❌ Ошибка при публикации: ' + (err.message || 'Сбой соединения')
    };
  } finally {
    isPublishing.value = false;
  }
}
</script>

<style scoped>
.admin-layout {
  padding: 24px 32px;
  max-width: 1400px;
  margin: 0 auto;
}

.publish-alert {
  padding: 12px 18px;
  border-radius: 10px;
  margin-bottom: 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 14px;
  font-weight: 600;
}

.publish-alert.success {
  background: #ecfdf5;
  color: #065f46;
  border: 1px solid #a7f3d0;
}

.publish-alert.error {
  background: #fef2f2;
  color: #991b1b;
  border: 1px solid #fecaca;
}

.close-alert-btn {
  background: none;
  border: none;
  font-size: 14px;
  cursor: pointer;
  color: inherit;
}

.admin-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  flex-wrap: wrap;
  gap: 16px;
}

.admin-title-wrap h2 {
  font-size: 24px;
  font-weight: 800;
  color: var(--ink-primary);
  margin-top: 4px;
}

.admin-tag {
  font-size: 11px;
  font-weight: 800;
  color: var(--orange-700);
  background: var(--orange-100);
  padding: 3px 10px;
  border-radius: 12px;
}

.admin-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.publish-btn {
  padding: 10px 20px;
  background: var(--orange-500);
  color: #ffffff;
  border: none;
  border-radius: 10px;
  font-weight: 800;
  font-size: 14px;
  cursor: pointer;
  box-shadow: var(--shadow-accent);
  transition: all 0.2s ease;
}

.publish-btn:hover:not(:disabled) {
  background: var(--orange-600);
  transform: translateY(-1px);
}

.publish-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.publishing-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mini-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.exit-admin-btn {
  padding: 10px 18px;
  background: #ffffff;
  border: 1px solid var(--border-light);
  border-radius: 10px;
  color: var(--ink-secondary);
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.exit-admin-btn:hover {
  border-color: var(--blue-600);
  color: var(--blue-700);
}

.admin-tabs {
  display: flex;
  gap: 8px;
  border-bottom: 2px solid var(--border-light);
  margin-bottom: 24px;
  overflow-x: auto;
}

.admin-tab-btn {
  padding: 10px 18px;
  background: transparent;
  border: none;
  border-bottom: 3px solid transparent;
  font-size: 14px;
  font-weight: 700;
  color: var(--ink-muted);
  cursor: pointer;
  margin-bottom: -2px;
  transition: all 0.2s ease;
  white-space: nowrap;
}

.admin-tab-btn.active {
  color: var(--blue-700);
  border-bottom-color: var(--blue-600);
}

.admin-placeholder-pane, .admin-entities-table {
  background: #ffffff;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 24px;
  box-shadow: var(--shadow-subtle);
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-top: 20px;
}

.stat-card {
  padding: 18px;
  background: var(--blue-50);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stat-num {
  font-size: 28px;
  font-weight: 800;
  color: var(--blue-700);
}

.stat-desc {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-muted);
}

.entities-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
  margin-top: 16px;
}

.entity-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  background: var(--bg-app);
  border-radius: 8px;
  font-size: 13px;
}

.entity-name {
  font-weight: 700;
  color: var(--ink-primary);
}

.entity-id {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-muted);
}
</style>
