"""Cleaner: normalisation, doubled glyphs, line dedup, running headers, page dropping."""

from core.ingestion.cleaner import (
    CleaningOptions,
    clean_pages,
    collapse_doubled_characters,
    dedupe_lines,
    find_running_lines,
    looks_character_doubled,
    normalise_text,
    strip_running_lines,
)
from core.models import PageText


def test_normalise_fixes_ligatures_and_spaces():
    assert normalise_text("ﬁnancial   proﬁt margin  ") == "financial profit margin"
    assert normalise_text("a\n\nb") == "a\n\nb"


def test_looks_character_doubled():
    assert looks_character_doubled(
        "IITTEEMM 11CC.. CCyybbeerrsseeccuurriittyy iiss ggoooodd ffoorr yyoouu ttooddaayy"
    )
    assert not looks_character_doubled("Item 1C. Cybersecurity is good for you today and tomorrow")
    assert not looks_character_doubled("short")


def test_collapse_doubled_characters():
    doubled = "RReevveennuuee 9966,,777733 wwaass ggoooodd"
    assert collapse_doubled_characters(doubled) == "Revenue 96,773 was good"
    assert collapse_doubled_characters("aa ••") == "a •"
    # healthy double letters survive
    assert (
        collapse_doubled_characters("we need a good balance sheet")
        == "we need a good balance sheet"
    )


def test_dedupe_lines_removes_near_duplicates_keeps_first():
    lines = ["Revenue 100", "Revenue 100", "Costs 50", "x", "Revenue 100", "", "", "Costs 50"]
    assert dedupe_lines(lines, window=3) == ["Revenue 100", "Costs 50", "x", "", "", "Costs 50"]


def test_running_header_detection_masks_digits_and_zones():
    pages = [
        [f"{i} Acme Report 2023 Management", "Body text one", "Total 100", "page footer"]
        for i in range(1, 7)
    ]
    running = find_running_lines(pages, min_page_ratio=0.5, min_pages=4, zone_lines=1)
    assert "# acme report #### management" in running
    assert "page footer" in running
    assert "body text one" not in running  # outside the header/footer zone
    stripped = strip_running_lines(pages[0], running)
    assert stripped == ["Body text one", "Total 100"]


def test_running_header_ignores_body_frequent_lines():
    pages = [["Header", *(["Total"] * 10), "Footer"] for _ in range(6)]
    running = find_running_lines(pages, zone_lines=1)
    assert running == {"header", "footer"}


def test_clean_pages_end_to_end_drops_short_pages_unless_tables():
    pages = [
        PageText(
            page=i,
            text=f"{i} Acme\n"
            + f"Lorem ipsum {chr(96 + i)} dolor sit amet " * 20
            + f"\nfooter {i}",
        )
        for i in range(1, 6)
    ]
    pages.append(PageText(page=6, text="tiny"))
    pages.append(PageText(page=7, text="tiny", tables=[[["a", "b"], ["1", "2"]]]))
    out = clean_pages(pages, CleaningOptions(min_chunk_chars=100, min_pages_for_header_detection=4))
    assert [p.page for p in out] == [1, 2, 3, 4, 5, 7]
    assert "Acme" not in out[0].text and "footer" not in out[0].text
    assert out[-1].tables


def test_long_section_headers_removed_at_lower_ratio():
    nav = "Acme Group Report 2023 To Our Stakeholders Combined Management Report Group Financial Statements"
    pages = []
    for i in range(1, 21):
        first = f"{i} {nav}" if i <= 4 else f"Section title {chr(65 + i % 9)}"
        pages.append([first, f"Body paragraph {chr(96 + i)} " * 5, "Total 1,000"])
    running = find_running_lines(pages, min_page_ratio=0.5, zone_lines=1)
    assert any("acme group report" in r for r in running)
    assert not any("section title" in r for r in running)  # short and rare: kept
    assert not any("body paragraph" in r for r in running)
