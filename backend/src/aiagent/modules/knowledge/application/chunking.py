"""Chia đoạn theo cấu trúc (ADR-0019): giữ ranh giới mục/đoạn văn, ~800 token, chồng lấn ~100.

Không cần tokenizer chính xác: tiếng Việt trung bình ~3 ký tự/token với bge-m3 → đo bằng ký tự.
"""

from __future__ import annotations

import re

from aiagent.modules.knowledge.domain import ChunkDraft, Section

MAX_CHARS = 2400
OVERLAP_CHARS = 300
MIN_CHARS = 200

_SENTENCE = re.compile(r"(?<=[.!?…])\s+")


def _pieces(text: str) -> list[str]:
    """Đoạn văn; đoạn quá dài thì tách theo câu; câu quá dài thì cắt cứng."""
    out: list[str] = []
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        if len(para) <= MAX_CHARS:
            out.append(para)
            continue
        for sentence in _SENTENCE.split(para):
            while len(sentence) > MAX_CHARS:
                out.append(sentence[:MAX_CHARS])
                sentence = sentence[MAX_CHARS - OVERLAP_CHARS :]
            if sentence.strip():
                out.append(sentence.strip())
    return out


def _tail(text: str) -> str:
    """Phần cuối (~OVERLAP) bắt đầu ở ranh giới từ — làm ngữ cảnh chồng lấn cho chunk sau."""
    if len(text) <= OVERLAP_CHARS:
        return text
    cut = text[-OVERLAP_CHARS:]
    space = cut.find(" ")
    return cut[space + 1 :] if 0 <= space < 60 else cut


def chunk_sections(sections: list[Section]) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    for section in sections:
        buf = ""
        for piece in _pieces(section.text):
            if buf and len(buf) + 2 + len(piece) > MAX_CHARS:
                chunks.append(
                    ChunkDraft(len(chunks), buf, heading=section.heading, page=section.page)
                )
                buf = _tail(buf) + "\n\n" + piece
            else:
                buf = f"{buf}\n\n{piece}" if buf else piece
        if buf.strip():
            # Mục quá ngắn thì nhập vào chunk trước cùng tiêu đề/trang cho đủ ngữ cảnh.
            prev = chunks[-1] if chunks else None
            if (
                prev is not None
                and len(buf) < MIN_CHARS
                and prev.heading == section.heading
                and len(prev.text) + len(buf) <= MAX_CHARS
            ):
                chunks[-1] = ChunkDraft(prev.ord, f"{prev.text}\n\n{buf}", prev.heading, prev.page)
            else:
                chunks.append(
                    ChunkDraft(len(chunks), buf, heading=section.heading, page=section.page)
                )
    return chunks
