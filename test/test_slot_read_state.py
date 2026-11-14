"""The gateway's per-session read time: its store rules, the load, and the slot row field.

Every browser reports reads through ``slot_read``; ``slot_read_state`` keeps the
newest per slot in the fenced ``pinned-order`` directory and stamps it on slot
rows as ``last_read_ts``, so a closed browser or a second device can tell what
is unread from its first slot list.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from kiro_crew.dashboard import pinned_session_order as pso
from kiro_crew.dashboard import slot_read_state as srs

EARLY = "2026-11-13T10:00:00+00:00"
LATE = "2026-11-13T11:00:00+00:00"


def _loaded(epoch: str = EARLY) -> srs.ReadState:
    reads = srs.ReadState()
    reads.adopt(epoch, {})
    return reads


class TestAdvance:
    def test_a_read_only_moves_forward(self) -> None:
        reads = srs.ReadState()
        assert reads.advance("a", LATE) == LATE
        assert reads.advance("a", EARLY) == LATE
        assert reads.reads == {"a": LATE}

    def test_instants_decide_not_string_order(self) -> None:
        """A later instant written with a smaller offset sorts lower as a string."""
        reads = srs.ReadState()
        reads.advance("a", "2026-11-13T12:00:00+05:00")  # 07:00 UTC
        assert reads.advance("a", "2026-11-13T08:00:00+00:00") == "2026-11-13T08:00:00+00:00"

    def test_an_unusable_time_keeps_what_is_stored(self) -> None:
        reads = srs.ReadState()
        assert reads.advance("a", "not a time") is None
        reads.advance("a", EARLY)
        for junk in ("not a time", "", 42, None, "2" * 65):
            assert reads.advance("a", junk) == EARLY

    def test_an_unusable_key_stores_nothing(self) -> None:
        reads = srs.ReadState()
        assert reads.advance("k" * (pso.MAX_PINNED_ORDER_KEY_CHARS + 1), LATE) is None
        assert reads.reads == {}

    def test_the_cap_drops_the_oldest_reads(self, monkeypatch) -> None:
        monkeypatch.setattr(srs, "MAX_READ_STATE_KEYS", 2)
        reads = srs.ReadState()
        reads.advance("old", "2026-11-13T09:00:00+00:00")
        reads.advance("mid", EARLY)
        reads.advance("new", LATE)
        assert set(reads.reads) == {"mid", "new"}


class TestRowField:
    def test_rows_carry_nothing_until_the_file_is_read(self) -> None:
        reads = srs.ReadState()
        reads.advance("a", LATE)
        assert reads.row_read_ts("a") is None

    def test_a_slot_with_no_read_counts_as_read_up_to_the_epoch(self) -> None:
        reads = _loaded(EARLY)
        assert reads.row_read_ts("never-read") == EARLY
        reads.advance("a", LATE)
        assert reads.row_read_ts("a") == LATE

    def test_the_field_is_person_only(self) -> None:
        """Every route that strips the person's order fields strips the read time too."""
        assert "last_read_ts" in pso.PIN_ORDER_FIELDS


class TestFile:
    def test_save_and_load_round_trip_beside_the_orders(self, tmp_path) -> None:
        path = srs.store_path(tmp_path)
        assert path.parent == pso.store_path(tmp_path).parent
        srs.save(path, EARLY, {"a": LATE})
        assert srs.load_stored(path) == (EARLY, {"a": LATE})

    def test_a_missing_file_reads_as_empty(self, tmp_path) -> None:
        assert srs.load_stored(srs.store_path(tmp_path)) == (None, {})

    @pytest.mark.parametrize(
        "body",
        [b"{not json", b"[1, 2]", json.dumps({"epoch": "x", "reads": {"a": "y"}}).encode()],
    )
    def test_an_unusable_file_reads_as_empty(self, tmp_path, body: bytes) -> None:
        path = srs.store_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(body)
        assert srs.load_stored(path) == (None, {})

    def test_adopt_keeps_a_read_that_landed_before_the_load(self) -> None:
        reads = srs.ReadState()
        reads.advance("a", LATE)
        reads.advance("b", EARLY)
        assert reads.adopt(EARLY, {"a": EARLY, "b": LATE, "c": EARLY}) is True
        assert reads.reads == {"a": LATE, "b": LATE, "c": EARLY}

    def test_adopt_of_an_unchanged_file_needs_no_write(self) -> None:
        reads = srs.ReadState()
        assert reads.adopt(EARLY, {"a": LATE}) is False

    def test_a_first_load_creates_the_epoch_and_writes_it(self) -> None:
        reads = srs.ReadState()
        assert reads.adopt(None, {}) is True
        assert reads.epoch is not None and reads.row_read_ts("a") == reads.epoch


