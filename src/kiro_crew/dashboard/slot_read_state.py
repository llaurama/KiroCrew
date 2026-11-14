"""The person's read time for each session, kept on the gateway.

Each ``slot_read`` frame carries the newest row time the reader had shown. This
store keeps the newest per slot and stamps it on slot rows as ``last_read_ts``,
so a closed browser or a second device sees what is unread from its first list.

The file ``chat_read_state.json`` sits in the fenced ``pinned-order`` directory
as ``{"epoch": <ts>, "reads": {<slot>: <ts>}}``. A slot with no read counts as
read up to ``epoch``, the store's creation, so an upgrade badges nothing old.
A read only moves forward, so resending one is safe. Rows carry ``None`` until
the file has been read.
"""

from __future__ import annotations

import asyncio
import json
import logging
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kiro_crew.atomic_write import atomic_write
from kiro_crew.dashboard import pinned_session_order as pso
from kiro_crew.history import latest_transcript_ts, transcript_sort_key
from kiro_crew.owner_only_files import mkdirs_owner_only

logger = logging.getLogger(__name__)

READ_STATE_FILE = "chat_read_state.json"

#: The most slots the store keeps. Past it the oldest reads are dropped, which
#: only costs those slots their saved read; the live slot cap is far below it.
MAX_READ_STATE_KEYS = pso.MAX_PINNED_ORDER_KEYS

#: A stored time is a transcript timestamp; this bounds a hand-edited file.
MAX_READ_TS_CHARS = 64

#: Byte ceiling for the file: every key and time at its bound, JSON-escaped.
MAX_READ_STATE_FILE_BYTES = (
    MAX_READ_STATE_KEYS * (pso.MAX_PINNED_ORDER_KEY_CHARS * 12 + MAX_READ_TS_CHARS + 8) + 256
)

#: How long reads gather before one write.
SAVE_DELAY_SECONDS = 1.0


def store_path(home: Path) -> Path:
    """The read-state file under *home* (the config dir)."""
    return home / pso.PINNED_ORDER_DIR / READ_STATE_FILE


def _valid_ts(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_READ_TS_CHARS
        and latest_transcript_ts(value) is not None
    )


def _valid_key(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= pso.MAX_PINNED_ORDER_KEY_CHARS


def load_stored(path: Path) -> tuple[str | None, dict[str, str]]:
    """The stored ``(epoch, reads)``; ``(None, {})`` when no usable file is there.

    A missing, malformed or foreign file reads as empty with a warning: losing
    read times costs badges, never startup. Only ``FileNotFoundError`` counts as
    missing; another ``OSError`` is raised, so an unreadable home is never
    written over as if it were empty.
    """
    try:
        data = pso.read_store_file(path, MAX_READ_STATE_FILE_BYTES)
    except FileNotFoundError:
        return None, {}
    if data is None:
        return None, {}
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        logger.warning("%s has malformed content: %s", path.name, exc)
        return None, {}
    if not isinstance(raw, dict):
        logger.warning("%s is not an object; ignoring it", path.name)
        return None, {}
    epoch = raw.get("epoch") if _valid_ts(raw.get("epoch")) else None
    reads_raw = raw.get("reads")
    reads: dict[str, str] = {}
    if isinstance(reads_raw, dict):
        reads = {k: v for k, v in reads_raw.items() if _valid_key(k) and _valid_ts(v)}
    return epoch, _capped(reads)


def save(path: Path, epoch: str, reads: dict[str, str]) -> None:
    """Replace the stored read state in one atomic, owner-only write."""
    mkdirs_owner_only(path.parent)
    payload = json.dumps({"epoch": epoch, "reads": reads})
    atomic_write(path, payload, fsync=True, restrict_to_owner=True)


def _capped(reads: dict[str, str]) -> dict[str, str]:
    if len(reads) <= MAX_READ_STATE_KEYS:
        return reads
    newest = sorted(reads.items(), key=lambda item: transcript_sort_key(item[1]))
    return dict(newest[-MAX_READ_STATE_KEYS:])


def _newer(a: str | None, b: str) -> str:
    """The later of *a* and *b* as instants; *a* wins a tie. *b* is known to parse."""
    if a is None:
        return b
    return b if transcript_sort_key(b) > transcript_sort_key(a) else a


class ReadState:
    """The read times in memory, and the coalesced write that keeps them on disk."""

    def __init__(self) -> None:
        self.reads: dict[str, str] = {}
        self.epoch: str | None = None
        self.loaded = False
        self._save_task: asyncio.Task[None] | None = None

    def advance(self, slot: str, read_ts: object) -> str | None:
        """Keep *read_ts* for *slot* when it is newer; return the stored time.

        Returns ``None`` when *slot* is not a usable key, or nothing is stored
        for it and *read_ts* does not parse.
        """
        if not _valid_key(slot):
            return None
        stored = self.reads.get(slot)
        if not _valid_ts(read_ts):
            return stored
        assert isinstance(read_ts, str)
        nxt = _newer(stored, read_ts)
        if nxt != stored:
            self.reads[slot] = nxt
            if len(self.reads) > MAX_READ_STATE_KEYS:
                self.reads = _capped(self.reads)
            self._schedule_save()
        return nxt

    def row_read_ts(self, slot: str) -> str | None:
        """What a slot row carries: its read, else the epoch; ``None`` until loaded."""
        if not self.loaded:
            return None
        return self.reads.get(slot) or self.epoch

    def adopt(self, epoch: str | None, stored: dict[str, str]) -> bool:
        """Merge the file's contents under reads that landed first; ``True`` when it must be written."""
        dirty = epoch is None
        self.epoch = epoch or datetime.now(timezone.utc).isoformat()
        merged = dict(stored)
        for slot, ts in self.reads.items():
            nxt = _newer(merged.get(slot), ts)
            if nxt != merged.get(slot):
                merged[slot] = nxt
                dirty = True
        self.reads = _capped(merged)
        self.loaded = True
        return dirty

    def _schedule_save(self) -> None:
        if not self.loaded or (self._save_task is not None and not self._save_task.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._save_task = loop.create_task(self._save_later())

    async def _save_later(self) -> None:
        await asyncio.sleep(SAVE_DELAY_SECONDS)
        await self.save_now()

    async def save_now(self) -> None:
        """Write the current state; a failure is logged and retried on the next read."""
        from kiro_crew.config.loader import config_dir

        if self.epoch is None:
            return
        try:
            await asyncio.to_thread(save, store_path(config_dir()), self.epoch, dict(self.reads))
        except OSError:
            logger.warning("could not write %s", READ_STATE_FILE, exc_info=True)


_STATES: "weakref.WeakKeyDictionary[Any, ReadState]" = weakref.WeakKeyDictionary()


def read_state_of(state: Any) -> ReadState:
    """The gateway's read state; one per dashboard state."""
    found = _STATES.get(state)
    if found is None:
        found = _STATES[state] = ReadState()
    return found


async def load_read_state_after_listen(state: Any) -> None:
    """Read the stored read times once the gateway serves, then publish them on the rows."""
    from kiro_crew.config.loader import config_dir

    reads = read_state_of(state)
    if reads.loaded:
        return
    try:
        epoch, stored = await asyncio.to_thread(load_stored, store_path(config_dir()))
    except OSError:
        logger.warning("could not read %s", READ_STATE_FILE, exc_info=True)
        return
    if reads.adopt(epoch, stored):
        await reads.save_now()
    state.push_slots_update()
