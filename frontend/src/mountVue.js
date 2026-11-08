import { createApp } from 'vue';
import App from './App.vue';
import './assets/base.css';

export function mountVue() {
  const root = document.getElementById('app');
  if (!root) return;
  const app = createApp(App);
  app.config.errorHandler = (err, instance, info) => {
    console.error('Глобальная ошибка Vue:', err, info);
  };
  app.mount(root);
}
