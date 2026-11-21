"""DLP — dò và che dữ liệu nhạy cảm trong văn bản (ADR-0021). Hàm thuần, không dependency.

Mức che (`AgentSpec.dlp`):
- `off`: không che.
- `mask` (mặc định cho kênh ngoài): khóa bí mật, số CCCD, số thẻ (Luhn).
- `strict`: thêm email và số điện thoại.

`find_secrets` dùng riêng để chặn tham số tool gửi ra ngoài có chứa khóa bí mật.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LEVELS = ("off", "mask", "strict")

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
        ),
    ),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("aries_key", re.compile(r"\bak_[A-Za-z0-9\-]+\.[0-9a-f]{32}\.[A-Za-z0-9_\-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
)
_CCCD = re.compile(r"(?<!\d)0\d{11}(?!\d)")  # CCCD 12 số, bắt đầu bằng mã tỉnh 0xx
_CARD = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<![\d+])(?:\+84|84|0)(?:[ .\-]?\d){9}(?!\d)")

LABELS = {
    "cccd": "[CCCD đã ẩn]",
    "card": "[số thẻ đã ẩn]",
    "email": "[email đã ẩn]",
    "phone": "[SĐT đã ẩn]",
}


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def find_secrets(text: str) -> list[Finding]:
    return [
        Finding(kind, m.start(), m.end())
        for kind, pattern in _SECRET_PATTERNS
        for m in pattern.finditer(text)
    ]


def find(text: str, level: str = "mask") -> list[Finding]:
    if level == "off":
        return []
    found = find_secrets(text)
    found += [Finding("cccd", m.start(), m.end()) for m in _CCCD.finditer(text)]
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found.append(Finding("card", m.start(), m.end()))
    if level == "strict":
        found += [Finding("email", m.start(), m.end()) for m in _EMAIL.finditer(text)]
        found += [Finding("phone", m.start(), m.end()) for m in _PHONE.finditer(text)]
    # Gộp vùng chồng nhau, ưu tiên vùng bắt đầu sớm / dài hơn
    found.sort(key=lambda f: (f.start, -(f.end - f.start)))
    merged: list[Finding] = []
    for f in found:
        if merged and f.start < merged[-1].end:
            continue
        merged.append(f)
    return merged


def mask(text: str, level: str = "mask") -> str:
    findings = find(text, level)
    if not findings:
        return text
    out, pos = [], 0
    for f in findings:
        out.append(text[pos : f.start])
        out.append(LABELS.get(f.kind, "[khóa bí mật đã ẩn]"))
        pos = f.end
    out.append(text[pos:])
    return "".join(out)


class StreamMasker:
    """Che dữ liệu trong luồng token: giữ lại đuôi `hold` ký tự để không cắt đôi một mẫu."""

    def __init__(self, level: str = "mask", hold: int = 64) -> None:
        self.level = level
        self.hold = hold
        self._buf = ""

    def feed(self, chunk: str) -> str:
        if self.level == "off":
            return chunk
        self._buf += chunk
        cut = len(self._buf) - self.hold
        if cut <= 0:
            return ""
        for f in find(self._buf, self.level):
            if f.start < cut < f.end:
                cut = f.start
                break
        head, self._buf = self._buf[:cut], self._buf[cut:]
        return mask(head, self.level)

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return mask(rest, self.level) if self.level != "off" else rest
