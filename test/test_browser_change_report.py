"""``browser.report_changes``: click, type and select_option say what the page did.

The report is read from the walker's ``glance`` before and after the action, and
folded by ``browser_harness.change_report``. Pinned here:

* with the setting off (the default) no extra helper is called and no result key is
  added, so every existing caller sees the result it saw before;
* with it on, the result carries ``changes``: the new address (without its query),
  a changed title, dialogs that opened or closed, new alerts and validation
  messages, and the controls that appeared or went away;
* a JavaScript dialog the action opened is reported without reading the page, which
  it would hold;
* a page that cannot be read leaves the action's own result, with a ``warning``;
* every piece of page text is one bounded, redacted line.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from kiro_crew.browser_harness import change_report, ops
from kiro_crew.browser_harness.supervisor import HelperError
from kiro_crew.config import KiroCrewConfig
from kiro_crew.dashboard.handlers import browser_ops
from kiro_crew.mcp_tools import browser as browser_tool
from kiro_crew.testing.wait import finished

#: Credential-shaped, built here so no scanner reads the source as a real one.
FAKE_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
_METHOD_RE = re.compile(r"\.(\w+)\((.*)\)$", re.DOTALL)


def census(**over: Any) -> dict[str, Any]:
    """One ``glance`` answer: a page with a button, a field and nothing else."""
    base: dict[str, Any] = {
        "url": "https://shop.example/cart?session=abc123",
        "title": "Cart",
        "controls": ["button|Pay", "textbox|Coupon"],
        "dialogs": [],
        "alerts": [],
        "invalid": [],
        "truncated": False,
    }
    base.update(over)
    return base


class Page:
    """A scripted page and supervisor: ``glances`` is answered in order, then repeats."""

    def __init__(self, tmp_path: Path, glances: list[Any]) -> None:
        self.calls: list[str] = []
        self.glances = list(glances)
        self.dialog: Any = None
        self.helpers: dict[str, Callable[..., Any]] = {}
        self.clock = 1000.0
        self.tmp = tmp_path

    async def call(self, helper: str, **kwargs: Any) -> Any:
        if helper in ("closed_secret_count", "frame_owner_count"):
            return 0
        if helper == "current_tab":
            return {"url": "https://shop.example/cart"}
        self.calls.append(helper)
        if helper in ("js", "js_isolated"):
            method = _METHOD_RE.search(kwargs["expression"][len(ops.WALKER_SOURCE) :])
            assert method
            name = method.group(1)
            self.calls[-1] = f"page:{name}"
            if name == "glance":
                nxt = self.glances.pop(0) if len(self.glances) > 1 else self.glances[0]
                if isinstance(nxt, Exception):
                    raise nxt
                return nxt
            return {
                "ok": True,
                "x": 10.0,
                "y": 20.0,
                "w": 5.0,
                "h": 5.0,
                "tag": "button",
                "focused": True,
                "selected": ["a"],
            }
        if helper == "page_info":
            return {"w": 800, "h": 600, "dialog": self.dialog}
        if helper in self.helpers:
            return self.helpers[helper](**kwargs)
        return None

    async def sleep(self, seconds: float) -> None:
        self.clock += seconds

    def ctx(self, *, report: bool = True) -> ops.OpContext:
        extra: dict[str, Any] = {"report_changes": True} if report else {}
        return ops.OpContext(
            helper=self.call,
            sleep=self.sleep,
            now=lambda: self.clock,
            screenshot_dir=self.tmp / "shots",
            **extra,
        )


def run(op: str, args: dict[str, Any], page: Page, *, report: bool = True) -> dict[str, Any]:
    return asyncio.run(ops.run_op(op, args, page.ctx(report=report)))


def click(page: Page, **kw: Any) -> dict[str, Any]:
    return run("click", {"ref": "e1"}, page, **kw)


# --- off by default --------------------------------------------------------------


def test_off_the_result_and_the_helper_calls_are_what_they_were(tmp_path: Path) -> None:
    page = Page(tmp_path, [census(), census(title="Paid")])
    out = click(page, report=False)
    assert out == {"ok": True, "x": 10.0, "y": 20.0, "button": "left", "clicks": 1, "ref": "e1"}
    assert "page:glance" not in page.calls and "page_info" not in page.calls


def test_the_setting_defaults_off_on_the_context() -> None:
    assert ops.OpContext(helper=lambda *a, **k: None).report_changes is False


# --- what the report says ----------------------------------------------------------


def test_no_change_seen_is_said_so(tmp_path: Path) -> None:
    out = click(Page(tmp_path, [census()]))
    assert out["changes"] == {"no_change_seen": True}


def test_a_new_page_is_named_without_its_query_and_its_controls_are_left_to_a_snapshot(
    tmp_path: Path,
) -> None:
    after = census(
        url="https://user:pw@shop.example:8443/thanks?token=abc#top",
        title="Thanks",
        controls=["link|Home"],
    )
    out = click(Page(tmp_path, [census(), after]))
    assert out["changes"] == {
        "url": "https://shop.example:8443/thanks",
        "title": "Thanks",
        "controls": "a new page: take a snapshot for its refs",
    }
    assert "pw" not in json.dumps(out) and "abc" not in json.dumps(out)


def test_a_query_only_change_is_said_to_be_one(tmp_path: Path) -> None:
    out = click(Page(tmp_path, [census(), census(url="https://shop.example/cart?page=2")]))
    assert out["changes"] == {"url": "https://shop.example/cart (query or fragment changed)"}


def test_dialog_alert_validation_and_controls_that_changed(tmp_path: Path) -> None:
    after = census(
        controls=["button|Pay", "button|Confirm", "textbox|Coupon", "link|Help"],
        dialogs=["Confirm payment"],
        alerts=["Card declined"],
        invalid=["Coupon: Enter a valid code"],
    )
    before = census(controls=["button|Pay", "textbox|Coupon", "button|Back"])
    out = click(Page(tmp_path, [before, after]))
    assert out["changes"] == {
        "dialog_opened": ["Confirm payment"],
        "alerts": ["Card declined"],
        "invalid": ["Coupon: Enter a valid code"],
        "appeared": ['button "Confirm"', 'link "Help"'],
        "disappeared": ['button "Back"'],
    }


def test_a_dialog_that_closed_is_reported(tmp_path: Path) -> None:
    out = click(Page(tmp_path, [census(dialogs=["Confirm"]), census()]))
    assert out["changes"] == {"dialog_closed": ["Confirm"]}


def test_an_alert_or_message_already_there_is_not_new(tmp_path: Path) -> None:
    same = census(alerts=["Saved"], invalid=["Name: Required"])
    assert click(Page(tmp_path, [same, same]))["changes"] == {"no_change_seen": True}


def test_a_control_with_no_name_is_listed_by_its_role(tmp_path: Path) -> None:
    out = click(
        Page(tmp_path, [census(), census(controls=["button|Pay", "textbox|Coupon", "link|"])])
    )
    assert out["changes"] == {"appeared": ["link"]}


def test_a_page_with_more_controls_than_the_walker_reads_is_not_compared(tmp_path: Path) -> None:
    out = click(Page(tmp_path, [census(), census(controls=["button|New"], truncated=True)]))
    assert out["changes"] == {
        "controls": "not compared: the page has more controls than the report reads"
    }


def test_type_and_select_option_carry_the_report_and_other_ops_do_not(tmp_path: Path) -> None:
    changed = [census(), census(alerts=["Looks good"])]
    typed = run("type", {"ref": "e2", "text": "SAVE10"}, Page(tmp_path, changed))
    assert typed["changes"] == {"alerts": ["Looks good"]}
    picked = run("select_option", {"ref": "e3", "value": "a"}, Page(tmp_path, changed))
    assert picked["changes"] == {"alerts": ["Looks good"]}
    hovered = Page(tmp_path, changed)
    assert "changes" not in run("hover", {"ref": "e1"}, hovered)
    assert "page:glance" not in hovered.calls


def test_a_refused_action_is_left_alone(tmp_path: Path) -> None:
    out = run("click", {"ref": "nope"}, Page(tmp_path, [census()]))
    assert out["ok"] is False and "changes" not in out and "warning" not in out


# --- a JavaScript dialog --------------------------------------------------------


def test_a_dialog_the_click_opened_is_reported_and_the_page_is_not_read_again(
    tmp_path: Path,
) -> None:
    page = Page(tmp_path, [census()])
    page.helpers["click"] = lambda **_kw: setattr(
        page, "dialog", {"type": "confirm", "message": "Delete?"}
    )
    out = click(page)
    assert out["ok"] is True
    assert out["changes"] == {
        "js_dialog": {"type": "confirm", "message": "Delete?"},
        "note": "the page is held by this dialog; call `dialog` to answer it",
    }
    assert page.calls.count("page:glance") == 1  # only the read before the click


def test_a_dialog_already_open_keeps_the_ops_own_outcome(tmp_path: Path) -> None:
    page = Page(tmp_path, [census()])
    page.dialog = {"type": "alert", "message": "Hi"}

    def stuck(**_kw: Any) -> None:
        raise HelperError("timed out")

    page.helpers["click"] = stuck
    out = click(page)
    assert out["code"] == "dialog_open"
    assert "page:glance" not in page.calls


# --- a page that cannot be read ---------------------------------------------------


def test_a_page_unreadable_after_the_action_keeps_the_action_and_says_so(tmp_path: Path) -> None:
    out = click(Page(tmp_path, [census(), HelperError("context gone")]))
    assert out["ok"] is True and out["ref"] == "e1"
    assert "changes" not in out
    assert out["warning"] == "`changes` left out: the page could not be read around the action"


def test_a_navigation_that_swallows_the_first_read_is_read_again(tmp_path: Path) -> None:
    after = census(url="https://shop.example/done", title="Done", controls=[])
    out = click(Page(tmp_path, [census(), HelperError("navigating"), after]))
    assert out["changes"]["url"] == "https://shop.example/done"


def test_a_page_unreadable_before_the_action_still_runs_the_action(tmp_path: Path) -> None:
    out = click(Page(tmp_path, [HelperError("busy")]))
    assert out["ok"] is True and "warning" in out and "changes" not in out


def test_a_malformed_answer_is_dropped_not_trusted(tmp_path: Path) -> None:
    out = click(Page(tmp_path, [census(), "not a census"]))
    assert out["ok"] is True and "changes" not in out and "warning" in out


class Stalling(Page):
    """A page whose ``page_info`` never answers on one side of the click."""

    def __init__(self, tmp_path: Path, *, after: bool) -> None:
        super().__init__(tmp_path, [census(), census(title="Paid")])
        self.stalled = not after
        self.helpers["click"] = self._clicked

    def _clicked(self, **_kw: Any) -> None:
        self.stalled = not self.stalled

    async def call(self, helper: str, **kwargs: Any) -> Any:
        if helper == "page_info" and self.stalled:
            await asyncio.Event().wait()  # never set: only the report's bound ends it
        return await super().call(helper, **kwargs)


@pytest.mark.parametrize("bound", ["CHANGE_BEFORE_S", "CHANGE_AFTER_S"])
def test_a_page_that_stalls_the_report_keeps_the_action_inside_its_bound(
    bound: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reads around the action are bounded as a whole, not only the walker call:
    the route's budget counts on it, so a clicked page that stalls never turns the
    click into a timeout the agent would answer by clicking again."""
    # The stalled read never answers, so the bound always expires: its value only
    # sets how long the test takes.
    monkeypatch.setattr(ops, bound, 0.05, raising=False)
    page = Stalling(tmp_path, after=bound == "CHANGE_AFTER_S")
    out = asyncio.run(finished(ops.run_op("click", {"ref": "e1"}, page.ctx()), "the click"))
    assert out["ok"] is True and out["ref"] == "e1" and "changes" not in out
    assert out["warning"] == "`changes` left out: the page could not be read around the action"


