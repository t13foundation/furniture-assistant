import { expect, test, type Page } from '@playwright/test'
import { randomBytes, scryptSync } from 'node:crypto'
import { rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
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

async function openModule(page: Page) {
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
  const usersFile = join(tmpdir(), 'fa-e2e-users.yaml')
  const salt = randomBytes(16)
  const hash = scryptSync('viewer-secret', salt, 64, { N: 2 ** 14, r: 8, p: 1 })
  writeFileSync(
    usersFile,
    `users:\n  viewer:\n    salt: ${salt.toString('base64')}\n    password_hash: ${hash.toString('base64')}\n    role: viewer\n`,
  )
  try {
    await page.goto('/')
    await page.getByLabel('Username').fill('viewer')
    await page.getByLabel('Password').fill('viewer-secret')
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.locator('.signed-in-user')).toHaveText('viewer')

    expect(await page.evaluate(async () => (await fetch('/api/projects')).status)).toBe(200)
    const projectId = decodeURIComponent(new URL(projectUrl).hash.split('/')[2])
    expect(
      await page.evaluate(async (id) => (await fetch(`/api/projects/${id}/analyze`, { method: 'POST' })).status, projectId),
    ).toBe(403)
    await page.goto(projectUrl)
    await expect(page.getByRole('link', { name: '+ New project' })).toHaveCount(0)
    await page.getByRole('link', { name: /Szafa testowa/ }).first().click()
    await expect(page.getByRole('button', { name: 'Edit', exact: true })).toHaveCount(0)
    await reviewTab(page).click()
    await expect(page.getByRole('button', { name: 'Ask the model again' })).toHaveCount(0)
    await expect(page.locator('.module-workspace section[inert]')).toBeVisible()
  } finally {
    rmSync(usersFile, { force: true })
  }
})
