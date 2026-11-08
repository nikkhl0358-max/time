<template>
  <footer class="app-footer">
    <div class="footer-container">
      <div class="footer-info">
        <div class="footer-brand">
          <div class="footer-icon-3d"></div>
          <span class="footer-title">КТМУ · Расписание</span>
        </div>
        <p class="footer-desc">
          Колледж туризма и медиатехнологий. Интерактивная система расписания.
        </p>
        <span class="footer-copy">© 2026 Все права защищены</span>
      </div>

      <div class="footer-actions">
        <button
          type="button"
          class="admin-entry-btn"
          :title="isAdmin ? 'Перейти в кабинет составителя' : 'Вход в панель управления для составителей'"
          @click="$emit('open-admin')"
        >
          <span class="admin-key-icon">🔐</span>
          <span>{{ isAdmin ? 'Кабинет составителя (Админ)' : 'Войти (Админ)' }}</span>
        </button>
      </div>
    </div>
  </footer>
</template>

<script setup>
defineProps({
  isAdmin: { type: Boolean, default: false }
});

defineEmits(['open-admin']);
</script>

<style scoped>
.app-footer {
  background: #ffffff;
  border-top: 1px solid var(--border-light);
  padding: 32px 0 28px;
  margin-top: auto;
}

.footer-container {
  max-width: 1440px;
  margin: 0 auto;
  padding: 0 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.footer-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.footer-brand {
  display: flex;
  align-items: center;
  gap: 8px;
}

.footer-icon-3d {
  width: 18px;
  height: 18px;
  border-radius: 4px;
  background: linear-gradient(135deg, var(--blue-600) 0%, var(--orange-500) 100%);
  box-shadow: 0 2px 6px rgba(2, 132, 199, 0.3);
}

.footer-title {
  font-size: 15px;
  font-weight: 800;
  color: var(--ink-primary);
  letter-spacing: -0.01em;
}

.footer-desc {
  font-size: 13px;
  color: var(--ink-muted);
  margin: 0;
}

.footer-copy {
  font-size: 11px;
  color: var(--ink-muted);
  opacity: 0.85;
}

.footer-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.admin-entry-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 18px;
  min-height: 44px;
  background: #ffffff;
  border: 1px solid var(--border-light);
  border-radius: 12px;
  color: var(--ink-secondary);
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
}

.admin-entry-btn:hover {
  border-color: var(--blue-500);
  color: var(--blue-700);
  background: var(--blue-50);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.15);
}

.admin-entry-btn:active {
  transform: translateY(0);
}

.admin-key-icon {
  font-size: 14px;
}

@media (max-width: 768px) {
  .app-footer {
    padding: 24px 0 20px;
  }
  .footer-container {
    flex-direction: column;
    text-align: center;
    padding: 0 20px;
    gap: 18px;
  }
  .footer-brand {
    justify-content: center;
  }
  .admin-entry-btn {
    width: 100%;
    justify-content: center;
    max-width: 280px;
  }
}
</style>