def test_the_ops_own_warning_is_kept_beside_the_reports() -> None:
    result: dict[str, Any] = {"ok": True, "warning": "`clicks` ignored"}
    ops._left_out(result)
    assert result["warning"] == (
        "`clicks` ignored; `changes` left out: the page could not be read around the action"
    )


def test_the_route_budget_counts_the_report_only_while_it_is_on() -> None:
    from kiro_crew.browser_harness import budgets

    on = ops.OpContext(helper=lambda *a, **k: None, report_changes=True)
    off = ops.OpContext(helper=lambda *a, **k: None)
    plain = budgets.OP_TIMEOUT_S + budgets.DECISION_HEADROOM_S
    assert browser_ops._op_budget("click", off) == plain
    assert browser_ops._op_budget("hover", on) == plain
    assert browser_ops._op_budget("navigate", on) == budgets.SLOW_OP_TIMEOUT_S + (
        budgets.DECISION_HEADROOM_S
    )
    for op in ops.REPORT_OPS:
        assert browser_ops._op_budget(op, on) == plain + budgets.CHANGE_REPORT_S
    assert budgets.CHANGE_REPORT_S == budgets.CHANGE_BEFORE_S + budgets.CHANGE_AFTER_S
    # The tool still waits longer than the gateway's worst case for such an op.
    worst = (
        budgets.ENSURE_TIMEOUT_S
        + budgets.LOCK_WAIT_S
        + budgets.CREDENTIAL_WORST_S
        + budgets.OP_TIMEOUT_S
        + browser_ops._op_budget("click", on)
    )
    assert browser_tool.FAST_COMMAND_TIMEOUT_SECS > worst


