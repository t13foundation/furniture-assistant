import { expect, test, type Page } from '@playwright/test'
import { randomBytes, scryptSync } from 'node:crypto'
import { rmSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// One project is created by the upload test and reused by the following tests (serial, one worker).
const drawing = fileURLToPath(new URL('./fixtures/szafa.png', import.meta.url))
let projectUrl = ''

test.beforeEach(() => console.log('PID', process.pid, 'projectUrl', JSON.stringify(projectUrl)))

function reviewTab(page: Page) {
  return page.getByRole('button', { name: /^Review \d+$/ })
}

async function reviewCount(page: Page): Promise<number> {
  const label = (await reviewTab(page).textContent()) ?? ''
  return Number(label.replace(/\D/g, ''))
}

async function ensureProjectUrl(page: Page) {
  if (!projectUrl) {
    await page.goto('/')
    await page
      .getByRole('link', { name: /E2E szafa/ })
      .first()
      .click()
    projectUrl = page.url()
  }
}

async function openModule(page: Page) {
  await ensureProjectUrl(page)
  await page.goto(projectUrl)
  await page
    .getByRole('link', { name: /Szafa testowa/ })
    .first()
    .click()
  await expect(page.getByRole('heading', { name: /Szafa testowa/ })).toBeVisible()
}

test('upload a drawing and get an analysed 3D module', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/#/new')
  await page.getByPlaceholder('e.g. Apartment 04').fill('E2E szafa')
  await page.locator('input[type="file"]').setInputFiles(drawing)
  await expect(page.getByText('1 file ready')).toBeVisible()
  await page.getByRole('button', { name: 'Run analysis' }).click()

  await expect(page).toHaveURL(/#\/p\/[^/]+$/)
  projectUrl = page.url()
  await expect(page.getByRole('link', { name: /Szafa testowa/ }).first()).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText(/Project total: \d+ requests · \d+ input \/ \d+ output tokens/)).toBeVisible()
  await expect(page.getByText(/This analysis: \d+ requests · \d+ input \/ \d+ output tokens/)).toBeVisible()

  await page
    .getByRole('link', { name: /Szafa testowa/ })
    .first()
    .click()
  await expect(page.getByRole('button', { name: 'Drawing and 3D' })).toBeVisible()
  await expect(page.locator('canvas').first()).toBeVisible()
  await expect(page.getByLabel('Cabinet kind')).toHaveValue('wardrobe')
  expect(errors).toEqual([])
})

test('review: confirm one item and confirm all readings', async ({ page }) => {
  await openModule(page)
  await reviewTab(page).click()
  const before = await reviewCount(page)
  expect(before).toBeGreaterThan(0)

  const confirm = page
    .getByRole('button', { name: 'Confirm', exact: true })
    .and(page.locator(':enabled'))
    .first()
  await confirm.click()
  await expect.poll(() => reviewCount(page)).toBeLessThan(before)

  const confirmAll = page.getByRole('button', { name: /Confirm all readings/ })
  if (await confirmAll.isVisible()) {
    const beforeAll = await reviewCount(page)
    await confirmAll.click()
    await expect.poll(() => reviewCount(page)).toBeLessThan(beforeAll)
  }
})

test('edit the module, see it in the history and restore the analysed version', async ({ page }) => {
  await openModule(page)
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  await page.getByLabel('height (mm)').fill('2300')
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByRole('button', { name: 'Save changes' })).toBeHidden()

  await page.getByRole('button', { name: 'History' }).click()
  await expect(page.getByRole('cell', { name: 'editor_save' }).first()).toBeVisible()
  const analysed = page
    .getByRole('row')
    .filter({ has: page.getByRole('cell', { name: 'analysis', exact: true }) })
  await analysed.getByRole('button', { name: 'Restore' }).click()
  await expect(page.getByRole('cell', { name: 'restore', exact: true }).first()).toBeVisible()

  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(page.getByLabel('height (mm)')).toHaveValue('2200')
  await page.getByRole('button', { name: 'Cancel' }).click()
})

