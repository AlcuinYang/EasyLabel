// Screenshot tour of every screen for visual review: SHOTS_DIR=/some/dir npx playwright test screens
import { expect, test, type Browser, type Page } from '@playwright/test'

const DIR = process.env.SHOTS_DIR
test.skip(!DIR, 'set SHOTS_DIR to take the screenshot tour')

const ADMIN = { Authorization: 'Bearer e2e-admin' }

async function invite(page: Page, exp: string, tier: 'lab' | 'remote') {
  const res = await page.request.post(`/api/admin/experiments/${exp}/invites`, { headers: ADMIN, data: { tier, count: 1 } })
  return (await res.json())[0] as string
}

async function rater(browser: Browser, width = 1800, height = 1000, dpr = 2) {
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: dpr })
  return context.newPage()
}

async function fillProfile(page: Page) {
  await expect(page.getByRole('radiogroup')).toHaveCount(4)
  for (const group of await page.getByRole('radiogroup').all()) await group.getByRole('radio').nth(1).click()
}

const shot = (page: Page, name: string, fullPage = false) => page.screenshot({ path: `${DIR}/${name}.png`, fullPage })

test('rater onboarding and pair', async ({ browser }) => {
  const page = await rater(browser)
  await page.goto(await invite(page, 'demo-e1', 'remote'))
  await expect(page.getByRole('button', { name: '同意并继续' })).toBeVisible()
  await shot(page, '01-consent')
  await page.getByRole('button', { name: '同意并继续' }).click()
  await expect(page.getByRole('heading', { name: '基本信息' })).toBeVisible()
  await shot(page, '02-profile')
  await fillProfile(page)
  await page.getByRole('button', { name: '继续' }).click()
  await expect(page.getByRole('heading', { name: /近黑/ })).toBeVisible()
  await shot(page, '03-display-black')
  await page.getByRole('button', { name: '5 级' }).click()
  await expect(page.getByRole('heading', { name: /近白/ })).toBeVisible()
  await page.getByRole('button', { name: '5 级' }).click()
  await expect(page.getByRole('heading', { name: /像素/ })).toBeVisible()
  await shot(page, '04-display-pixel')
  await page.getByRole('button', { name: '没有，都很均匀' }).click()
  await expect(page.getByRole('button', { name: '开始练习' })).toBeVisible()
  await shot(page, '05-practice-intro')
  await page.getByRole('button', { name: '开始练习' }).click()
  await page.waitForTimeout(600)
  await shot(page, '06-pair-practice')
  await page.keyboard.press('2')
  await page.waitForTimeout(150)
  await shot(page, '07-pair-feedback')
  await page.keyboard.press('Escape')
  await page.waitForTimeout(150)
  await shot(page, '08-paused')
  await page.keyboard.press('Escape')
  await page.keyboard.press('n')
  await page.waitForTimeout(150)
  await shot(page, '09-note')
  await page.keyboard.press('Escape')
  await page.setViewportSize({ width: 1200, height: 800 })
  await page.waitForTimeout(150)
  await shot(page, '10-too-small')
})

test('rater display failure', async ({ browser }) => {
  const page = await rater(browser, 1800, 1000, 1.25)
  await page.goto(await invite(page, 'demo-e1', 'remote'))
  await page.getByRole('button', { name: '同意并继续' }).click()
  await fillProfile(page)
  await page.getByRole('button', { name: '继续' }).click()
  await page.getByRole('button', { name: '2 级' }).click()
  await expect(page.getByRole('heading', { name: '近白台阶' })).toBeVisible()
  await page.getByRole('button', { name: '5 级' }).click()
  await page.getByRole('button', { name: '有条带或波纹' }).click()
  await expect(page.getByRole('heading', { name: '显示检查未通过' })).toBeVisible()
  await shot(page, '11-display-failed')
})

async function lab(browser: Browser, exp: string) {
  const page = await rater(browser, 1500, 900)
  await page.goto(await invite(page, exp, 'lab'))
  await page.getByRole('button', { name: '同意并继续' }).click()
  await fillProfile(page)
  await page.getByRole('button', { name: '继续' }).click()
  await page.getByRole('button', { name: '5 级' }).click()
  await expect(page.getByRole('heading', { name: '近白台阶' })).toBeVisible()
  await page.getByRole('button', { name: '5 级' }).click()
  await expect(page.getByRole('button', { name: '开始第 1 块' })).toBeVisible()
  return page
}

test('rater grid', async ({ browser }) => {
  const page = await lab(browser, 'demo-e0')
  await shot(page, '12-ready')
  await page.getByRole('button', { name: '开始第 1 块' }).click()
  await page.waitForTimeout(600)
  await page.keyboard.press('1')
  await page.keyboard.press('3')
  await shot(page, '13-grid-accept')
  await page.keyboard.press('Enter')
  await page.keyboard.press('3')
  await shot(page, '14-grid-best')
})

test('rater region', async ({ browser }) => {
  const page = await lab(browser, 'demo-e3')
  await page.getByRole('button', { name: '开始第 1 块' }).click()
  await page.waitForTimeout(600)
  await shot(page, '15-region-overall')
  await page.keyboard.press('2')
  const canvas = page.locator('.region canvas')
  const box = (await canvas.boundingBox())!
  await canvas.click({ position: { x: box.width * 0.2, y: box.height * 0.3 } })
  await canvas.click({ position: { x: box.width * 0.8, y: box.height * 0.7 } })
  await page.locator('.mark').first().getByRole('button', { name: '明暗层次' }).click()
  await page.waitForTimeout(150)
  await shot(page, '16-region-marks')
})

test('admin', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.goto('/admin/login?token=e2e-admin')
  await expect(page.getByText('演示 E1').first()).toBeVisible()
  await shot(page, '20-admin-list', true)
  await page.getByText('演示 E1 · 成对比较').first().click()
  await page.waitForTimeout(400)
  await shot(page, '21-admin-progress', true)
  for (const [tab, name] of [['评分者', '22-admin-raters'], ['邀请', '23-admin-invites'], ['预览', '24-admin-preview'], ['配置', '25-admin-config']]) {
    await page.getByRole('tab', { name: tab }).click()
    await page.waitForTimeout(400)
    await shot(page, name, true)
  }
})
