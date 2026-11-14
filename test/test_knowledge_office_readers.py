"""Slide decks (.pptx) and workbooks (.xlsx) join the Knowledge Library.

``FileReader`` reads both through dependencies the package already declares:
``doc_parser`` (stdlib ``zipfile`` plus the hardened XML parser) for decks and
``openpyxl`` for workbooks. A file that cannot be parsed returns the same
``format: 'error'`` sentinel a bad ``.docx`` does, which ``ingest_file`` raises
on, so the surrounding scan records the failure and carries on.
"""

from __future__ import annotations

import ast
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from kiro_crew.knowledge import readers
from kiro_crew.knowledge.chunker import HeadingAwareChunker
from kiro_crew.knowledge.readers import FileReader
from kiro_crew.testing.wait import default_timeout

_NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_SLIDE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"


def _slide_xml(*lines: str) -> str:
    runs = "".join(f"<a:p><a:r><a:t>{line}</a:t></a:r></a:p>" for line in lines)
    return (
        f'<p:sld xmlns:p="{_NS_P}" xmlns:a="{_NS_A}"><p:cSld><p:spTree>'
        f"<p:sp><p:txBody>{runs}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )


def _deck(
    path: Path, slides: list[tuple[str, ...]], *, slide_xml: dict[int, str] | None = None
) -> Path:
    """A minimal deck: one part per slide, listed in ``presentation.xml`` order."""
    ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{i}"/>' for i in range(1, len(slides) + 1))
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="{_SLIDE_REL}" Target="slides/slide{i}.xml"/>'
        for i in range(1, len(slides) + 1)
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{_NS_P}" xmlns:r="{_NS_R}"><p:sldIdLst>{ids}</p:sldIdLst>'
            "</p:presentation>",
        )
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships xmlns="{_NS_REL}">{rels}</Relationships>',
        )
        for i, lines in enumerate(slides, 1):
            body = (slide_xml or {}).get(i) or _slide_xml(*lines)
            zf.writestr(f"ppt/slides/slide{i}.xml", body)
    return path


def _workbook(path: Path, sheets: dict[str, list[list[object]]]) -> Path:
    from openpyxl import Workbook

    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    book.save(path)
    return path


def test_both_formats_are_in_the_allowlist_and_have_a_reader():
    assert {".pptx", ".xlsx"} <= FileReader.SUPPORTED
    assert {".pptx", ".xlsx"} <= set(FileReader._DISPATCH)


def test_deck_text_is_read_slide_by_slide_in_deck_order(tmp_path):
    deck = _deck(tmp_path / "plan.pptx", [("Kickoff", "Goals for Q3"), ("Budget", "Ten units")])
    text, meta = FileReader().read(str(deck))
    assert text == "## Slide 1\nKickoff\nGoals for Q3\n\n## Slide 2\nBudget\nTen units"
    assert meta["format"] == "pptx" and meta["slide_count"] == 2


def test_deck_text_splits_into_one_chunk_per_slide(tmp_path):
    deck = _deck(tmp_path / "plan.pptx", [("Kickoff",), ("Budget",)])
    text, _meta = FileReader().read(str(deck))
    chunks = HeadingAwareChunker().chunk_slides(text)
    assert [c["content"] for c in chunks] == ["Kickoff", "Budget"]
    assert [c["section_title"] for c in chunks] == ["Slide 1", "Slide 2"]


def test_deck_with_no_text_is_read_as_empty_text_not_an_error(tmp_path):
    deck = _deck(tmp_path / "images.pptx", [()], slide_xml={1: _slide_xml()})
    text, meta = FileReader().read(str(deck))
    assert text == "" and meta["format"] == "pptx" and meta["slide_count"] == 0


@pytest.mark.parametrize("payload", [b"not an archive", b"PK\x03\x04 truncated"])
def test_a_deck_that_is_not_an_archive_is_an_error_result(tmp_path, payload):
    broken = tmp_path / "broken.pptx"
    broken.write_bytes(payload)
    text, meta = FileReader().read(str(broken))
    assert text.startswith("Error reading file:") and meta["format"] == "error"


def test_extract_slides_keeps_its_empty_list_for_an_unreadable_deck_by_default(tmp_path):
    from kiro_crew.doc_parser import extract_slides

    broken = tmp_path / "broken.pptx"
    broken.write_bytes(b"not an archive")
    assert extract_slides(str(broken)) == []
    with pytest.raises(ValueError):
        extract_slides(str(broken), strict=True)


def test_a_deck_with_a_corrupt_slide_is_an_error_result(tmp_path):
    deck = _deck(tmp_path / "bad.pptx", [("fine",), ("x",)], slide_xml={2: "<p:sld><unclosed>"})
    text, meta = FileReader().read(str(deck))
    assert text.startswith("Error reading file:") and meta["format"] == "error"


