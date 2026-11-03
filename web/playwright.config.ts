import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: 'e2e',
  timeout: 60_000,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:8799', screenshot: 'only-on-failure' },
  webServer: { command: 'bash e2e/serve.sh', url: 'http://127.0.0.1:8799/api/docs', timeout: 240_000, reuseExistingServer: true },
})
