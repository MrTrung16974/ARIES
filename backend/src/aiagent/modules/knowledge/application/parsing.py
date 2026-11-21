"""Đọc tài liệu → các đoạn có tiêu đề/trang (ADR-0019). Nhận diện loại theo NỘI DUNG, không tin
tên file/Content-Type do người dùng gửi."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath

from aiagent.modules.knowledge.domain import ParsedDocument, Section
from aiagent.shared_kernel.errors import ValidationError

MAX_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 1500
_TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".html", ".htm", ".json", ".log"}


def detect_type(data: bytes, filename: str) -> str:
    head = data[:8]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        except zipfile.BadZipFile:
            pass
        raise ValidationError("Chỉ hỗ trợ tệp nén .docx (Word).")
    ext = PurePosixPath(filename.lower()).suffix
    if b"\x00" in data[:4096]:
        raise ValidationError("Không đọc được tệp nhị phân này.")
    if ext in (".html", ".htm") or data[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
        return "text/html"
    if ext in (".csv", ".tsv"):
        return "text/csv"
    if ext in (".md", ".markdown"):
        return "text/markdown"
    if ext and ext not in _TEXT_EXT:
        raise ValidationError(f"Chưa hỗ trợ loại tệp '{ext}'.")
    return "text/plain"


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1258", errors="replace")  # bảng mã Windows tiếng Việt


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _stem(filename: str) -> str:
    return PurePosixPath(filename).stem[:200] or "Tài liệu"


# ------------------------------------------------------------------ từng loại
def _pdf(data: bytes, filename: str) -> ParsedDocument:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValidationError("PDF có mật khẩu — hãy gỡ mật khẩu trước khi tải lên.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValidationError(f"PDF quá {MAX_PDF_PAGES} trang.")
        sections = []
        for i, page in enumerate(reader.pages, start=1):
            text = _clean(page.extract_text() or "")
            if text:
                sections.append(Section(text=text, page=i))
        metadata = reader.metadata
        meta_title = str((metadata.title if metadata else "") or "").strip()
    except PdfReadError as exc:
        raise ValidationError("PDF hỏng hoặc không đọc được.") from exc
    if not sections:
        raise ValidationError("PDF không có chữ (có thể là ảnh scan — cần OCR, chưa hỗ trợ).")
    return ParsedDocument(title=meta_title[:200] or _stem(filename), sections=sections)


def _docx(data: bytes, filename: str) -> ParsedDocument:
    import docx

    document = docx.Document(io.BytesIO(data))
    sections: list[Section] = []
    heading = ""
    buf: list[str] = []
    title = (document.core_properties.title or "").strip()

    def flush() -> None:
        text = _clean("\n".join(buf))
        if text:
            sections.append(Section(text=text, heading=heading))
        buf.clear()

    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name if para.style is not None else "") or ""
        if style.lower().startswith(("heading", "title")):
            flush()
            heading = text[:200]
            title = title or (text if style.lower().startswith("title") else "")
        else:
            buf.append(text)
    flush()
    for table in document.tables:  # bảng: mỗi hàng một dòng "ô | ô"
        rows = [" | ".join(c.text.strip() for c in r.cells) for r in table.rows]
        text = _clean("\n".join(r for r in rows if r.strip(" |")))
        if text:
            sections.append(Section(text=text, heading="Bảng"))
    if not sections:
        raise ValidationError("Tệp Word không có nội dung chữ.")
    return ParsedDocument(title=title[:200] or _stem(filename), sections=sections)


class _HtmlSections(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg", "nav", "footer"})
    _HEAD = frozenset({"h1", "h2", "h3"})
    _BLOCK = frozenset({"p", "div", "li", "br", "tr", "section", "article", "pre", "h4", "h5"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.sections: list[Section] = []
        self._heading = ""
        self._buf: list[str] = []
        self._skip = 0
        self._in: str | None = None
        self._head_buf: list[str] = []

    def _flush(self) -> None:
        text = _clean("".join(self._buf))
        if text:
            self.sections.append(Section(text=text, heading=self._heading))
        self._buf = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "title" or tag in self._HEAD:
            self._in = tag
            self._head_buf = []
            if tag in self._HEAD:
                self._flush()
        elif tag in self._BLOCK:
            self._buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag == self._in:
            text = " ".join("".join(self._head_buf).split())
            if tag == "title":
                self.title = text
            else:
                self._heading = text[:200]
            self._in = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in:
            self._head_buf.append(data)
        else:
            self._buf.append(data)


def _html(data: bytes, filename: str) -> ParsedDocument:
    parser = _HtmlSections()
    parser.feed(_decode(data))
    parser.close()
    parser._flush()
    if not parser.sections:
        raise ValidationError("Trang HTML không có nội dung chữ.")
    return ParsedDocument(title=parser.title[:200] or _stem(filename), sections=parser.sections)


def _markdown(data: bytes, filename: str) -> ParsedDocument:
    sections: list[Section] = []
    heading, title = "", ""
    buf: list[str] = []
    for line in _decode(data).splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            text = _clean("\n".join(buf))
            if text:
                sections.append(Section(text=text, heading=heading))
            buf = []
            heading = m.group(2).strip()[:200]
            title = title or (heading if len(m.group(1)) == 1 else "")
        else:
            buf.append(line)
    text = _clean("\n".join(buf))
    if text:
        sections.append(Section(text=text, heading=heading))
    if not sections:
        raise ValidationError("Tệp trống.")
    return ParsedDocument(title=title or _stem(filename), sections=sections)


def _csv(data: bytes, filename: str) -> ParsedDocument:
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        raise ValidationError("Tệp CSV trống.")
    header, body = rows[0], rows[1:]
    # Mỗi hàng thành "cột: giá trị" để tìm kiếm/trả lời theo ngữ nghĩa.
    lines = [
        "; ".join(
            f"{h.strip()}: {v.strip()}" for h, v in zip(header, row, strict=False) if v.strip()
        )
        for row in body
    ]
    sections, size = [], 40
    for i in range(0, len(lines), size):
        chunk = "\n".join(line for line in lines[i : i + size] if line)
        if chunk:
            sections.append(Section(text=chunk, heading=f"Hàng {i + 1}–{i + size}"))
    if not sections:
        raise ValidationError("Tệp CSV không có dữ liệu.")
    return ParsedDocument(title=_stem(filename), sections=sections)


def _text(data: bytes, filename: str) -> ParsedDocument:
    text = _clean(_decode(data))
    if not text:
        raise ValidationError("Tệp trống.")
    return ParsedDocument(title=_stem(filename), sections=[Section(text=text)])


def parse(data: bytes, filename: str) -> tuple[str, ParsedDocument]:
    """(content_type thật, tài liệu đã parse). ValidationError với thông điệp cho người dùng."""
    if not data:
        raise ValidationError("Tệp trống.")
    if len(data) > MAX_BYTES:
        raise ValidationError("Tệp quá 25 MB.")
    content_type = detect_type(data, filename)
    handler = {
        "application/pdf": _pdf,
        "text/html": _html,
        "text/markdown": _markdown,
        "text/csv": _csv,
        "text/plain": _text,
    }.get(content_type, _docx)
    return content_type, handler(data, filename)
