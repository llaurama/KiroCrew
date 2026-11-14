"""One row walk over an ``.xlsx`` workbook, for every reader that indexes or searches one.

File grep's document pass and the Knowledge Library's reader both turn a workbook
into ``("<sheet> · row <n>", row text)`` pairs. Each keeps its own outcome contract
(grep answers a refused workbook as a settled empty result, the library reader as
an error), so this module only walks: it vets the archive, opens it read-only,
and builds each row inside a character budget under a deadline.
"""

from __future__ import annotations

import os
import time
import zipfile
from dataclasses import dataclass, field
from typing import IO

from kiro_crew.zip_vet import (
    TAIL_WINDOW,
    WORKBOOK_MAX_EXPANDED_BYTES,
    WORKBOOK_MAX_MEMBERS,
    vet_zip_inventory_bytes,
)

__all__ = ["WorkbookRows", "read_workbook_rows"]


@dataclass
class WorkbookRows:
    """What one walk read: its rows, the sheets it reached, and why it stopped early."""

    rows: list[tuple[str, str]] = field(default_factory=list)
    sheets: int = 0
    #: The character budget or the deadline ended the walk before the last row.
    cut: bool = False
    #: The read broke after ``rows``; they are real, the rest was never seen.
    failure: Exception | None = None


def read_workbook_rows(
    fileobj: IO[bytes],
    *,
    deadline: float,
    max_chars: int,
    separator: str,
    stride: int,
    labels_count: bool = False,
    collapse_whitespace: bool = False,
    max_members: int = WORKBOOK_MAX_MEMBERS,
    max_expanded_bytes: int = WORKBOOK_MAX_EXPANDED_BYTES,
) -> WorkbookRows:
    """Walk the non-empty rows of the workbook in *fileobj* (seekable bytes).

    Two gates run BEFORE openpyxl sees the bytes, because it opens the container
    itself: the inventory vet bounds the declared member count, and the
    ``infolist()`` sum bounds expansion. Either refusal, an archive that is not a
    zip, a missing openpyxl or a workbook openpyxl cannot open raises; a failure
    after rows were read is returned in ``failure`` beside them.

    A row's cells join with *separator*, and the row is built inside what is left
    of *max_chars*, never joined first and cut after: openpyxl hands back one
    shared string for every cell that references it, so a wide row is N
    references until a join makes it N copies. With *labels_count* a row also
    pays for its label plus three characters, the ``": "`` and newline of a
    ``<label>: <cells>`` line. *collapse_whitespace* folds every whitespace run
    in a title or cell to one space. The deadline (a ``time.monotonic()``
    reading) is checked every *stride* rows, because the character budget cannot
    see an empty row and one small sheet can declare millions of them.
    """
    size = fileobj.seek(0, os.SEEK_END)
    fileobj.seek(max(0, size - TAIL_WINDOW))
    vet_zip_inventory_bytes(fileobj.read(TAIL_WINDOW), max_members=max_members)
    fileobj.seek(0)
    with zipfile.ZipFile(fileobj) as archive:
        if sum(info.file_size for info in archive.infolist()) > max_expanded_bytes:
            raise ValueError("workbook expands past its ceiling")
    fileobj.seek(0)
    # Imported only past both ceilings: parser setup a refusal should not pay for.
    import openpyxl  # type: ignore[import-untyped]

    book = openpyxl.load_workbook(fileobj, read_only=True, data_only=True)
    walk = WorkbookRows()
    try:
        _walk(book, walk, deadline, max_chars, separator, stride, labels_count, collapse_whitespace)
    except Exception as exc:
        walk.failure = exc
    finally:
        book.close()
    return walk


def _walk(
    book,
    walk: WorkbookRows,
    deadline: float,
    budget: int,
    separator: str,
    stride: int,
    labels_count: bool,
    collapse: bool,
) -> None:
    for sheet in book.worksheets:
        walk.sheets += 1
        if not hasattr(sheet, "iter_rows"):  # a chartsheet has no cell grid
            continue
        if hasattr(sheet, "reset_dimensions"):
            # A dimension record can lie, and the read-only reader trusts it.
            sheet.reset_dimensions()
        title = " ".join(str(sheet.title).split()) if collapse else sheet.title
        for index, row in enumerate(sheet.iter_rows(values_only=True), 1):
            if (index - 1) % stride == 0 and time.monotonic() >= deadline:
                walk.cut = True
                return
            label = f"{title} · row {index}"
            room = budget - (len(label) + 3 if labels_count else 0)
            if room <= 0:
                walk.cut = True
                return
            cells: list[str] = []
            for cell in row:
                if cell is None:
                    continue
                piece = str(cell)
                if collapse:
                    piece = " ".join(piece[: max(room, 0) + 1].split())
                    if not piece:
                        continue
                if len(piece) >= room:
                    cells.append(piece[: max(room, 0)])
                    room = 0
                    break
                cells.append(piece)
                room -= len(piece) + len(separator)
                if room <= 0:
                    break
            if not cells:
                continue
            walk.rows.append((label, separator.join(cells)))
            budget = room
            if budget <= 0:
                walk.cut = True
                return