_RUNS_SLIDE = (
    f'<p:sld xmlns:p="{_NS_P}" xmlns:a="{_NS_A}"><p:cSld><p:spTree><p:sp><p:txBody>'
    '<a:p><a:r><a:rPr b="1"/><a:t>Micro</a:t></a:r><a:r><a:t>services</a:t></a:r>'
    "<a:r><a:t> at scale</a:t></a:r></a:p>"
    "<a:p><a:r><a:t>Line one</a:t></a:r><a:br/><a:r><a:t>line two</a:t></a:r></a:p>"
    "<a:p><a:endParaRPr/></a:p>"
    "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
)


def test_the_runs_of_one_paragraph_join_into_the_words_the_slide_shows(tmp_path):
    deck = _deck(tmp_path / "runs.pptx", [("unused",)], slide_xml={1: _RUNS_SLIDE})
    text, meta = FileReader().read(str(deck))
    assert text == "## Slide 1\nMicroservices at scale\nLine one\nline two"
    assert meta["slide_count"] == 1


def test_the_shared_slide_walk_keeps_a_word_split_across_runs_whole(tmp_path):
    from kiro_crew.doc_parser import extract_slides

    deck = _deck(tmp_path / "runs.pptx", [("unused",)], slide_xml={1: _RUNS_SLIDE})
    assert extract_slides(str(deck)) == [(1, "Microservices at scale\nLine one\nline two")]


