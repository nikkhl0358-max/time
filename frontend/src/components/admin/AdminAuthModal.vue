<template>
  <Modal :isOpen="isOpen" title="Авторизация составителя" @close="handleClose">
    <form @submit.prevent="handleSubmit" class="auth-form">
      <div v-if="error" class="auth-error-banner" role="alert">
        <span>⚠️ {{ error }}</span>
        <button type="button" class="dismiss-error-btn" @click="clearError">✕</button>
      </div>

      <div class="form-field">
        <label for="admin-auth-username">Имя пользователя / логин</label>
        <input 
          id="admin-auth-username" 
          v-model.trim="username" 
          type="text" 
          required 
          autocomplete="username"
          class="auth-input"
          placeholder="admin"
          :disabled="loading"
          @input="clearError"
        />
      </div>

      <div class="form-field">
        <label for="admin-auth-password">Пароль</label>
        <input 
          id="admin-auth-password" 
          v-model="password" 
          type="password" 
          required 
          autocomplete="current-password"
          class="auth-input"
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
import { ref, watch } from 'vue';
import Modal from '../common/Modal.vue';
import { useAuth } from '../../composables/useAuth.js';

const props = defineProps({
  isOpen: { type: Boolean, default: false }
});

const emit = defineEmits(['close', 'login-success']);

const username = ref('');
const password = ref('');
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

async function handleSubmit() {
  if (!username.value || !password.value) return;
  const success = await login(username.value, password.value);
  if (success) {
    resetForm();
    emit('login-success');
    emit('close');
  }
}

watch(() => props.isOpen, (open) => {
  if (!open) {
    resetForm();
  } else {
    clearError();
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
  background: var(--orange-50);
  border: 1px solid var(--orange-200);
  color: var(--orange-700);
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.dismiss-error-btn {
  background: none;
  border: none;
  color: var(--orange-700);
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
