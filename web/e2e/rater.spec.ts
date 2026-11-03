import { expect, test, type Browser, type Page } from '@playwright/test'

const ADMIN = { Authorization: 'Bearer e2e-admin' }
const SHOTS = process.env.SHOTS_DIR ?? 'test-results/shots'

async function invite(page: Page, exp: string, tier: 'lab' | 'remote') {
  const res = await page.request.post(`/api/admin/experiments/${exp}/invites`, { headers: ADMIN, data: { tier, count: 1 } })
  expect(res.ok()).toBeTruthy()
  return (await res.json())[0] as string
}

async function newRater(browser: Browser, viewport: { width: number; height: number }, dpr: number) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: dpr })
  return context.newPage()
}

async function onboard(page: Page, exp: string, tier: 'lab' | 'remote' = 'remote') {
  await page.goto(await invite(page, exp, tier))
  await page.getByRole('button', { name: '同意并继续' }).click()
  await expect(page.getByRole('heading', { name: '基本信息' })).toBeVisible()
  await expect(page.locator('select')).toHaveCount(4)
  for (const select of await page.locator('select').all()) await select.selectOption({ index: 1 })
  await page.getByRole('button', { name: '继续' }).click()
  await expect(page.getByRole('heading', { name: /近黑台阶/ })).toBeVisible()
  await page.getByRole('button', { name: '5 级' }).click()
  await expect(page.getByRole('heading', { name: /近白台阶/ })).toBeVisible()
  await page.getByRole('button', { name: '5 级' }).click()
  if (tier === 'remote') await page.getByRole('button', { name: '没有，都很均匀' }).click()
}

test('pair: onboarding, practice feedback, exact-pixel images, undo and pause', async ({ browser }) => {
  const page = await newRater(browser, { width: 1800, height: 1000 }, 2)
  await onboard(page, 'demo-e1')
  await page.getByRole('button', { name: '开始练习' }).click()
  await expect(page.locator('.progress .label')).toContainText('练习 · 1/')
  await page.screenshot({ path: `${SHOTS}/pair-practice.png` })
  await page.waitForTimeout(400)
  await page.keyboard.press('2')
  await expect(page.locator('.feedback')).toContainText('参考答案')
  // Finish practice.
  while (await page.locator('.progress .label').textContent().then((t) => t?.startsWith('练习'))) {
    await page.waitForTimeout(1000)
    await page.keyboard.press('1')
    await page.waitForTimeout(150)
    if (await page.getByRole('button', { name: '开始第 1 块' }).isVisible()) break
  }
  await page.getByRole('button', { name: '开始第 1 块' }).click()
  await expect(page.locator('.progress .label')).toContainText('第 1 块 · 1/')
  const img = page.locator('.pair img').first()
  await expect(img).toBeVisible()
  await page.waitForTimeout(400)
  const geometry = await img.evaluate((el: HTMLImageElement) => ({
    css: el.getBoundingClientRect().width, natural: el.naturalWidth, dpr: window.devicePixelRatio,
  }))
  expect(geometry.css).toBe(720)
  expect(geometry.natural).toBe(1440) // one image pixel per device pixel
  await page.screenshot({ path: `${SHOTS}/pair-task.png` })
  await page.keyboard.press('3')
  await expect(page.locator('.progress .label')).toContainText('· 2/')
  await page.keyboard.press('Backspace')
  await expect(page.locator('.progress .label')).toContainText('· 1/')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('heading', { name: '已暂停' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('heading', { name: '已暂停' })).toBeHidden()
})

test('pair: window too small withholds the task', async ({ browser }) => {
  const page = await newRater(browser, { width: 1800, height: 1000 }, 2)
  await onboard(page, 'demo-e1')
  await page.getByRole('button', { name: '开始练习' }).click()
  await expect(page.locator('.pair img').first()).toBeVisible()
  await page.setViewportSize({ width: 1200, height: 800 })
  await expect(page.getByRole('heading', { name: '窗口太小' })).toBeVisible()
  await expect(page.getByText('至少 1520 ×')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/too-small.png` })
  await page.keyboard.press('1')
  await expect(page.locator('.progress .label')).toContainText('练习 · 1/') // keys are ignored while blocked
})

test('remote tier rejects fractional device pixel ratio', async ({ browser }) => {
  const page = await newRater(browser, { width: 1800, height: 1000 }, 1.25)
  await onboard(page, 'demo-e1')
  await expect(page.getByRole('heading', { name: '显示检查未通过' })).toBeVisible()
  await expect(page.getByText('设备像素比 1.25')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/display-failed.png` })
})

