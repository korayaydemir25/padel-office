"""Instagram Graph API — yayınlama ve metrik çekme. LLM kullanmaz.

Gereksinimler (Meta tarafı):
  * Instagram Business/Creator hesap + bağlı Facebook sayfası
  * Uzun ömürlü erişim token'ı (instagram_content_publish, instagram_basic,
    instagram_manage_insights izinleri)
  * Medya dosyaları HERKESE AÇIK bir URL'den erişilebilir olmalı (Instagram dosyayı
    kendisi indirir). output/ klasörünü bir CDN/S3/kendi sitene senkronlayıp
    PUBLIC_MEDIA_BASE_URL'e o adresi yaz.
"""
from __future__ import annotations

import os
import time

import requests


class Instagram:
    def __init__(self, dry_run: bool = False):
        self.token = os.getenv("IG_ACCESS_TOKEN", "")
        self.user_id = os.getenv("IG_USER_ID", "")
        self.base = f"https://graph.facebook.com/{os.getenv('GRAPH_API_VERSION', 'v23.0')}"
        self.public_base = os.getenv("PUBLIC_MEDIA_BASE_URL", "").rstrip("/")
        self.dry_run = dry_run or not (self.token and self.user_id)

    # --- yardımcılar ------------------------------------------------------
    def _post(self, path, **params):
        r = requests.post(f"{self.base}/{path}", data={**params, "access_token": self.token},
                          timeout=60)
        r.raise_for_status()
        return r.json()

    def _get(self, path, **params):
        r = requests.get(f"{self.base}/{path}", params={**params, "access_token": self.token},
                         timeout=60)
        r.raise_for_status()
        return r.json()

    def public_url(self, local_path: str) -> str:
        if not self.public_base:
            raise RuntimeError("PUBLIC_MEDIA_BASE_URL ayarlı değil; Instagram yerel dosyayı göremez.")
        return f"{self.public_base}/{os.path.basename(local_path)}"

    # --- yayın ------------------------------------------------------------
    def publish(self, media_path: str, caption: str, is_video: bool) -> str:
        if self.dry_run:
            print(f"  [DRY-RUN] Yayınlanacaktı: {media_path}\n  {caption[:80]}...")
            return f"dry_{int(time.time())}"

        url = self.public_url(media_path)
        if is_video:
            c = self._post(f"{self.user_id}/media", media_type="REELS", video_url=url,
                           caption=caption, share_to_feed="true")
            self._wait_ready(c["id"])
        else:
            c = self._post(f"{self.user_id}/media", image_url=url, caption=caption)
        res = self._post(f"{self.user_id}/media_publish", creation_id=c["id"])
        return res["id"]

    def _wait_ready(self, container_id: str, timeout: int = 300):
        start = time.time()
        while time.time() - start < timeout:
            st = self._get(container_id, fields="status_code").get("status_code")
            if st == "FINISHED":
                return
            if st == "ERROR":
                raise RuntimeError("Instagram video işlemesi başarısız oldu.")
            time.sleep(10)
        raise TimeoutError("Video işleme zaman aşımına uğradı.")

    # --- metrikler --------------------------------------------------------
    def recent_media_metrics(self, limit: int = 30) -> list[dict]:
        if self.dry_run:
            return []
        media = self._get(f"{self.user_id}/media",
                          fields="id,caption,media_type,timestamp,like_count,comments_count",
                          limit=limit).get("data", [])
        out = []
        for m in media:
            row = {"ig_media_id": m["id"], "posted_at": m.get("timestamp"),
                   "media_type": m.get("media_type"), "caption": m.get("caption", ""),
                   "likes": m.get("like_count", 0), "comments": m.get("comments_count", 0),
                   "reach": 0, "saved": 0, "shares": 0}
            try:
                ins = self._get(f"{m['id']}/insights", metric="reach,saved,shares").get("data", [])
                for i in ins:
                    row[i["name"]] = (i.get("values") or [{}])[0].get("value", 0)
            except requests.HTTPError:
                pass  # bazı eski/özel medya türleri insight vermez
            out.append(row)
        return out
