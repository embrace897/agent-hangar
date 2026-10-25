// TEMPORARY: screenshots for the Agent Hangar README video. Not part of the suite; deleted after the capture.
import { chromium, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from './fixtures.ts'

const URL_ = process.env.HANGAR_PLATFORM_URL ?? ''
const OUT = process.env.SHOTS_DIR ?? ''
const W = 1440
const H = 900

test.skip(!URL_ || !OUT, 'capture only')
test.setTimeout(240_000)

async function browserAs(user: string, password: string): Promise<Page> {
  const browser = await chromium.launch({ channel: 'msedge' })
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, locale: 'pt-BR' })
  const r = await ctx.request.post(`${URL_}/api/auth/password`, { data: { username: user, password } })
  expect(r.ok()).toBe(true)
  return ctx.newPage()
}

test('capture', async ({ hangar }) => {
  mkdirSync(OUT, { recursive: true })
  const { app, page, invoke } = hangar
  const shot = (p: Page, name: string) => p.screenshot({ path: join(OUT, `${name}.png`) })
  await app.evaluate(({ BrowserWindow }, [w, h]) => {
    const win = BrowserWindow.getAllWindows()[0]
    win.setContentSize(w, h)
    win.center()
  }, [W, H])
  await page.waitForTimeout(800)

  // Admin: the console hub and the sync button on the LLM connection.
  const admin = await browserAs(process.env.HANGAR_PLATFORM_USER!, process.env.HANGAR_PLATFORM_PASSWORD!)
  await admin.goto(`${URL_}/ui#/app`)
  await admin.waitForTimeout(2500)
  await shot(admin, 'c01-hub')
  const csrf = (await admin.context().cookies()).find((c) => c.name === 'hangar_csrf')?.value ?? ''
  await admin.request.patch(`${URL_}/api/catalog/llm/claude-empresa/desktop`, {
    headers: { 'x-csrf-token': csrf },
    data: { sync: false }
  })
  await admin.goto(`${URL_}/ui#/providers/custom-anthropic`)
  await admin.waitForTimeout(2500)
  const toCard = () =>
    admin.evaluate(() => {
      const el = [...document.querySelectorAll('b, strong, h3, div')].find(
        (e) => e.childElementCount === 0 && e.textContent?.trim() === 'claude-empresa'
      )
      el?.scrollIntoView({ block: 'start' })
      window.scrollBy(0, -24)
    })
  await toCard()
  await admin.waitForTimeout(600)
  await shot(admin, 'c02-sync')
  await admin.locator('.cd-sync[data-v="1"]:not([disabled])').last().click()
  await admin.waitForTimeout(2500)
  await toCard()
  await admin.waitForTimeout(600)
  await shot(admin, 'c02b-synced')

  // Member: the app before connecting.
  await shot(page, 'd01-home')
  await invoke('platform:setConfig', { url: URL_, mode: 'optional' })
  await page.keyboard.press('Control+,')
  await page.getByRole('button', { name: 'Plataforma', exact: true }).click()
  await page.waitForTimeout(800)
  await shot(page, 'd02-settings')

  // Sign in: the app opens the browser on the platform's consent screen.
  await app.evaluate(({ shell }) => {
    shell.openExternal = async (url: string) => {
      ;(globalThis as { __authUrl?: string }).__authUrl = url
    }
  })
  await page.getByRole('button', { name: 'Conectar à plataforma' }).first().click()
  let authUrl: string | undefined
  await expect
    .poll(async () => (authUrl = await app.evaluate(() => (globalThis as { __authUrl?: string }).__authUrl)))
    .toBeTruthy()
  const member = await browserAs(process.env.HANGAR_PLATFORM_MEMBER_USER!, process.env.HANGAR_PLATFORM_MEMBER_PASSWORD!)
  await member.goto(authUrl!)
  await member.waitForTimeout(2500)
  await shot(member, 'd03-consent')
  await member.getByRole('button', { name: /Autorizar|Permitir|Aprovar|Conectar/ }).first().click()
  await expect
    .poll(async () => (await invoke<{ status: string }>('platform:state')).status, { timeout: 30_000 })
    .toBe('connected')
  await expect
    .poll(async () => (await invoke<{ models: { status: string } }>('platform:state')).models.status, { timeout: 30_000 })
    .not.toBe('syncing')
  await page.waitForTimeout(1500)
  await shot(page, 'd04-connected')
  await page.keyboard.press('Escape')

  // Workspace.
  const nav = page.getByRole('region', { name: 'Agent Hangar' })
  await expect(nav.getByLabel(/Ao vivo/)).toBeVisible({ timeout: 30_000 })
  await nav.getByRole('button', { name: 'Início', exact: true }).click()
  await page.waitForTimeout(2000)
  await shot(page, 'd05-inicio')
  const agents = nav.getByRole('button', { name: 'Meus agentes', exact: true })
  if (await agents.count()) {
    await agents.click()
    await page.waitForTimeout(2000)
    await shot(page, 'd06-agentes')
  }

  // A session on a synced token model: app -> platform proxy -> provider.
  await hangar.openNewSession({ title: 'Contrato ACME' })
  const picker = page.getByLabel('Modelo', { exact: true })
  const option = picker.locator('optgroup[label^="Modelos de token"] option', { hasText: 'Sonnet' }).first()
  await picker.selectOption({ label: (await option.textContent()) ?? '' })
  await page.waitForTimeout(800)
  const composer = page.getByPlaceholder(/Responder ao Claude/)
  await composer.fill('Resuma o que mudou no contrato renovado da ACME e o que eu preciso revisar antes de assinar.')
  await shot(page, 'd08-picker')
  await composer.press('Enter')
  await expect(page.getByText(/Reajuste/).first()).toBeVisible({ timeout: 90_000 })
  await page.waitForTimeout(700)
  await shot(page, 'd09-streaming')
  await expect(page.getByText(/Sugiro levar o item 2/)).toBeVisible({ timeout: 90_000 })
  await page.waitForTimeout(1500)
  await shot(page, 'd10-answer')

  // Admin sees the device.
  await admin.goto(`${URL_}/ui#/app/devices`)
  await admin.waitForTimeout(2500)
  await shot(admin, 'c03-devices')
  await admin.goto(`${URL_}/ui#/app`)
  await admin.waitForTimeout(2500)
  await shot(admin, 'c04-hub-after')

  await invoke('platform:disconnect').catch(() => undefined)
})
