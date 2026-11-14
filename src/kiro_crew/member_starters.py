"""A Bot's conversation starters: short prompts its owner pins to the empty chat.

A starter is the Bot owner's own text. It is shown as a button on the Bot's empty
chat and a press sends it as the person's first message, so it is cleaned as the
Bot's display name is (control and format characters removed, whitespace collapsed),
refused when credential-shaped as its description is, and bounded in length and in
how many a Bot keeps.

The rule is *drop, never refuse*. A value that does not pass is left out and named by
its place in the list, never quoted, so nothing it held reaches a log or an API
answer; what passes is kept. A hand-edited ``config.json`` therefore never fails to
load over a starter, and a save that carries a bad starter keeps the others.
"""

from __future__ import annotations

from kiro_crew.external_text import external_text_requires_redaction

#: How many starters a Bot keeps. Four fit one row of buttons on a phone.
STARTER_MAX_COUNT = 4
#: The longest starter, in characters, once it is cleaned.
STARTER_MAX_CHARS = 120


def clean_starters(raw: object) -> tuple[list[str], list[str]]:
    """The starters in *raw* that pass, and a warning for each value left out.

    ``None`` is "no starters" and says nothing. Anything else that is not a list is
    left out whole. Each string is cleaned as a display name is; then a value that is
    not text, is empty, is too long, looks like a credential or repeats an earlier
    starter is left out, and so is every one past :data:`STARTER_MAX_COUNT`.
    """
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["starters must be a list of text; none were kept"]
    # The display name's own cleaner, imported from the facade as member_goals does.
    from kiro_crew.members import _strip_controls

    kept: list[str] = []
    warnings: list[str] = []
    for place, item in enumerate(raw, start=1):
        text = None
        if isinstance(item, str):
            text = " ".join(_strip_controls(item, keep_newlines=False).split())
        if text is None:
            why = "is not text"
        elif not text:
            why = "is empty"
        elif len(text) > STARTER_MAX_CHARS:
            why = f"is longer than {STARTER_MAX_CHARS} characters"
        elif external_text_requires_redaction(text):
            why = "looks like a credential"
        elif text in kept:
            why = "repeats an earlier starter"
        elif len(kept) >= STARTER_MAX_COUNT:
            why = f"is past the first {STARTER_MAX_COUNT}"
        else:
            kept.append(text)
            continue
        warnings.append(f"starter {place} {why} and was left out")
    return kept, warnings
