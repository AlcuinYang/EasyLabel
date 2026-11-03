import { resolve } from 'node:path'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Two entry points; the FastAPI app serves static/{rater,admin}/index.html and static/assets/.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: resolve(__dirname, '../easylabel/server/static'),
    emptyOutDir: true,
    rollupOptions: {
      input: { rater: resolve(__dirname, 'rater/index.html'), admin: resolve(__dirname, 'admin/index.html') },
    },
  },
  test: { include: ['src/**/*.test.ts'] }, // e2e/ belongs to Playwright
  server: { proxy: { '/api': 'http://127.0.0.1:8765', '/i': 'http://127.0.0.1:8765', '/r': 'http://127.0.0.1:8765' } },
})
