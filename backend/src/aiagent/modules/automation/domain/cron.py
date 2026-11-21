"""Biểu thức cron 5 trường (phút giờ ngày tháng thứ) — hàm thuần, không dependency (ADR-0021).

Hỗ trợ `*`, danh sách `a,b`, khoảng `a-b`, bước `*/n` và `a-b/n`, thứ 0–7 (0 và 7 = Chủ nhật),
bí danh `@hourly`, `@daily`, `@weekly`, `@monthly`. Như cron chuẩn: khi cả "ngày" và "thứ" đều bị
giới hạn thì khớp một trong hai.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
}
_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_NAMES = ("phút", "giờ", "ngày", "tháng", "thứ")
MAX_PER_HOUR = 12  # tối thiểu 5 phút một lần


class CronError(ValueError):
    pass


def _field(text: str, idx: int) -> frozenset[int]:
    lo, hi = _BOUNDS[idx]
    out: set[int] = set()
    for part in text.split(","):
        rng, _, step_s = part.partition("/")
        try:
            step = int(step_s) if step_s else 1
            if rng == "*":
                start, end = lo, hi
            elif "-" in rng:
                a, b = rng.split("-", 1)
                start, end = int(a), int(b)
            else:
                start = int(rng)
                end = hi if step_s else start
        except ValueError:
            raise CronError(f"Trường {_NAMES[idx]} không hợp lệ: {part!r}") from None
        if step < 1 or start < lo or end > hi or start > end:
            raise CronError(f"Trường {_NAMES[idx]} ngoài phạm vi {lo}–{hi}: {part!r}")
        out.update(range(start, end + 1, step))
    if idx == 4 and 7 in out:  # 7 = Chủ nhật
        out.discard(7)
        out.add(0)
    return frozenset(out)


@dataclass(frozen=True)
class CronExpr:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Chủ nhật
    day_any: bool
    weekday_any: bool

    @classmethod
    def parse(cls, expr: str) -> CronExpr:
        text = ALIASES.get(expr.strip().lower(), expr.strip())
        parts = text.split()
        if len(parts) != 5:
            raise CronError("Biểu thức cron phải có 5 trường: phút giờ ngày tháng thứ.")
        minutes, hours, days, months, weekdays = (_field(p, i) for i, p in enumerate(parts))
        return cls(
            minutes,
            hours,
            days,
            months,
            weekdays,
            day_any=parts[2] == "*",
            weekday_any=parts[4] == "*",
        )

    def _day_ok(self, d: datetime) -> bool:
        dom = d.day in self.days
        dow = (d.isoweekday() % 7) in self.weekdays
        if self.day_any and self.weekday_any:
            return True
        if self.day_any:
            return dow
        if self.weekday_any:
            return dom
        return dom or dow

    def next_after(self, after: datetime, tz: str = "UTC") -> datetime:
        """Thời điểm khớp đầu tiên SAU `after` (UTC, có tzinfo), trả về UTC."""
        zone = zone_of(tz)
        local = after.astimezone(zone).replace(second=0, microsecond=0, tzinfo=None)
        t = local + timedelta(minutes=1)
        limit = local + timedelta(days=366 * 5)
        while t <= limit:
            if t.month not in self.months:
                t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
                continue
            if not self._day_ok(t):
                t = t.replace(hour=0, minute=0) + timedelta(days=1)
                continue
            if t.hour not in self.hours:
                t = t.replace(minute=0) + timedelta(hours=1)
                continue
            if t.minute not in self.minutes:
                t += timedelta(minutes=1)
                continue
            result = t.replace(tzinfo=zone).astimezone(UTC)
            if result > after:
                return result
            t += timedelta(minutes=1)  # giờ lùi (DST) có thể quay về quá khứ
        raise CronError("Biểu thức cron không bao giờ khớp (ví dụ 31/2).")


def zone_of(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise CronError(f"Múi giờ không hợp lệ: {tz!r}") from None


def validate(expr: str, tz: str, *, max_per_hour: int = MAX_PER_HOUR) -> CronExpr:
    """Phân tích + kiểm tần suất tối đa + chắc chắn có lần chạy kế tiếp."""
    cron = CronExpr.parse(expr)
    if len(cron.minutes) > max_per_hour:
        raise CronError("Lịch chạy dày nhất là 5 phút một lần.")
    cron.next_after(datetime.now(UTC), tz)
    return cron
