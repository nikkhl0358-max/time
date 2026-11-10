<template>
  <Modal :isOpen="isOpen" title="Авторизация составителя" @close="handleClose">
    <form @submit.prevent="handleSubmit" class="auth-form">
      <div v-if="error" class="auth-error-banner" role="alert">
        <div class="auth-error-message">
          <svg class="auth-error-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="12"></line>
            <line x1="12" y1="16" x2="12.01" y2="16"></line>
          </svg>
          <span>{{ error }}</span>
        </div>
        <button type="button" class="dismiss-error-btn" @click="clearError" aria-label="Закрыть ошибку">✕</button>
      </div>

      <div class="form-field">
        <label for="admin-auth-username">Имя пользователя / логин</label>
        <input 
          id="admin-auth-username" 
          ref="usernameInputRef"
          v-model.trim="username" 
          type="text" 
          required 
          autocomplete="username"
          :class="['auth-input', { 'has-error': error }]"
          placeholder="admin"
          :disabled="loading"
          @input="clearError"
        />
      </div>

      <div class="form-field">
        <label for="admin-auth-password">Пароль</label>
        <input 
          id="admin-auth-password" 
          ref="passwordInputRef"
          v-model="password" 
          type="password" 
          required 
          autocomplete="current-password"
          :class="['auth-input', { 'has-error': error }]"
          placeholder="••••••••"
          :disabled="loading"
          @input="clearError"
        />
      </div>

      <button 
        type="submit" 
        :disabled="loading || !username || !password" 
        class="auth-submit-btn"
      >
        <span v-if="loading" class="btn-loading">
          <span class="btn-spinner"></span>
          Вход в систему...
        </span>
        <span v-else>Войти в панель управления</span>
      </button>
    </form>
  </Modal>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue';
import Modal from '../common/Modal.vue';
import { useAuth } from '../../composables/useAuth.js';

const props = defineProps({
  isOpen: { type: Boolean, default: false }
});

const emit = defineEmits(['close', 'login-success']);

const username = ref('');
const password = ref('');
const usernameInputRef = ref(null);
const passwordInputRef = ref(null);

const { login, authLoading: loading, authError: error } = useAuth();

function resetForm() {
  username.value = '';
  password.value = '';
  error.value = null;
}

function clearError() {
  if (error.value) error.value = null;
}

function handleClose() {
  resetForm();
  emit('close');
}

/**
 * Автофокусировка: если логин заполнен — фокус на пароль, иначе на логин.
 */
async function focusInitialInput() {
  await nextTick();
  if (username.value && passwordInputRef.value) {
    passwordInputRef.value.focus();
  } else if (usernameInputRef.value) {
    usernameInputRef.value.focus();
  }
}

async function handleSubmit() {
  if (!username.value || !password.value) return;
  const success = await login(username.value, password.value);
  if (success) {
    resetForm();
    emit('login-success');
    emit('close');
  } else {
    await nextTick();
    passwordInputRef.value?.focus();
    passwordInputRef.value?.select();
  }
}

watch(() => props.isOpen, async (open) => {
  if (!open) {
    resetForm();
  } else {
    clearError();
    await focusInitialInput();
  }
});
</script>

<style scoped>
.auth-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.auth-error-banner {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #991b1b;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.auth-error-message {
  display: flex;
  align-items: center;
  gap: 8px;
}

.auth-error-icon {
  flex-shrink: 0;
  color: #ef4444;
}

.dismiss-error-btn {
  background: none;
  border: none;
  color: #991b1b;
  cursor: pointer;
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 4px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-field label {
  font-size: 12px;
  font-weight: 700;
  color: var(--ink-secondary);
}

.auth-input {
  padding: 10px 14px;
  border-radius: 9px;
  border: 1px solid var(--border-light);
  font-size: 14px;
  color: var(--ink-primary);
  outline: none;
  transition: all 0.2s ease;
}

.auth-input:focus {
  border-color: var(--blue-600);
  box-shadow: 0 0 0 3px rgba(2, 132, 199, 0.15);
}

.auth-input.has-error {
  border-color: #ef4444;
  background-color: #fff8f8;
}

.auth-input.has-error:focus {
  border-color: #dc2626;
  box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.18);
}

.auth-submit-btn {
  margin-top: 8px;
  padding: 12px;
  background: var(--blue-600);
  color: #ffffff;
  border: none;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.25);
  transition: all 0.2s ease;
}

.auth-submit-btn:hover:not(:disabled) {
  background: var(--blue-700);
}

.auth-submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.btn-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #ffffff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
