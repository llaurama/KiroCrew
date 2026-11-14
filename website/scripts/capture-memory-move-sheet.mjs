/**
 * Screenshot harness for the memory provider picker rows and the move sheet.
 *
 * Runs the REAL built SPA (website/dist) behind `serveDist` and answers every
 * /api/** call through `stubDashboardApi`, plus the provider overview and the
 * move job routes. Shots, light and dark:
 *   01  Settings > Memory > Memory provider: a provider that can replace Crew
 *       memory and one that cannot (its gaps line), each with "Move Crew memory here";
 *   02  the move sheet's plan (counts, samples per kind);
 *   03  the move sheet after the run, with "Undo this move".
 *
 * Usage: node scripts/capture-memory-move-sheet.mjs [outDir]
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

import { serveDist } from './lib/serve-dist.mjs'
import { stubDashboardApi } from './lib/stub-dashboard-api.mjs'

const OUT = process.argv[2] || '../temp-screenshots/37204-memory-move'
mkdirSync(OUT, { recursive: true })

const caps = over => ({
  api_version: 1,
  provider: { name: 'Example', version: '1.0.0' },
  egress: 'local',
  read_only: false,
  ops: ['context', 'search', 'get', 'list', 'put', 'delete', 'put_batch', 'changes'],
  kinds: ['preference', 'fact', 'episode', 'summary', 'rule', 'finding', 'document'],
  search: { lexical: true, semantic: true, filters: ['kind'] },
  writes: { verbatim: true, preserves_timestamps: true, keeps_origin: true, idempotent: true, dry_run_put: false, max_batch: 50, max_text: 64000 },
  history: { revisions: false, retired: true, changes: true },
  ingest: 'raw',
  isolation: { multi_store: true, principal_auth: false },
  documents: 'readwrite',
  steering: 'none',
  max_page: 100,
  ...over,
})

const OVERVIEW = {
  mode: 'builtin',
  id: '',
  timeout_secs: 3,
  switched_at: 0,
  stop_learning_ack: false,
  store: 'default',
  builtin: { id: 'builtin', display_name: 'Crew memory' },
  providers: [
    {
      id: 'app:cloud-memory/cloud', app: 'cloud-memory', app_version: '1.0.0',
      display_name: 'Cloud memory', description: 'Keeps memory in a cloud memory service.',
      settings_page: '', enabled: true, capabilities: caps({}), replace_gaps: [],
      mode_gaps: { augment: [], replace: [] }, egress_consented: true,
      latency: { calls: 12, ops: { context: { p50_ms: 80, p95_ms: 140, count: 12 } } },
    },
    {
      id: 'app:graph-memory/graph', app: 'graph-memory', app_version: '0.4.0',
      display_name: 'Graph memory', description: 'An MCP memory server, through its bridge.',
      settings_page: '', enabled: true,
      capabilities: caps({ ops: ['context', 'search', 'list', 'put'], writes: { ...caps({}).writes, verbatim: false, idempotent: false }, documents: 'none', history: { revisions: false, retired: false, changes: false } }),
      replace_gaps: ['op:get', 'op:delete', 'writes.verbatim', 'writes.idempotent'],
      mode_gaps: { augment: [], replace: ['op:get', 'op:delete', 'writes.verbatim', 'writes.idempotent'] },
      egress_consented: true,
    },
  ],
  shared_crew: false,
  policy_allows: true,
  active: { mode: 'builtin', id: 'builtin', unavailable: '' },
}

const sample = (id, kind, text) => ({ id, kind, text, planned: 'would_write' })
const PLAN = {
  id: 'a1b2c3d4e5f60718', from: 'builtin', to: 'app:cloud-memory/cloud', store: 'default', dry_run: true,
  state: 'planned', checkpoint: { cursor: null, listed: 1214 },
  by_kind: { rule: 61, finding: 25, preference: 40, fact: 1078, document: 2, summary: 8 },
  outcomes: { would_write: 1206, 'refused:content_screen': 3, 'skipped:retired': 5 },
  skipped_kinds: {}, notes: [], verify: {}, rolled_back: 0, error: '',
  samples: {
    rule: [sample('lesson:1', 'rule', 'Keep commit subjects under 72 characters.'), sample('lesson:2', 'rule', 'Run the formatter only on the files you changed.')],
    preference: [sample('fact:pref.reply', 'preference', 'Prefers short replies with the conclusion first.')],
    document: [sample('doc:preferences', 'document', '# Preferences - Tabs over spaces - Short replies')],
  },
}
const DONE = {
  ...PLAN, id: 'b1b2c3d4e5f60718', dry_run: false, state: 'done',
  outcomes: { inserted: 1180, deduped: 26, 'refused:content_screen': 3, 'skipped:retired': 5 },
  verify: { checked: 24, matched: 24, mismatched: 0 }, samples: undefined,
}

const PAUSED = { ...DONE, id: 'c1b2c3d4e5f60718', state: 'paused', outcomes: { inserted: 400 }, verify: {}, error: 'the provider stayed unavailable (timed out); resume when it is back' }
const ROLLED = { ...DONE, state: 'rolled_back', rolled_back: 1180 }
const ROLLBACK_FAILED = { ...DONE, state: 'rollback_failed', rolled_back: 310, error: 'rollback stopped: timed out' }
const REPLACED = { ...OVERVIEW, mode: 'replace', id: 'app:cloud-memory/cloud', active: { mode: 'replace', id: 'app:cloud-memory/cloud', unavailable: '' } }
const AUGMENTED = { ...OVERVIEW, mode: 'augment', id: 'app:graph-memory/graph', active: { mode: 'augment', id: 'app:graph-memory/graph', unavailable: '' } }


const TEAM = {
  id: 'app:team-memory/team', app: 'team-memory', app_version: '2.0.0',
  display_name: 'Team memory', description: 'A remote memory service with one shared memory.',
  settings_page: '', enabled: true,
  capabilities: caps({ egress: 'remote', ingest: 'none', isolation: { multi_store: false, principal_auth: false } }),
  replace_gaps: [], mode_gaps: { augment: [], replace: [] }, egress_consented: false,
}
const CONSENTS = { ...OVERVIEW, providers: [TEAM, ...OVERVIEW.providers] }
const NOTICES = {
  ...OVERVIEW,
  mode: 'replace', id: 'app:cloud-memory/cloud',
  active: { mode: 'replace', id: 'app:cloud-memory/cloud', unavailable: 'the provider app is not running' },
  providers: [
    { ...OVERVIEW.providers[0] },
    { ...TEAM, egress_consented: true },
    { ...OVERVIEW.providers[1], capabilities: caps({ read_only: true, ops: ['context', 'search', 'get', 'list'] }), mode_gaps: { augment: [], replace: ['read_only'] }, replace_gaps: ['read_only'] },
    { id: 'app:old-memory/old', app: 'old-memory', app_version: '0.1.0', display_name: 'Old memory', description: '', settings_page: '', enabled: false, blocked_reason: 'the provider app is not enabled' },
  ],
}
const NONE = { ...OVERVIEW, providers: [] }

const { srv, base } = await serveDist()
const browser = await chromium.launch()

/** One page with the stubs; `run` is what a non-dry-run POST answers, `undo` a rollback. */
async function open(theme, { overview = OVERVIEW, run = DONE, undo = ROLLED, failMove = false, waitFor = 'Graph memory' } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 })
  const page = await context.newPage()
  const jobs = new Map([[PLAN.id, PLAN], [run.id, run]])
  await stubDashboardApi(page, {
    theme,
    localStorageEntries: { 'mc-lang': 'en' },
    extra: async (path, route) => {
      const method = route.request().method()
      const json = body => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
      if (path === '/api/memory/stores') return json({ stores: [], active: 'default' }), true
      if (path === '/api/memory/retired') return json({ retired: [] }), true
      if (path === '/api/memory/backups') return json({ backups: [] }), true
      if (path === '/api/memory/stats' || path === '/api/memory/settings') return json({}), true
      if (path === '/api/memory/semantic') return json({ items: [], total: 0 }), true
      if (['/api/memory/preferences', '/api/memory/projects', '/api/memory/history'].includes(path))
        return json({ content: '' }), true
      if (path === '/api/memory/provider' && method === 'PUT') return json({ ok: true, mode: 'replace' }), true
      if (path === '/api/memory/provider') return json(overview), true
      if (path === '/api/memory/provider/migrations' && method === 'POST') {
        const body = route.request().postDataJSON()
        if (failMove && !body.dry_run)
          return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'provider unavailable', code: 'provider_unavailable' }) }), true
        return json(body.dry_run ? PLAN : run), true
      }
      if (path.endsWith('/rollback')) {
        jobs.set(run.id, undo)
        return json(undo), true
      }
      if (path.startsWith('/api/memory/provider/migrations/')) {
        const id = path.split('/')[5]
        return json(jobs.get(id) ?? PLAN), true
      }
      return false
    },
  })
  await page.goto(base + '/settings/overview?view=memory', { waitUntil: 'domcontentloaded' })
  const picker = page.getByTestId('memory-provider-picker')
  await picker.waitFor({ state: 'visible', timeout: 20000 })
  if (waitFor) await page.getByText(waitFor).first().waitFor({ state: 'visible', timeout: 10000 })
  else await page.waitForTimeout(1500)
  return { context, page, picker }
}