class TestLoadAndSave:
    def _state(self) -> MagicMock:
        return MagicMock()

    def test_the_load_writes_a_new_store_and_publishes_rows(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("kiro_crew.config.loader.config_dir", lambda: tmp_path)
        state = self._state()
        asyncio.run(srs.load_read_state_after_listen(state))
        epoch, stored = srs.load_stored(srs.store_path(tmp_path))
        assert epoch == srs.read_state_of(state).epoch and stored == {}
        state.push_slots_update.assert_called_once_with()

    def test_reads_after_the_load_reach_disk_in_one_write(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("kiro_crew.config.loader.config_dir", lambda: tmp_path)
        monkeypatch.setattr(srs, "SAVE_DELAY_SECONDS", 0)
        writes: list[dict[str, str]] = []
        real_save = srs.save

        def counting_save(path, epoch, reads):
            writes.append(dict(reads))
            real_save(path, epoch, reads)

        async def scenario() -> None:
            reads = _loaded(EARLY)
            monkeypatch.setattr(srs, "save", counting_save)
            reads.advance("a", EARLY)
            reads.advance("a", LATE)
            reads.advance("b", LATE)
            task = reads._save_task
            assert task is not None
            await asyncio.wait_for(task, timeout=5)

        asyncio.run(scenario())
        assert writes == [{"a": LATE, "b": LATE}]
        assert srs.load_stored(srs.store_path(tmp_path))[1] == {"a": LATE, "b": LATE}

    def test_no_write_before_the_load(self, monkeypatch) -> None:
        monkeypatch.setattr(srs, "save", MagicMock(side_effect=AssertionError("wrote early")))

        async def scenario() -> None:
            reads = srs.ReadState()
            reads.advance("a", LATE)
            assert reads._save_task is None

        asyncio.run(scenario())


def _gateway(tmp_path, monkeypatch, keys=("a", "b")):
    from chat_test_helpers import _make_state

    monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr("kiro_crew.config.loader.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    for key in keys:
        slot = state.get_or_create_slot(key)
        slot.append("user", "hello")
        slot.drain()
    return state


async def _row_reads(state) -> dict[str, str | None]:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from kiro_crew.dashboard.chat import api_chat_slots

    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/slots", api_chat_slots)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/chat/slots")
        assert resp.status == 200
        body = await resp.json()
    rows = body["slots"] if isinstance(body, dict) else body
    return {row["key"]: row.get("last_read_ts") for row in rows}


@pytest.mark.asyncio
async def test_a_read_reaches_rows_and_survives_a_restart(tmp_path, monkeypatch) -> None:
    """A device that was closed during the read still sees it on its first slot list."""
    from kiro_crew.dashboard.ws import _handle_slot_read

    first = _gateway(tmp_path, monkeypatch)
    assert await _row_reads(first) == {"a": None, "b": None}
    await srs.load_read_state_after_listen(first)
    epoch = srs.read_state_of(first).epoch
    assert await _row_reads(first) == {"a": epoch, "b": epoch}
    assert _handle_slot_read(first, "a", LATE, owner=True) is True
    assert await _row_reads(first) == {"a": LATE, "b": epoch}
    await srs.read_state_of(first).save_now()

    second = _gateway(tmp_path, monkeypatch)
    await srs.load_read_state_after_listen(second)
    assert await _row_reads(second) == {"a": LATE, "b": epoch}
