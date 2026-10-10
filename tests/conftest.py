"""Shared fixtures: synthetic PDFs and a scripted LLM provider (no network, no dataset)."""

from __future__ import annotations

from pathlib import Path

import pytest


def make_pdf(
    path: Path, pages: list[str], *, doubled: bool = False, duplicate_layer: bool = False
) -> Path:
    """Write a small text PDF with one block of text per page using reportlab-free raw PDF."""
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    content_ids: list[int] = []
    for text in pages:
        lines = text.split("\n")
        stream_lines = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"]
        for line in lines:
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream_lines.append(f"({esc}) Tj T*")
            if doubled:
                # second copy of the same glyphs, shifted 1.5pt right: a doubled text layer
                stream_lines.append(f"1.5 14 Td ({esc}) Tj -1.5 -14 Td")
        stream_lines.append("ET")
        if duplicate_layer:
            # the whole page drawn a second time as a separate run, with a running header
            # added and a slight vertical drift — the "two text layers" defect
            stream_lines += [
                "BT",
                "/F1 11 Tf",
                "14 TL",
                "50 800 Td",
                "(Running header) Tj T*",
                "ET",
            ]
            stream_lines += ["BT", "/F1 11 Tf", "14 TL", "50 777.5 Td"]
            for line in lines:
                esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                stream_lines.append(f"({esc}) Tj T*")
            stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1")
        cid = add(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        content_ids.append(cid)
        page_ids.append(0)  # placeholder
    pages_id = len(objects) + len(pages) + 1
    for i, cid in enumerate(content_ids):
        pid = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] /Contents {cid} 0 R "
            f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode()
        )
        page_ids[i] = pid
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


@pytest.fixture
def synthetic_pdf(tmp_path: Path) -> Path:
    body = "Acme Holdings plc is the parent company of the Acme Group. " * 4
    pages = [
        f"Acme Holdings plc Annual Report 2023\nPage {i}\n{body}\nRevenue 1,234 million"
        for i in range(1, 6)
    ]
    return make_pdf(tmp_path / "acme.pdf", pages)


@pytest.fixture
def doubled_pdf(tmp_path: Path) -> Path:
    body = "Every glyph on this page is drawn twice by the producer. " * 3
    pages = [f"Doubled Report\n{body}\nRevenue 9,876 million" for _ in range(3)]
    return make_pdf(tmp_path / "doubled.pdf", pages, doubled=True)


@pytest.fixture
def duplicate_layer_pdf(tmp_path: Path) -> Path:
    body = "\n".join(
        f"Line {i} of the body discusses revenue, costs and the outlook for the group in detail."
        for i in range(40)
    )
    pages = [f"Layered Report page {n}\n{body}" for n in range(1, 4)]
    return make_pdf(tmp_path / "layered.pdf", pages, duplicate_layer=True)
