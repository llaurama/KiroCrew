/**
 * Unread from the gateway's read times (`last_read_ts` on each slot row).
 *
 * The live rules badge a session only in a window that was open when its rows
 * arrived. These specs pin what every slot list now does on top of them: badge
 * what arrived while no window was open, clear what another device read, and
 * hold a read the gateway has not confirmed yet so a laggy gateway cannot bring
 * the badge back.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { QueryClient } from '@tanstack/react-query'
import { store } from '../../store'
import { sseSlots, markSlotRead, markSlotUnread } from '../../store/dashboardSlice'
import { setActiveSlot } from '../../store/chatSlice'
import { MANUAL_UNREAD, __resetSharedUnreadQueueForTests } from '../../store/dashboard/unread'
import type { ChatSlot } from '../../types'
import { applyServerReads, READ_RESEND_AFTER_MS, _resetServerReadsForTest } from './serverReads'
import { bindSlotReadSender, noteShownHere, _resetSlotReadRelayForTest } from '../../lib/slotReadRelay'
import { queueRead, queuedReads } from '../../lib/readOutbox'
import { UNREAD_ON_ATTENTION_KEY } from '../unreadOnAttention'

const READ = '2026-11-13T10:00:00+00:00'
const BEFORE = '2026-11-13T09:00:00+00:00'
const AFTER = '2026-11-13T11:00:00+00:00'

const row = (key: string, extra: Partial<ChatSlot> = {}): ChatSlot => ({
  key, title: key, messages: 1, running: false, last_read_ts: READ, last_agent_ts: AFTER, last_ts: AFTER, ...extra,
} as ChatSlot)

const queryClient = new QueryClient()
const sent: Array<[string, string | undefined]> = []

const apply = (rows: ChatSlot[], now = 1_000_000) => {
  store.dispatch(sseSlots(rows))
  applyServerReads(store.getState().dashboard.slots, { dispatch: store.dispatch, queryClient, now: () => now })
}
const unread = () => store.getState().dashboard.unreadSlots

describe('applyServerReads', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    __resetSharedUnreadQueueForTests()
    _resetSlotReadRelayForTest()
    _resetServerReadsForTest()
    sent.length = 0
    bindSlotReadSender((slot, ts) => { sent.push([slot, ts]) })
    queryClient.clear()
    store.dispatch(setActiveSlot(null))
    for (const key of [...store.getState().dashboard.unreadSlots]) store.dispatch(markSlotRead(key))
  })
  afterEach(() => {
    store.dispatch(sseSlots([]))
    _resetSlotReadRelayForTest()
  })

  it('badges a session whose agent wrote after the last read, with no live frame', () => {
    apply([row('missed')])
    expect(unread()).toContain('missed')
    expect(store.getState().dashboard.unreadSince?.missed).toBe(AFTER)
  })

  it('leaves a session alone when the read covers its newest agent row', () => {
    apply([row('seen', { last_agent_ts: BEFORE })])
    expect(unread()).not.toContain('seen')
  })

  it('does nothing while the gateway has not read its store', () => {
    apply([row('unknown', { last_read_ts: null })])
    expect(unread()).not.toContain('unknown')
  })

  it('skips the slot on screen, a muted slot, a crewmate thread, and a running turn under the attention setting', () => {
    store.dispatch(setActiveSlot('open'))
    localStorage.setItem(UNREAD_ON_ATTENTION_KEY, '1')
    apply([
      row('open'),
      row('muted', { muted: true }),
      row('member-mate', { mode: 'member' }),
      row('running', { running: true }),
      row('done'),
    ])
    expect(unread()).toEqual(['done'])
  })

  it('clears a badge a read on another device covers', () => {
    apply([row('elsewhere', { last_read_ts: BEFORE })])
    expect(unread()).toContain('elsewhere')
    apply([row('elsewhere', { last_read_ts: AFTER })])
    expect(unread()).not.toContain('elsewhere')
  })

  it('keeps a badge for a message newer than the read', () => {
    apply([row('newer', { last_read_ts: BEFORE })])
    store.dispatch(markSlotUnread({ slot: 'newer', ts: '2026-11-13T12:00:00+00:00' }))
    apply([row('newer', { last_read_ts: AFTER })])
    expect(unread()).toContain('newer')
  })

  it('never clears a manual mark-as-unread', () => {
    apply([row('manual', { last_agent_ts: BEFORE })])
    store.dispatch(markSlotUnread('manual'))
    expect(store.getState().dashboard.unreadSince?.manual).toBe(MANUAL_UNREAD)
    apply([row('manual', { last_read_ts: AFTER, last_agent_ts: BEFORE })])
    expect(unread()).toContain('manual')
  })

  it('counts a row this window showed on screen as read', () => {
    noteShownHere('watched', AFTER)
    apply([row('watched')])
    expect(unread()).not.toContain('watched')
  })

  describe('a read the gateway has not confirmed', () => {
    it('holds the badge off and is sent again, at most once per resend window', () => {
      queueRead('laggy', AFTER)
      apply([row('laggy')], 1_000_000)
      expect(unread()).not.toContain('laggy')
      expect(sent).toEqual([['laggy', AFTER]])
      apply([row('laggy', { title: 'again' })], 1_000_000 + READ_RESEND_AFTER_MS - 1)
      expect(sent).toHaveLength(1)
      _resetSlotReadRelayForTest()
      bindSlotReadSender((slot, ts) => { sent.push([slot, ts]) })
      apply([row('laggy', { title: 'later' })], 1_000_000 + READ_RESEND_AFTER_MS)
      expect(sent).toEqual([['laggy', AFTER], ['laggy', AFTER]])
    })

    it('is forgotten once a slot row carries a read at least as new', () => {
      queueRead('confirmed', READ)
      apply([row('confirmed', { last_read_ts: READ, last_agent_ts: BEFORE })])
      expect(queuedReads()).toEqual({})
      expect(sent).toEqual([])
    })
  })
})
