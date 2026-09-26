import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      // Everything under /api (including /api/v1) goes to the api service, exactly like the
      // frontend nginx in the cluster (docker/nginx.frontend.conf.template). The api mounts the
      // /api/v1 document routes itself and hands OCR to Celery -> idp; sending /api/v1 straight to
      // idp here registered uploads in the idp process, so the api's document list never saw them.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  optimizeDeps: {
    exclude: ['lucide-react'],
  },
});
