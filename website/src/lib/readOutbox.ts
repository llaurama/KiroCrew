/** Reads the gateway has not confirmed yet, kept across a reload.
 *
 * A read goes out over the socket with `sendIfOpen`, so one made while the
 * socket is down, or one lost when a stalled gateway's socket is killed, never
 * arrives. Every read sent is also kept here (`mc-read-outbox`: slot -> newest
 * read time) until the gateway confirms a stored read at least that new: the
 * `slot_read` echo, or a slot row's `last_read_ts`. Until then the badge rules
 * treat the slot as read up to the kept time, and `serverReads` sends it again.
 * Resending is safe because the gateway keeps only the newest read.
 */
import { safeGetItem, safeSetItem } from '../utils/safeStorage'
import { newerTs } from './slotReadRelay'
import { readCovers } from '../store/dashboard/unread'

export const READ_OUTBOX_KEY = 'mc-read-outbox'

/** Far above any real number of unconfirmed reads; past it the oldest go. */
export const READ_OUTBOX_MAX = 500

const load = (): Record<string, string> => {
  try {
    const raw = JSON.parse(safeGetItem(READ_OUTBOX_KEY) ?? '{}') as unknown
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
    const out: Record<string, string> = {}
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      if (typeof v === 'string' && Number.isFinite(Date.parse(v))) out[k] = v
    }
    return out
  } catch { return {} }
}

const store = (entries: Record<string, string>): void => {
  let kept = Object.entries(entries)
  if (kept.length > READ_OUTBOX_MAX) {
    kept = kept.sort(([, a], [, b]) => Date.parse(a) - Date.parse(b)).slice(-READ_OUTBOX_MAX)
  }
  safeSetItem(READ_OUTBOX_KEY, JSON.stringify(Object.fromEntries(kept)))
}

/** Keep a read until the gateway confirms it; an older or unparseable time changes nothing. */
export function queueRead(slot: string, readTs: string | undefined): void {
  if (!slot || !readTs || !Number.isFinite(Date.parse(readTs))) return
  const entries = load()
  const next = newerTs(entries[slot], readTs)
  if (next === entries[slot]) return
  entries[slot] = next as string
  store(entries)
}

/** Forget a slot's kept read once the gateway stores one at least as new. */
export function ackRead(slot: string, storedTs: string | undefined): void {
  const entries = load()
  const queued = entries[slot]
  if (queued === undefined || !readCovers(storedTs, queued)) return
  delete entries[slot]
  store(entries)
}

/** Every kept read: slot -> time. */
export function queuedReads(): Record<string, string> {
  return load()
}
