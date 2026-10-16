import { defineConfig, devices } from '@playwright/test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'

const USERS_FILE = process.env.FA_E2E_USERS_FILE ?? join(tmpdir(), `fa-e2e-users-${randomUUID()}.yaml`)
rmSync(USERS_FILE, { force: true })
process.env.FA_E2E_USERS_FILE = USERS_FILE
// Browser tests against the real backend + built frontend, with a deterministic fake model (e2e/fake_llm.py).
// Projects are written to a throw-away directory; run `npm run build` first (`make web-e2e` does both).
const API_PORT = 8011
const MODEL_PORT = 8799

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 30_000 },
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${API_PORT}`, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `uv run uvicorn fake_llm:app --app-dir e2e --host 127.0.0.1 --port ${MODEL_PORT}`,
      port: MODEL_PORT,
      reuseExistingServer: false,
    },
    {
      command: `uv run uvicorn src.webapp.api:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: '..',
      url: `http://127.0.0.1:${API_PORT}/api/auth/me`,
      reuseExistingServer: false,
      env: {
        FA_API_BASE: `http://127.0.0.1:${MODEL_PORT}/v1`,
        FA_ORCHESTRATOR_MODEL: 'fake',
        FA_TRACING_ENABLED: 'false',
        FA_OUTPUT_DIR: mkdtempSync(join(tmpdir(), 'fa-e2e-')),
        FA_USERS_FILE: USERS_FILE,
        FA_SESSION_SECRET: 'fa-e2e-session-secret',
      },
    },
  ],
})