def test_deeply_nested_paragraphs_are_read_in_one_pass_with_each_text_once(tmp_path):
    """Crafted nesting must cost work in proportion to the slide, not its square.

    A walk that re-reads every paragraph's subtree keeps each ancestor's joined
    descendant text as a line of its own, so the result alone grows with the square
    of the depth. Deeper than the interpreter's recursion limit, too.
    """
    from kiro_crew.doc_parser import extract_slides

    depth = 3000
    words = [f"w{n}." for n in range(depth)]
    nested = "".join(f"<a:p><a:r><a:t>{word}</a:t></a:r>" for word in words) + "</a:p>" * depth
    slide = (
        f'<p:sld xmlns:p="{_NS_P}" xmlns:a="{_NS_A}"><p:cSld><p:spTree><p:sp><p:txBody>'
        f"{nested}<a:t>loose</a:t></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    deck = _deck(tmp_path / "deep.pptx", [("unused",)], slide_xml={1: slide})
    slides = extract_slides(str(deck))
    assert slides == [(1, "".join(words) + "\nloose")]
    assert len(slides[0][1]) <= len(slide)


def _zip_named(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return path


@pytest.mark.parametrize(
    "members",
    [
        {"hello.txt": "hi"},
        {"ppt/slides/slide1.xml": _slide_xml("orphan")},
    ],
    ids=["no-deck-parts", "slides-without-a-presentation"],
)
def test_a_zip_that_is_not_a_deck_is_an_error_result_not_an_empty_deck(tmp_path, members):
    """Ingestion replaces a source's items with what a read returns, so a container
    with no presentation part must fail the read rather than index as a textless deck."""
    deck = _zip_named(tmp_path / "deck.pptx", members)
    text, meta = FileReader().read(str(deck))
    assert text.startswith("Error reading file:") and meta["format"] == "error"


def test_best_effort_slide_reads_keep_a_deck_without_a_presentation_part(tmp_path):
    from kiro_crew.doc_parser import extract_slides

    deck = _zip_named(tmp_path / "loose.pptx", {"ppt/slides/slide1.xml": _slide_xml("orphan")})
    assert extract_slides(str(deck)) == [(1, "orphan")]
    assert extract_slides(str(_zip_named(tmp_path / "x.pptx", {"hello.txt": "hi"}))) == []


def test_deck_text_stops_at_the_character_cap_and_says_so(tmp_path, monkeypatch):
    # The walk stops after the slide that meets the budget, so one long slide
    # would carry the text past it if the reader did not cut it.
    monkeypatch.setattr(readers, "_PPTX_MAX_CHARS", 10)
    deck = _deck(tmp_path / "long.pptx", [("A" * 25,), ("B" * 5,)])
    text, meta = FileReader().read(str(deck))
    assert text == "## Slide 1\n" + "A" * 10
    assert meta["truncated"] is True and meta["slide_count"] == 1


def test_a_deck_without_the_xml_parser_is_a_missing_dependency(tmp_path, monkeypatch):
    deck = _deck(tmp_path / "plan.pptx", [("Kickoff",)])
    monkeypatch.setitem(sys.modules, "defusedxml", None)
    monkeypatch.setitem(sys.modules, "defusedxml.ElementTree", None)
    text, meta = FileReader().read(str(deck))
    assert "defusedxml" in text and meta["format"] == "error"


def test_workbook_rows_carry_their_sheet_and_row_number(tmp_path):
    book = _workbook(
        tmp_path / "stock.xlsx",
        {
            "Bins": [["sku", "qty"], ["A-1", 12], [None, None], ["B-2", 3.5]],
            "Notes": [["line one\nline two", "ok"]],
        },
    )
    text, meta = FileReader().read(str(book))
    assert text.split("\n") == [
        "Bins · row 1: sku | qty",
        "Bins · row 2: A-1 | 12",
        "Bins · row 4: B-2 | 3.5",
        "Notes · row 1: line one line two | ok",
    ]
    assert meta["format"] == "xlsx" and meta["sheet_count"] == 2
    assert not meta.get("truncated")


def test_workbook_cells_are_read_as_cached_values_not_formulas(tmp_path):
    book = _workbook(tmp_path / "calc.xlsx", {"S": [[1, 2, "=A1+B1"]]})
    text, _meta = FileReader().read(str(book))
    assert text == "S · row 1: 1 | 2"


def test_an_empty_workbook_reads_as_empty_text(tmp_path):
    book = _workbook(tmp_path / "empty.xlsx", {"S": []})
    text, meta = FileReader().read(str(book))
    assert text == "" and meta["format"] == "xlsx"


@pytest.mark.parametrize("payload", [b"not an archive", b"PK\x03\x04 truncated"])
def test_a_workbook_that_is_not_an_archive_is_an_error_result(tmp_path, payload):
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(payload)
    text, meta = FileReader().read(str(broken))
    assert text.startswith("Error reading file:") and meta["format"] == "error"


def test_a_workbook_over_the_member_ceiling_is_an_error_result(tmp_path, monkeypatch):
    book = _workbook(tmp_path / "big.xlsx", {"S": [["a"]]})
    monkeypatch.setattr(readers, "WORKBOOK_MAX_MEMBERS", 2)
    text, meta = FileReader().read(str(book))
    assert text.startswith("Error reading file:") and meta["format"] == "error"


def test_a_workbook_that_expands_past_the_ceiling_is_an_error_result(tmp_path, monkeypatch):
    book = _workbook(tmp_path / "bomb.xlsx", {"S": [["a"]]})
    monkeypatch.setattr(readers, "WORKBOOK_MAX_EXPANDED_BYTES", 10)
    text, meta = FileReader().read(str(book))
    assert text.startswith("Error reading file:") and meta["format"] == "error"


def test_every_workbook_reader_applies_one_pair_of_archive_ceilings():
    from kiro_crew import zip_vet
    from kiro_crew.dashboard.handlers import files

    assert readers.WORKBOOK_MAX_MEMBERS is files._SHEET_MAX_MEMBERS is zip_vet.WORKBOOK_MAX_MEMBERS
    assert (
        readers.WORKBOOK_MAX_EXPANDED_BYTES
        is files._SHEET_MAX_EXPANDED_BYTES
        is zip_vet.WORKBOOK_MAX_EXPANDED_BYTES
    )


def test_workbook_text_stops_at_the_character_cap_and_says_so(tmp_path, monkeypatch):
    book = _workbook(tmp_path / "long.xlsx", {"S": [[f"value-{n}"] for n in range(50)]})
    monkeypatch.setattr(readers, "_XLSX_MAX_CHARS", 60)
    text, meta = FileReader().read(str(book))
    assert meta["truncated"] is True and meta["format"] == "xlsx"
    assert 0 < len(text) <= 60
    assert text.startswith("S · row 1: value-0")


def test_workbook_rows_stop_at_the_wall_clock_deadline_and_say_so(tmp_path, monkeypatch):
    book = _workbook(tmp_path / "slow.xlsx", {"S": [[n] for n in range(10)]})
    # Zero: the deadline is already spent when the first row is read.
    monkeypatch.setattr(readers, "_XLSX_WALL_SECS", 0)
    text, meta = FileReader().read(str(book))
    assert text == ""
    assert (meta["format"], meta["sheet_count"], meta["truncated"]) == ("xlsx", 1, True)


def test_the_knowledge_reader_and_file_grep_share_one_workbook_row_walk():
    """Two copies of the walk drift (one skipped a lying dimension record, one did not)."""
    from kiro_crew.dashboard.handlers import files

    src = Path(readers.__file__).read_text(encoding="utf-8")
    grep_src = Path(files._grep_xlsx_segments.__code__.co_filename).read_text(encoding="utf-8")
    for text in (src, grep_src):
        assert "iter_rows" not in text and "load_workbook" not in text
        assert "read_workbook_rows" in text


def test_a_workbook_without_openpyxl_is_a_missing_dependency(tmp_path, monkeypatch):
    book = _workbook(tmp_path / "stock.xlsx", {"S": [["a"]]})
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    text, meta = FileReader().read(str(book))
    assert "openpyxl" in text and meta["format"] == "error"


def test_the_workbook_the_reader_opens_is_the_one_the_vet_judged(tmp_path):
    """A zip that is not a workbook (no ``xl/workbook.xml``) is refused, not indexed as empty."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    other = tmp_path / "other.xlsx"
    other.write_bytes(buf.getvalue())
    text, meta = FileReader().read(str(other))
    assert text.startswith("Error reading file:") and meta["format"] == "error"


def test_importing_the_readers_loads_neither_office_parser(tmp_path):
    """The readers sit on the gateway's boot import chain; both parsers load on first read."""
    code = (
        "import sys; import kiro_crew.knowledge.readers; "
        "print(repr([m for m in sys.modules if m.split('.')[0] in ('openpyxl', 'defusedxml')]))"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=default_timeout(),
        cwd=tmp_path,
    )
    assert res.returncode == 0, res.stderr
    assert ast.literal_eval(res.stdout.strip()) == []