# --- page text is untrusted -------------------------------------------------------


def test_page_text_is_one_bounded_redacted_line(tmp_path: Path) -> None:
    hostile = "Oops\n\x1b[31mred\x1b[0m\x07 " + "x" * 5000 + " " + FAKE_KEY
    after = census(alerts=[hostile], dialogs=["\x1b]0;owned\x07Title"], title="T\r\nitle")
    out = click(Page(tmp_path, [census(), after]))["changes"]
    (alert,) = out["alerts"]
    assert "\n" not in alert and "\x1b" not in alert and "\x07" not in alert
    assert len(alert) <= change_report.TEXT_CAP
    assert out["dialog_opened"] == ["Title"]
    assert out["title"] == "T itle"


def test_a_credential_in_page_text_is_redacted() -> None:
    text = change_report.clean(f"token {FAKE_KEY} issued", change_report.TEXT_CAP)
    assert FAKE_KEY not in text


def test_each_list_is_capped_and_the_rest_counted(tmp_path: Path) -> None:
    many = [f"button|B{i}" for i in range(20)]
    out = click(Page(tmp_path, [census(controls=[]), census(controls=many)]))["changes"]
    assert len(out["appeared"]) == change_report.LIST_CAP + 1
    assert out["appeared"][-1] == "... and 12 more"
    assert out["appeared"][0] == 'button "B0"'


