/**
 * Управление состоянием авторизации администратора.
 */
import { ref } from 'vue';
import { authRequest } from '../auth.js';

const currentUser = ref(null);
const authLoading = ref(false);
const authError = ref(null);

export function useAuth() {
  async function checkSession() {
    authLoading.value = true;
    try {
      const res = await authRequest('session');
      currentUser.value = res?.user || null;
      return currentUser.value;
    } catch {
      currentUser.value = null;
      return null;
    } finally {
      authLoading.value = false;
    }
  }

  async function login(username, password) {
    authLoading.value = true;
    authError.value = null;
    try {
      const payload = { login: username, username, password };
      const res = await authRequest('login', payload);
      currentUser.value = res?.user || { username, login: username };
      return true;
    } catch (err) {
      authError.value = err.message || 'Ошибка входа';
      return false;
    } finally {
      authLoading.value = false;
    }
  }

  async function logout() {
    authLoading.value = true;
    try {
      await authRequest('logout', {});
    } catch (err) {
      console.warn('Сбой при вызове logout на сервере:', err);
    } finally {
      currentUser.value = null;
      authLoading.value = false;
    }
  }

  return {
    currentUser,
    authLoading,
    authError,
    checkSession,
    login,
    logout
  };
}