test('grid: multi-select, none-exclusive, best within accepted, zoom', async ({ browser }) => {
  const page = await newRater(browser, { width: 1500, height: 900 }, 2)
  await onboard(page, 'demo-e0', 'lab')
  await page.getByRole('button', { name: '开始第 1 块' }).click()
  await expect(page.locator('.grid img')).toHaveCount(6)
  await page.waitForTimeout(400)
  await page.keyboard.press('1')
  await page.keyboard.press('3')
  await expect(page.locator('.tile.accepted')).toHaveCount(2)
  await page.screenshot({ path: `${SHOTS}/grid-accept.png` })
  await page.locator('.grid .frame').nth(1).click()
  await expect(page.locator('.overlay.zoom img')).toBeVisible()
  expect(await page.locator('.overlay.zoom img').evaluate((el) => el.getBoundingClientRect().width)).toBe(720)
  await page.keyboard.press('Escape')
  await expect(page.locator('.overlay.zoom')).toBeHidden()
  await page.keyboard.press('Enter')
  await expect(page.getByText('再挑出最好的一张')).toBeVisible()
  await page.keyboard.press('2') // not accepted → ignored
  await expect(page.locator('.tile.best')).toHaveCount(0)
  await page.keyboard.press('3')
  await expect(page.locator('.tile.best')).toHaveCount(1)
  await page.screenshot({ path: `${SHOTS}/grid-best.png` })
  await page.keyboard.press('Enter')
  await expect(page.locator('.progress .label')).toContainText('· 2/')
})

test('admin: list, progress heatmap, rater QC', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 1000 })
  await page.goto('/admin/login?token=e2e-admin')
  await expect(page.getByRole('cell', { name: /演示 E1/ })).toBeVisible()
  await page.getByRole('link', { name: '演示 E1 · 成对比较' }).click()
  await expect(page.locator('.heat .cell').first()).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/admin-progress.png`, fullPage: true })
  await page.getByRole('button', { name: '评分者质控' }).click()
  await page.screenshot({ path: `${SHOTS}/admin-raters.png`, fullPage: true })
  await page.getByRole('button', { name: '预览' }).click()
  await expect(page.locator('.preview img').first()).toBeVisible()
})

test('region: overall rating, click regions, type and direction', async ({ browser }) => {
  const page = await newRater(browser, { width: 1500, height: 900 }, 2)
  await onboard(page, 'demo-e3', 'lab')
  await page.getByRole('button', { name: '开始第 1 块' }).click()
  await expect(page.locator('.region img')).toBeVisible()
  await page.waitForTimeout(400)
  await page.keyboard.press('2')
  await expect(page.getByText('整图 2 分')).toBeVisible()
  const canvas = page.locator('.region canvas')
  const box = (await canvas.boundingBox())!
  await canvas.click({ position: { x: box.width * 0.2, y: box.height * 0.3 } })
  await canvas.click({ position: { x: box.width * 0.8, y: box.height * 0.7 } })
  await expect(page.locator('.mark')).toHaveCount(2)
  await expect(page.getByRole('button', { name: '提交' })).toBeDisabled()
  for (const mark of await page.locator('.mark').all()) {
    await mark.getByRole('button', { name: '明暗层次' }).click()
    await mark.getByRole('button', { name: '该拉高' }).click()
  }
  await page.screenshot({ path: `${SHOTS}/region.png` })
  await page.getByRole('button', { name: '提交' }).click()
  await expect(page.locator('.progress .label')).toContainText('· 2/')
})
