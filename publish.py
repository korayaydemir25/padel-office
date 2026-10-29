"""GitHub Actions'ta çalışır: onaylı ve zamanı gelmiş postları Instagram'a gönderir.

Gerekli GitHub Secrets:
  IG_ACCESS_TOKEN  uzun ömürlü token
  IG_USER_ID       Instagram Business/Creator hesap ID'si
İsteğe bağlı Secret / Variable:
  IG_API_HOST      graph.facebook.com (Facebook ile giriş, varsayılan)
                   veya graph.instagram.com (Instagram ile giriş)

Instagram görseli kendisi indirir; bu yüzden görsel, repo'nun herkese açık
raw.githubusercontent.com adresinden, değişmez commit SHA'sıyla verilir.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

QUEUE = Path(__file__).parent / "queue"
TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
USER_ID = os.getenv("IG_USER_ID", "")
HOST = os.getenv("IG_API_HOST") or "graph.facebook.com"
BASE = f"https://{HOST}/{os.getenv('GRAPH_API_VERSION', 'v23.0')}"
REPO = os.getenv("GITHUB_REPOSITORY", "")
SHA = os.getenv("GITHUB_SHA", "main")


def api(method: str, path: str, **params) -> dict:
    r = requests.request(method, f"{BASE}/{path}", timeout=60,
                         **({"data": params} if method == "POST" else {"params": params}),
                         headers={"Authorization": f"Bearer {TOKEN}"})
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
    return r.json()


def wait_container(cid: str, tries: int = 30, every: int = 5) -> None:
    for _ in range(tries):
        st = api("GET", cid, fields="status_code").get("status_code")
        if st in (None, "FINISHED"):
            return
        if st == "ERROR":
            raise RuntimeError("Instagram medyayı işleyemedi (status ERROR).")
        time.sleep(every)
    raise TimeoutError("Instagram medya işleme zaman aşımı.")


def publish(item: dict) -> str:
    caption = f"{item['caption']}\n\n{item.get('hashtags') or ''}".strip()
    if item.get("video"):  # reels
        url = f"https://raw.githubusercontent.com/{REPO}/{SHA}/queue/{item['video']}"
        c = api("POST", f"{USER_ID}/media", media_type="REELS", video_url=url, caption=caption,
                share_to_feed="true")
        wait_container(c["id"], tries=60, every=10)
    else:
        url = f"https://raw.githubusercontent.com/{REPO}/{SHA}/queue/{item['image']}"
        c = api("POST", f"{USER_ID}/media", image_url=url, caption=caption)
        wait_container(c["id"])
    return api("POST", f"{USER_ID}/media_publish", creation_id=c["id"])["id"]


def main() -> int:
    if not (TOKEN and USER_ID):
        print("IG_ACCESS_TOKEN / IG_USER_ID secret'ları yok; yayın atlandı.")
        return 0
    now = datetime.now(timezone.utc)
    changed = 0
    for f in sorted(QUEUE.glob("*.json")):
        item = json.loads(f.read_text(encoding="utf-8"))
        if item.get("status") != "approved":
            continue
        if datetime.fromisoformat(item["scheduled_at"]) > now:
            print(f"{item['id']}: zamanı gelmedi ({item['scheduled_at']})")
            continue
        try:
            mid = publish(item)
            item.update(status="published", ig_media_id=mid,
                        published_at=now.isoformat(timespec="seconds"))
            print(f"✓ {item['id']} yayınlandı: {mid}")
        except Exception as e:  # noqa: BLE001
            item["attempts"] = item.get("attempts", 0) + 1
            item["last_error"] = str(e)
            if item["attempts"] >= 3:
                item["status"] = "failed"
            print(f"✗ {item['id']} yayınlanamadı: {e}")
        f.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        changed += 1
    print(f"{changed} post işlendi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
