"""What a click, type or select changed: the ``changes`` report (``browser.report_changes``).

The walker's ``glance`` reads the page cheaply before and after the action; this
module turns the two reads into one short report, so the agent can see an opened
dialog, an error message or a new page without a ``snapshot`` call. It is pure:
the reads, the settle and the gateway wiring live in ``browser_harness.ops``.

Everything in the report is page text, so each piece is untrusted: it is cut to
a bounded single line, stripped of control characters and passed through the
credential and exfiltration-link redaction every egress site shares
(``redact_via_context``). A field's value is never read (a field is named by its
label), and the address is shown without its query, fragment or credentials,
since those can carry a token.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urlsplit

from kiro_crew.terminal_safe import safe_terminal_line

#: Entries kept per list in the report; the rest are counted, not listed.
LIST_CAP = 8
#: Longest control name, and longest dialog, alert or validation text, in characters.
NAME_CAP = 60
TEXT_CAP = 160
URL_CAP = 200
#: How much of one page-written string is looked at before it is cleaned.
_INPUT_CAP = 2000
#: Most entries read from one list of a census: a hostile page cannot grow the report.
_READ_CAP = 700


def clean(value: Any, cap: int) -> str:
    """One bounded line of page text: no controls, no credentials, no exfiltration link."""
    if not isinstance(value, str):
        return ""
    # Imported here: the MCP servers' tool listing imports the ops module.
    from kiro_crew.platform.context import redact_via_context

    line = redact_via_context(safe_terminal_line(" ".join(value[:_INPUT_CAP].split())))
    return line if len(line) <= cap else line[: cap - 1] + "…"


def _strings(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw[:_READ_CAP] if isinstance(item, str)]


def census(raw: Any) -> dict[str, Any] | None:
    """The walker's ``glance`` answer in the shape :func:`diff` reads, or ``None``."""
    if not isinstance(raw, dict) or not isinstance(raw.get("url"), str):
        return None
    return {
        "url": raw["url"],
        "title": raw.get("title") if isinstance(raw.get("title"), str) else "",
        "controls": _strings(raw.get("controls")),
        "dialogs": _strings(raw.get("dialogs")),
        "alerts": _strings(raw.get("alerts")),
        "invalid": _strings(raw.get("invalid")),
        "truncated": raw.get("truncated") is True,
    }


def shown_url(url: str) -> str:
    """The page's address without credentials, query or fragment."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return clean(f"{parts.scheme}:" if parts.scheme else "", URL_CAP)
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        port = ""
    return clean(f"{parts.scheme}://{parts.hostname}{port}{parts.path}", URL_CAP)


def _added(before: list[str], after: list[str]) -> list[str]:
    """Entries of *after* not matched by one of *before*, in page order."""
    left = Counter(before)
    out: list[str] = []
    for item in after:
        if left[item] > 0:
            left[item] -= 1
        else:
            out.append(item)
    return out


def _listed(items: list[str], cap: int) -> list[str]:
    shown = [text for text in (clean(item, cap) for item in items[:LIST_CAP]) if text]
    if len(items) > LIST_CAP:
        shown.append(f"... and {len(items) - LIST_CAP} more")
    return shown


def _control(key: str) -> str:
    role, _, name = key.partition("|")
    kind, label = clean(role, NAME_CAP) or "generic", clean(name, NAME_CAP)
    return f'{kind} "{label}"' if label else kind


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """The report of what differs between two censuses; ``{"no_change_seen": True}`` if none."""
    out: dict[str, Any] = {}
    moved = False
    if before["url"] != after["url"]:
        now, was = shown_url(after["url"]), shown_url(before["url"])
        moved = now != was
        out["url"] = now if moved else f"{now} (query or fragment changed)"
    if before["title"] != after["title"] and (title := clean(after["title"], TEXT_CAP)):
        out["title"] = title
    for key, names in (
        ("dialog_opened", _added(before["dialogs"], after["dialogs"])),
        ("dialog_closed", _added(after["dialogs"], before["dialogs"])),
    ):
        if names:
            out[key] = _listed(names, NAME_CAP) or ["dialog"]
    for key, name in (("alerts", "alerts"), ("invalid", "invalid")):
        if listed := _listed(_added(before[name], after[name]), TEXT_CAP):
            out[key] = listed
    if before["truncated"] or after["truncated"]:
        out["controls"] = "not compared: the page has more controls than the report reads"
    elif moved:
        out["controls"] = "a new page: take a snapshot for its refs"
    else:
        appeared = _added(before["controls"], after["controls"])
        gone_now = _added(after["controls"], before["controls"])
        if appeared:
            out["appeared"] = [_control(k) for k in appeared[:LIST_CAP]] + _more(appeared)
        if gone_now:
            out["disappeared"] = [_control(k) for k in gone_now[:LIST_CAP]] + _more(gone_now)
    return out or {"no_change_seen": True}


def _more(items: list[str]) -> list[str]:
    return [f"... and {len(items) - LIST_CAP} more"] if len(items) > LIST_CAP else []


def dialog_report(shown: dict[str, Any]) -> dict[str, Any]:
    """The report when a JavaScript dialog holds the page: it cannot be read until answered."""
    return {
        "js_dialog": {
            "type": clean(shown.get("type"), NAME_CAP) or "alert",
            "message": clean(shown.get("message"), TEXT_CAP),
        },
        "note": "the page is held by this dialog; call `dialog` to answer it",
    }
