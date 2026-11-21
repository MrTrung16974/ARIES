from __future__ import annotations

import io
from uuid import uuid4

import pytest

from aiagent.modules.knowledge.application import chunk_sections, parse, rrf_merge
from aiagent.modules.knowledge.application.chunking import MAX_CHARS
from aiagent.modules.knowledge.domain import ChunkHit, Section
from aiagent.modules.knowledge.infrastructure.adapters import FileBlobStore
from aiagent.modules.knowledge.infrastructure.sql_knowledge_repository import keyword_query
from aiagent.shared_kernel.errors import ValidationError


def test_parse_markdown_keeps_headings() -> None:
    ctype, doc = parse(
        "# Sổ tay nhân viên\n\nGiới thiệu.\n\n## Nghỉ phép\n\nMỗi năm 12 ngày phép.\n".encode(),
        "so-tay.md",
    )
    assert ctype == "text/markdown" and doc.title == "Sổ tay nhân viên"
    assert [s.heading for s in doc.sections] == ["Sổ tay nhân viên", "Nghỉ phép"]


def test_parse_html_strips_scripts() -> None:
    html = (
        b"<html><head><title>Quy dinh</title><script>evil()</script></head>"
        b"<body><h2>Muc 1</h2><p>Noi dung</p><nav>menu</nav></body></html>"
    )
    ctype, doc = parse(html, "x.html")
    assert ctype == "text/html" and doc.title == "Quy dinh"
    assert doc.sections[0].heading == "Muc 1" and "evil" not in doc.sections[0].text
    assert "menu" not in doc.sections[0].text


def test_parse_docx_with_headings_and_table() -> None:
    import docx

    d = docx.Document()
    d.add_heading("Chính sách bảo hành", level=1)
    d.add_paragraph("Bảo hành 24 tháng cho laptop.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Sản phẩm", "Thời hạn"
    t.cell(1, 0).text, t.cell(1, 1).text = "Màn hình", "36 tháng"
    buf = io.BytesIO()
    d.save(buf)
    ctype, doc = parse(buf.getvalue(), "chinh-sach.docx")
    assert ctype.endswith("wordprocessingml.document")
    assert doc.sections[0].heading == "Chính sách bảo hành"
    assert "24 tháng" in doc.sections[0].text
    assert "Màn hình | 36 tháng" in doc.sections[-1].text


def test_parse_csv_rows_as_facts() -> None:
    _, doc = parse(b"ma,ten,gia\nSP1,Laptop,15990000\n", "gia.csv")
    assert "ma: SP1; ten: Laptop; gia: 15990000" in doc.sections[0].text


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b"", "a.txt"),
        (b"\x00\x01\x02binary", "a.bin"),
        (b"PK\x03\x04not-a-docx", "a.xlsx"),
        (b"MZ\x90\x00", "virus.exe"),
        (b"%PDF-1.7 garbage", "broken.pdf"),
    ],
)
def test_parse_rejects_bad_files(data: bytes, name: str) -> None:
    with pytest.raises(ValidationError):
        parse(data, name)


def test_chunking_respects_size_and_overlap() -> None:
    para = "Câu thử nghiệm về chính sách nghỉ phép của công ty. " * 20
    text = "\n\n".join(para for _ in range(6))
    chunks = chunk_sections([Section(text=text, heading="Nghỉ phép", page=3)])
    assert len(chunks) > 1
    assert all(len(c.text) <= MAX_CHARS + 400 for c in chunks)
    assert all(c.heading == "Nghỉ phép" and c.page == 3 for c in chunks)
    assert [c.ord for c in chunks] == list(range(len(chunks)))
    # Chồng lấn: chunk sau bắt đầu bằng phần cuối chunk trước.
    assert chunks[1].text[:40] in chunks[0].text


def test_chunking_merges_tiny_sections() -> None:
    chunks = chunk_sections(
        [Section(text="Đoạn dài " * 40, heading="A"), Section(text="ngắn", heading="A")]
    )
    assert len(chunks) == 1 and chunks[0].text.endswith("ngắn")


def test_rrf_merge_prefers_items_ranked_by_both() -> None:
    def hit(name: str) -> ChunkHit:
        return ChunkHit(uuid4(), uuid4(), uuid4(), name, name, name, "", None, 0.0)

    a, b, c = hit("a"), hit("b"), hit("c")
    merged = rrf_merge([a, b], [c, b])
    assert merged[0].text == "b"


def test_keyword_query_sanitizes_operators() -> None:
    assert keyword_query("nghỉ phép & (DROP) | !x") == "nghỉ | phép | drop"
    assert keyword_query("!!") == ""


async def test_file_blob_store_blocks_traversal(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = FileBlobStore(tmp_path)
    await store.put("t/kb/doc", b"x")
    assert await store.get("t/kb/doc") == b"x"
    with pytest.raises(ValueError):
        await store.get("../etc/passwd")
    await store.delete("t/kb/doc")
    await store.delete("t/kb/doc")  # xóa lần hai không lỗi