test('valuation uses project pricing with markup', async ({ page }) => {
  await page.goto(`${projectUrl}/pricing`)
  await expect(page.getByText(/Using workspace pricing/)).toBeVisible()
  await page.getByLabel('Markup percent').fill('10')
  await page.getByRole('button', { name: 'Save pricing' }).click()
  await expect(page.getByRole('button', { name: 'Revert to workspace' })).toBeVisible()

  await page.goto(`${projectUrl}/valuation`)
  await expect(page.getByText('Total with markup')).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Markup' })).toBeVisible()
})

test('project review overview and activity list the work done', async ({ page }) => {
  await page.goto(`${projectUrl}/review`)
  await expect(page.getByRole('link', { name: /Szafa testowa|SZ01/ }).first()).toBeVisible()

  await page.goto(`${projectUrl}/activity`)
  for (const action of [
    'create_project',
    'analyze_finish',
    'answers',
    'editor_save',
    'restore',
    'pricing_change',
  ]) {
    await expect(page.getByRole('cell', { name: action, exact: true }).first()).toBeVisible()
  }
})

test('narrow screens hide the sidebar behind a menu button', async ({ page }) => {
  await page.setViewportSize({ width: 600, height: 900 })
  await page.goto(projectUrl)
  const menu = page.getByRole('button', { name: 'Open project navigation' })
  await expect(menu).toBeVisible()
  await expect(page.getByRole('link', { name: /Szafa testowa/ }).first()).toBeHidden()
  await menu.click()
  await expect(page.getByRole('link', { name: /Szafa testowa/ }).first()).toBeVisible()
})

test('viewer can read projects but cannot mutate or access editing controls', async ({ page }) => {
  const usersFile = process.env.FA_E2E_USERS_FILE
  if (!usersFile) throw new Error('FA_E2E_USERS_FILE must be set by playwright.config.ts')
  const salt = randomBytes(16)
  const hash = scryptSync('viewer-secret', salt, 64, { N: 2 ** 14, r: 8, p: 1 })
  writeFileSync(
    usersFile,
    `users:\n  viewer:\n    salt: ${salt.toString('base64')}\n    password_hash: ${hash.toString('base64')}\n    role: viewer\n`,
  )
  try {
    expect(await (await page.request.get('/api/auth/me')).json()).toMatchObject({ auth_enabled: true })
    await page.goto('/')
    await page.getByLabel('Username').fill('viewer')
    await page.getByLabel('Password').fill('viewer-secret')
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.locator('.signed-in-user')).toHaveText('viewer')

    expect(await page.evaluate(async () => (await fetch('/api/projects')).status)).toBe(200)
    const projectId = decodeURIComponent(new URL(projectUrl).hash.split('/')[2])
    expect(
      await page.evaluate(
        async (id) => (await fetch(`/api/projects/${id}/analyze`, { method: 'POST' })).status,
        projectId,
      ),
    ).toBe(403)
    await page.goto(projectUrl)
    await expect(page.getByRole('link', { name: '+ New project' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Rename project' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Delete project' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Run analysis' })).toHaveCount(0)
    await page
      .getByRole('link', { name: /Szafa testowa/ })
      .first()
      .click()
    await expect(page.getByRole('button', { name: 'Edit', exact: true })).toHaveCount(0)
    await reviewTab(page).click()
    await expect(page.getByRole('heading', { name: 'Review', exact: true })).toBeVisible()
    await expect(page.locator('.module-workspace section[inert]')).toHaveCount(0)
    await page.goto(`/#/p/${encodeURIComponent(projectId)}/pricing`)
    await expect(page.getByRole('heading', { name: 'Pricing', exact: true })).toBeVisible()
    await expect(page.getByText('Markup percent')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Compare with client quote' })).toBeVisible()
    await expect(page.getByText('Quote workbook (.xlsx)')).toBeVisible()
    await expect(page.getByLabel('Markup percent')).toBeDisabled()
    await expect(page.getByLabel('Quote workbook (.xlsx)')).toBeDisabled()
    for (const control of await page
      .locator('select[aria-label^="Modules for "], input[aria-label^="Ignore "]')
      .all()) {
      await expect(control).toBeDisabled()
    }
  } finally {
    rmSync(usersFile, { force: true })
  }
})
test('A to B to A does not expose stale module data after a failed reload', async ({ page }) => {
  await ensureProjectUrl(page)
  const projectId = new URL(projectUrl).hash.match(/\/p\/([^/]+)/)?.[1]
  expect(projectId).toBeTruthy()
  await page.route('**/api/projects/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname !== `/api/projects/${projectId}` || route.request().method() !== 'GET') {
      await route.continue()
      return
    }
    const response = await route.fetch()
    const detail = (await response.json()) as { modules: Array<Record<string, unknown>> }
    await route.fulfill({
      response,
      json: {
        ...detail,
        modules: [
          ...detail.modules,
          { ...detail.modules[0], module_id: 'SZ02', title: 'Second cabinet', group_id: 'SZ02' },
        ],
      },
    })
  })
  let aRequests = 0
  await page.route('**/api/projects/*/modules/*', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (route.request().method() === 'GET' && pathname.endsWith('/modules/SZ01')) {
      aRequests += 1
      if (aRequests === 2) {
        await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'module unavailable' }),
        })
        return
      }
    } else if (route.request().method() === 'GET' && pathname.endsWith('/modules/SZ02')) {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'module unavailable' }),
      })
      return
    }
    await route.continue()
  })

  await page.goto(projectUrl)
  await expect(page.getByRole('button', { name: 'Edit', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: /Second cabinet/ })).toBeVisible()
  await page.getByRole('link', { name: /Second cabinet/ }).click()
  await expect(page.getByText('module unavailable')).toBeVisible()
  await page.getByRole('link', { name: /Szafa testowa/ }).click()

  await expect.poll(() => aRequests).toBe(2)
  await expect(page.getByRole('button', { name: 'Edit', exact: true })).toHaveCount(0)
  await expect(page.getByText('module unavailable')).toBeVisible()
})