def test_a_name_is_cut_at_its_cap() -> None:
    assert len(change_report.clean("n" * 500, change_report.NAME_CAP)) == change_report.NAME_CAP


def test_a_hostile_census_cannot_grow_the_report() -> None:
    raw = census(controls=[f"button|{i}" for i in range(5000)], alerts=["a"] * 5000)
    read = change_report.census(raw)
    assert read is not None and len(read["controls"]) <= 700 and len(read["alerts"]) <= 700


# --- the walker ----------------------------------------------------------------


def test_glance_is_a_walker_method_read_in_the_isolated_world() -> None:
    assert "glance: function" in ops.WALKER_SOURCE
    assert "glance" not in ops.PAGE_WORLD_METHODS


# --- the setting and the tool text ------------------------------------------------


@pytest.mark.parametrize("flag", [True, False])
def test_the_gateway_reads_the_setting_for_each_op(
    flag: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = KiroCrewConfig()
    cfg.browser.report_changes = flag
    monkeypatch.setattr(KiroCrewConfig, "load", classmethod(lambda _cls: cfg))
    monkeypatch.setattr(browser_ops, "config_dir", lambda: tmp_path)
    assert browser_ops.build_context("s", MagicMock(), MagicMock()).report_changes is flag


def test_an_unreadable_setting_leaves_the_report_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_cls: Any) -> Any:
        raise OSError("config unreadable")

    monkeypatch.setattr(KiroCrewConfig, "load", classmethod(broken))
    monkeypatch.setattr(browser_ops, "config_dir", lambda: tmp_path)
    assert browser_ops.build_context("s", MagicMock(), MagicMock()).report_changes is False


def test_the_tool_text_carries_the_report_and_redacts_it_again() -> None:
    result = {
        "ok": True,
        "ref": "e1",
        "changes": {"alerts": [f"key {FAKE_KEY}"], "url": "https://shop.example/done"},
    }
    text = browser_tool._result_text("click", result)
    assert "https://shop.example/done" in text and "alerts" in text
    assert FAKE_KEY not in text
