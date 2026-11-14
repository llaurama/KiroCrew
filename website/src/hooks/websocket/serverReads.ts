/** Unread from the gateway's read times, so every device agrees.
 *
 * Live frames badge only a window open when they arrive. Each slot row carries
 * `last_read_ts`, the newest read any device reported. After every slot list:
 *
 * - a badge the read covers clears (read on another device);
 * - a slot whose newest agent row is newer than the read badges (missed while closed);
 * - kept reads (`readOutbox`) count as read and are resent until confirmed.
 *
 * A row with no `last_read_ts` yet is left to the live rules. A manual
 * mark-as-unread is never cleared here.
 */
import type { QueryClient } from '@tanstack/react-query'
import { store, type AppDispatch } from '../../store'
import type { ChatSlot } from '../../types'
import { markSlotUnread, remoteSlotRead } from '../../store/dashboardSlice'
import { MANUAL_UNREAD, readCovers } from '../../store/dashboard/unread'
import { ackRead, queuedReads } from '../../lib/readOutbox'
import { emitSlotRead, newerTs, shownHere } from '../../lib/slotReadRelay'
import { appsMutingSessions, isSlotMuted } from '../sessionMute'
import { loadUnreadOnAttention } from '../unreadOnAttention'
import { isSlotOnScreen } from './attention'

/** A kept read is sent again at most this often while the gateway has not confirmed it. */
export const READ_RESEND_AFTER_MS = 10_000

const lastResent = new Map<string, number>()

/** The time of the newest row that should badge *row* when nobody saw it arrive.
 *
 *  An agent row (`last_agent_ts`: assistant text or a tool call), the same rows
 *  the live rule badges. With "only when done or waiting" on, a running turn
 *  badges nothing yet. A crewmate thread is left to its live rule, which also
 *  skips tool rows. */
export function unreadCandidateTs(row: ChatSlot, attentionOn: boolean): string | undefined {
  if (row.mode === 'member') return undefined
  if (attentionOn && row.running) return undefined
  return row.last_agent_ts || undefined
}

export interface ServerReadDeps {
  dispatch: AppDispatch
  queryClient: QueryClient
  now?: () => number
}

export function applyServerReads(rows: readonly ChatSlot[], { dispatch, queryClient, now = Date.now }: ServerReadDeps): void {
  const kept = queuedReads()
  const attentionOn = loadUnreadOnAttention()
  const mutedApps = appsMutingSessions(queryClient.getQueryData(['kirocrewConfig']))
  const at = now()
  for (const row of rows) {
    const serverRead = row.last_read_ts
    if (typeof serverRead !== 'string' || !serverRead) continue
    let keptRead: string | undefined = kept[row.key]
    if (keptRead !== undefined && readCovers(serverRead, keptRead)) {
      ackRead(row.key, serverRead)
      keptRead = undefined
    }
    if (keptRead !== undefined && at - (lastResent.get(row.key) ?? -Infinity) >= READ_RESEND_AFTER_MS) {
      lastResent.set(row.key, at)
      emitSlotRead(row.key, keptRead)
    }
    const read = newerTs(newerTs(serverRead, keptRead), shownHere(row.key)) ?? serverRead
    const { unreadSlots, unreadSince, slots } = store.getState().dashboard
    if (unreadSlots.includes(row.key)) {
      const since = unreadSince?.[row.key]
      if (since === MANUAL_UNREAD) continue
      const guard = since ?? row.last_ts
      if (guard && readCovers(read, guard)) dispatch(remoteSlotRead({ slot: row.key, readTs: read }))
      continue
    }
    const candidate = unreadCandidateTs(row, attentionOn)
    if (!candidate || readCovers(read, candidate)) continue
    if (isSlotOnScreen(row.key) || isSlotMuted(slots, row.key, mutedApps)) continue
    dispatch(markSlotUnread({ slot: row.key, ts: candidate }))
  }
}

/** Test hook: forget resend times so cases do not leak into each other. */
export function _resetServerReadsForTest(): void {
  lastResent.clear()
}
