"""En iyi yayın saatlerini seçer — LLM yok, kendi verinden istatistik.

Veri < MIN_POSTS ise marka rehberindeki varsayılan pencereler kullanılır.
Veri biriktikçe (gün, saat) bazında etkileşim oranı en yüksek dilimler seçilir.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MIN_POSTS = 12


def _engagement(m: dict) -> float:
    reach = m.get("reach") or 0
    if reach <= 0:
        return 0.0
    eng = sum((m.get(k) or 0) for k in ("likes", "comments", "saved", "shares"))
    return eng / reach


def learned_slots(metrics: list[dict], tz: ZoneInfo) -> list[tuple[int, int]]:
    """(hafta günü, saat) dilimlerini etkileşime göre sıralar."""
    buckets = defaultdict(list)
    for m in metrics:
        if not m.get("posted_at"):
            continue
        dt = datetime.fromisoformat(m["posted_at"].replace("Z", "+00:00")).astimezone(tz)
        buckets[(dt.weekday(), dt.hour)].append(_engagement(m))
    scored = [(sum(v) / len(v), k) for k, v in buckets.items() if v]
    return [k for _, k in sorted(scored, reverse=True)]


def plan_slots(n: int, brand: dict, metrics: list[dict], week_start: date) -> list[datetime]:
    tz = ZoneInfo(brand.get("timezone", "Europe/Istanbul"))
    candidates: list[datetime] = []

    if len(metrics) >= MIN_POSTS:
        for wd, hr in learned_slots(metrics, tz):
            d = week_start + timedelta(days=wd)
            candidates.append(datetime.combine(d, time(hr, 0), tz))
    # varsayılanlar her zaman yedek olarak eklenir
    # Keşif: her gün farklı pencere denenir ki analiz ajanı karşılaştırma verisi toplasın.
    win = brand["posting_windows"]
    for i in range(7):
        d = week_start + timedelta(days=i)
        opts = win["weekend"] if d.weekday() >= 5 else win["weekday"]
        rotated = opts[i % len(opts):] + opts[:i % len(opts)]
        for hhmm in rotated:
            h, mi = map(int, hhmm.split(":"))
            candidates.append(datetime.combine(d, time(h, mi), tz))

    # Aynı güne iki post koyma, günlere yay
    chosen, used_days = [], set()
    now = datetime.now(tz)
    for dt in candidates:
        if dt <= now or dt.date() in used_days:
            continue
        chosen.append(dt)
        used_days.add(dt.date())
        if len(chosen) == n:
            break
    return sorted(chosen)
