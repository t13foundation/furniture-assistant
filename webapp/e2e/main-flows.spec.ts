import { expect, test, type Page } from '@playwright/test'
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
