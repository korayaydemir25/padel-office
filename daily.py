"""Günlük mod — her gün 1 post, telefondan onay, GitHub Actions ile yayın.

Bu modda ayrı bir API anahtarı gerekmez: metni zamanlanmış görevdeki Claude yazar,
bu betik sadece bağlam verir, görseli üretir, kuralları kontrol eder ve kuyruğu yönetir.

  python daily.py context              # bugünün içerik sütunu, medya listesi, son postlar (JSON)
  python daily.py prepare post.json    # görseli üret + kural kontrolü → queue/<tarih>.json/.jpg
  python daily.py approve <tarih>      # onayla, yayın saatini ata (Actions paylaşır)
  python daily.py skip <tarih>         # bugünü atla
  python daily.py status               # kuyruğun durumu
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from office import media
from office.agents import rule_check

ROOT = Path(__file__).parent
QUEUE = ROOT / "queue"
BRAND = yaml.safe_load((ROOT / "config" / "brand.yaml").read_text(encoding="utf-8"))
TZ = ZoneInfo(BRAND.get("timezone", "Europe/Istanbul"))


def _items() -> list[dict]:
    QUEUE.mkdir(exist_ok=True)
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(QUEUE.glob("*.json"))]


def _save(item: dict) -> None:
    QUEUE.mkdir(exist_ok=True)
    (QUEUE / f"{item['id']}.json").write_text(json.dumps(item, ensure_ascii=False, indent=2),
                                              encoding="utf-8")


def _load(pid: str) -> dict:
    p = QUEUE / f"{pid}.json"
    if not p.exists():
        sys.exit(f"Kuyrukta bulunamadı: {pid}")
    return json.loads(p.read_text(encoding="utf-8"))


def cmd_context() -> None:
    items = _items()
    done = [i for i in items if i["status"] in ("approved", "published")]
    used = Counter(i.get("pillar") for i in done)
    pillars = BRAND["content_pillars"]
    # en az kullanılan sütun; eşitlikte son 2 postta olmayanı seç
    recent = [i.get("pillar") for i in done[-2:]]
    pillar = sorted(pillars, key=lambda p: (used[p], p in recent))[0]
    today = datetime.now(TZ).date().isoformat()
    print(json.dumps({
        "today": today,
        "already_in_queue_today": any(i["id"] == today for i in items),
        "suggested_pillar": pillar,
        "assets": media.list_assets(ROOT / "assets"),
        "recent_posts": [{"date": i["id"], "pillar": i.get("pillar"), "headline": i.get("headline"),
                          "status": i["status"]} for i in items[-14:]],
        "rules": {"headline_max": 40, "subline_max": 60,
                  "hashtags_always": BRAND["hashtags"]["always"],
                  "hashtag_pool": BRAND["hashtags"]["pool"],
                  "hashtags_max": BRAND["hashtags"]["max_per_post"],
                  "banned_words": BRAND.get("banned_words", []), "emoji_max": 3,
                  "templates": ["photo_overlay (assets'ten fotoğraf gerekir)", "text_card", "event"]},
    }, ensure_ascii=False, indent=2))


def cmd_prepare(path: str) -> None:
    post = json.loads(Path(path).read_text(encoding="utf-8"))
    pid = post.get("id") or datetime.now(TZ).date().isoformat()
    if post.get("template") == "reel":
        sys.exit("Günlük modda şimdilik sadece görsel postlar destekleniyor (reel yok).")
    assets = media.list_assets(ROOT / "assets")
    if post.get("source_asset") not in assets["photos"]:
        post["source_asset"] = None
        if post.get("template") == "photo_overlay":
            post["template"] = "text_card"
    tags = post.get("hashtags") or []
    post.update(id=pid, file_stem=pid,
                hashtags=" ".join(tags) if isinstance(tags, list) else tags)
    post["media_path"] = media.render_image(post, BRAND, str(ROOT / "assets"), str(QUEUE))
    issues = rule_check(post, BRAND)
    item = {k: post.get(k) for k in ("id", "pillar", "template", "headline", "subline", "caption",
                                     "hashtags", "source_asset", "visual_brief")}
    item.update(image=f"{pid}.jpg", status="pending_approval" if not issues else "needs_fix",
                issues=issues, created_at=datetime.now(TZ).isoformat(timespec="seconds"))
    _save(item)
    print(json.dumps({"id": pid, "image": str(QUEUE / item["image"]), "status": item["status"],
                      "issues": issues}, ensure_ascii=False, indent=2))


def cmd_add_reel(video: str, meta_path: str) -> None:
    """Hazır bir reels videosunu kuyruğa ekler. meta: {id?, pillar, caption, hashtags}."""
    import shutil
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    pid = meta.get("id") or datetime.now(TZ).date().isoformat() + "-reel"
    QUEUE.mkdir(exist_ok=True)
    shutil.copy(video, QUEUE / f"{pid}.mp4")
    tags = meta.get("hashtags") or []
    item = {"id": pid, "pillar": meta.get("pillar"), "template": "reel",
            "headline": meta.get("headline"), "caption": meta["caption"],
            "hashtags": " ".join(tags) if isinstance(tags, list) else tags,
            "video": f"{pid}.mp4", "image": None, "issues": [],
            "status": "pending_approval", "created_at": datetime.now(TZ).isoformat(timespec="seconds")}
    item["issues"] = rule_check({**item, "media_path": str(QUEUE / item["video"])}, BRAND)
    if item["issues"]:
        item["status"] = "needs_fix"
    _save(item)
    print(json.dumps({"id": pid, "status": item["status"], "issues": item["issues"]},
                     ensure_ascii=False, indent=2))


def _publish_time(now: datetime) -> datetime:
    d = BRAND.get("daily_publish_time", {"weekday": "19:30", "weekend": "18:00"})
    hhmm = d["weekend"] if now.weekday() >= 5 else d["weekday"]
    h, m = map(int, hhmm.split(":"))
    at = datetime.combine(now.date(), time(h, m), TZ)
    return at if at > now + timedelta(minutes=5) else now


def cmd_approve(pid: str, at: str | None = None) -> None:
    item = _load(pid)
    if item["status"] == "needs_fix":
        sys.exit(f"Bu post kural kontrolünden geçmedi: {item['issues']}")
    now = datetime.now(TZ)
    when = datetime.fromisoformat(at) if at else _publish_time(now)
    item.update(status="approved", approved_at=now.isoformat(timespec="seconds"),
                scheduled_at=when.isoformat(timespec="seconds"))
    _save(item)
    print(f"Onaylandı: {pid} → {item['scheduled_at']} için sıraya alındı.")


def cmd_skip(pid: str) -> None:
    item = _load(pid)
    item["status"] = "skipped"
    _save(item)
    print(f"Atlandı: {pid}")


def cmd_status() -> None:
    for i in _items()[-14:]:
        print(f"{i['id']}  {i['status']:<17} {i.get('scheduled_at') or '':<26} {i.get('headline')}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    {"context": lambda: cmd_context(),
     "prepare": lambda: cmd_prepare(a[1]),
     "approve": lambda: cmd_approve(a[1], a[2] if len(a) > 2 else None),
     "skip": lambda: cmd_skip(a[1]),
     "add-reel": lambda: cmd_add_reel(a[1], a[2]),
     "status": lambda: cmd_status()}[a[0]]()