test('successful report reload clears its previous error', async ({ page }) => {
  let failed = false
  await ensureProjectUrl(page)
  await page.route('**/api/projects/*/summary/materials', async (route) => {
    if (!failed) {
      failed = true
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'report unavailable' }),
      })
    } else {
      await route.continue()
    }
  })
  await page.goto(`${projectUrl}/materials`)
  await expect(page.getByText('report unavailable')).toBeVisible()

  await page.getByRole('link', { name: 'Production processes', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Production processes' })).toBeVisible()
  await page.getByRole('link', { name: 'Material summary', exact: true }).click()

  await expect(page.getByRole('heading', { name: 'Material summary' })).toBeVisible()
  await expect(page.getByText('report unavailable')).toHaveCount(0)
})

test('rename and delete a project', async ({ page }) => {
  await page.goto('/#/new')
  await page.getByPlaceholder('e.g. Apartment 04').fill('Rename delete e2e')
  await page.locator('input[type="file"]').setInputFiles(drawing)
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(page).toHaveURL(/#\/p\/[^/]+$/)
  await expect(page.getByRole('link', { name: /Szafa testowa/ }).first()).toBeVisible({ timeout: 60_000 })

  await page.getByRole('button', { name: 'Rename project' }).click()
  await page.getByLabel('Project name').fill('Unsaved E2E project')
  await page.getByRole('button', { name: 'Cancel rename' }).click()
  await expect(page.getByText('Rename delete e2e')).toBeVisible()

  await page.getByRole('button', { name: 'Rename project' }).click()
  await page.getByLabel('Project name').fill('Renamed E2E project')
  await page.getByRole('button', { name: 'Save project name' }).click()
  await expect(page.getByText('Renamed E2E project')).toBeVisible()

  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: 'Delete project' }).click()
  await expect(page).toHaveURL(/#\/$/)
  await expect(page.getByText(/Rename delete e2e|Renamed E2E project/)).toHaveCount(0)
})