async function settle(page) {
  await page.waitForTimeout(700)
}

async function startCopy(page, picker) {
  await picker.getByRole('button', { name: 'Copy Crew memory here' }).first().click()
  await page.getByTestId('memory-move-sheet').waitFor({ state: 'visible', timeout: 10000 })
  await page.getByRole('button', { name: 'Copy', exact: true }).waitFor({ state: 'visible' })
  await settle(page)
}

for (const theme of ['light', 'dark']) {
  // 01-04: the picker, the plan, the finished copy, and after Undo.
  {
    const { context, page, picker } = await open(theme)
    await picker.screenshot({ path: join(OUT, `${theme}-01-picker.png`) })
    await startCopy(page, picker)
    await page.getByTestId('memory-move-sheet').locator('details').first().evaluate(el => { el.open = true })
    await page.getByRole('dialog').screenshot({ path: join(OUT, `${theme}-02-plan.png`) })
    await page.getByRole('button', { name: 'Copy', exact: true }).click()
    await page.getByRole('button', { name: 'Undo this copy' }).waitFor({ state: 'visible', timeout: 10000 })
    await settle(page)
    await page.getByRole('dialog').screenshot({ path: join(OUT, `${theme}-03-done.png`) })
    await page.getByRole('button', { name: 'Undo this copy' }).click()
    await page.getByText(/Removed .* items this copy added/).waitFor({ state: 'visible', timeout: 10000 })
    await settle(page)
    await page.getByRole('dialog').screenshot({ path: join(OUT, `${theme}-04-undone.png`) })
    await context.close()
  }
  // 05: a copy paused by a provider outage, with Resume.
  {
    const { context, page, picker } = await open(theme, { run: PAUSED })
    await startCopy(page, picker)
    await page.getByRole('button', { name: 'Copy', exact: true }).click()
    await page.getByRole('button', { name: 'Resume' }).waitFor({ state: 'visible', timeout: 10000 })
    await settle(page)
    await page.getByRole('dialog').screenshot({ path: join(OUT, `${theme}-05-paused.png`) })
    await context.close()
  }
  // 06: an undo that stopped part way, offered again as an undo.
  {
    const { context, page, picker } = await open(theme, { undo: ROLLBACK_FAILED })
    await startCopy(page, picker)
    await page.getByRole('button', { name: 'Copy', exact: true }).click()
    await page.getByRole('button', { name: 'Undo this copy' }).click()
    await page.getByRole('button', { name: 'Try undo again' }).waitFor({ state: 'visible', timeout: 10000 })
    await settle(page)
    await page.getByRole('dialog').screenshot({ path: join(OUT, `${theme}-06-undo-failed.png`) })
    await context.close()
  }
  // 07: the switch sheet for "Use instead of Crew memory".
  {
    const { context, page, picker } = await open(theme)
    await picker.getByRole('button', { name: 'Use instead of Crew memory' }).first().click()
    await page.getByRole('dialog').waitFor({ state: 'visible', timeout: 10000 })
    await settle(page)
    await page.getByRole('dialog').screenshot({ path: join(OUT, `${theme}-07-switch-sheet.png`) })
    await context.close()
  }
  // 08-09: the picker while a provider replaces Crew memory, and while one is a read-only source.
  for (const [name, overview] of [['08-picker-replace', REPLACED], ['09-picker-augment', AUGMENTED]]) {
    const { context, picker } = await open(theme, { overview })
    await picker.screenshot({ path: join(OUT, `${theme}-${name}.png`) })
    await context.close()
  }
  // 10: the switch sheet for a remote provider that takes no chat summaries and keeps one memory.
  {
    const { context, page, picker } = await open(theme, { overview: CONSENTS, waitFor: 'Team memory' })
    await picker.getByRole('button', { name: 'Use instead of Crew memory' }).first().click()
    await page.getByRole('dialog').waitFor({ state: 'visible', timeout: 10000 })
    await settle(page)
    await page.getByRole('dialog').screenshot({ path: join(OUT, `${theme}-10-switch-consents.png`) })
    await context.close()
  }
  // 11: switching back to Crew memory, with the copy back offered.
  {
    const { context, page, picker } = await open(theme, { overview: REPLACED })
    await picker.getByRole('button', { name: 'Use Crew memory' }).click()
    await page.getByRole('dialog').waitFor({ state: 'visible', timeout: 10000 })
    await settle(page)
    await page.getByRole('dialog').screenshot({ path: join(OUT, `${theme}-11-switch-back.png`) })
    await context.close()
  }
  // 12: stopping a read-only source.
  {
    const { context, page, picker } = await open(theme, { overview: AUGMENTED })
    await picker.getByRole('button', { name: 'Stop reading as a source' }).click()
    await page.getByRole('dialog').waitFor({ state: 'visible', timeout: 10000 })
    await settle(page)
    await page.getByRole('dialog').screenshot({ path: join(OUT, `${theme}-12-stop-source.png`) })
    await context.close()
  }
  // 13: the switch went through but the copy after it failed.
  {
    const { context, page, picker } = await open(theme, { failMove: true })
    await picker.getByRole('button', { name: 'Use instead of Crew memory' }).first().click()
    await page.getByRole('button', { name: 'Switch', exact: true }).click()
    await page.getByText(/the copy did not/).waitFor({ state: 'visible', timeout: 10000 })
    await settle(page)
    await page.getByRole('dialog').screenshot({ path: join(OUT, `${theme}-13-copy-failed.png`) })
    await context.close()
  }
  // 14-15: the picker's notices and chips, and with no provider installed.
  {
    const { context, picker } = await open(theme, { overview: NOTICES, waitFor: 'Old memory' })
    await picker.screenshot({ path: join(OUT, `${theme}-14-picker-notices.png`) })
    await context.close()
  }
  {
    const { context, picker } = await open(theme, { overview: NONE, waitFor: '' })
    await picker.screenshot({ path: join(OUT, `${theme}-15-picker-none.png`) })
    await context.close()
  }
}

await browser.close()
srv.close()
console.log('wrote', OUT)
