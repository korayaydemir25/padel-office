"""Yönetici (orkestratör). Bilerek LLM DEĞİL: iş akışını sıradan Python yönetir.
Bir ajanın 'karar' vermesi gerekmeyen her adım koddadır → sıfır token maliyeti.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import agents, media, scheduler
from .instagram import Instagram
from .store import Store

MAX_REVISIONS = 2


class Office:
    def __init__(self, root: str | Path = ".", llm=None):
        self.root = Path(root)
        brand_path = self.root / "config" / "brand.yaml"
        self.brand_text = brand_path.read_text(encoding="utf-8")
        self.brand = yaml.safe_load(self.brand_text)
        self.tz = ZoneInfo(self.brand.get("timezone", "Europe/Istanbul"))
        self.store = Store(self.root / "data" / "office.db")
        self.assets_dir = str(self.root / "assets")
        self.out_dir = str(self.root / "output")
        self._llm = llm

    @property
    def llm(self):
        if self._llm is None:
            from .llm import LLM
            self._llm = LLM(self.brand_text, self.store)
        return self._llm

    def next_week_start(self) -> date:
        today = datetime.now(self.tz).date()
        return today + timedelta(days=(7 - today.weekday()) % 7 or 7)

    @staticmethod
    def week_key(d: date) -> str:
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"

    # ------------------------------------------------------------------ 1-4
    def run_week(self, week_start: date | None = None, this_week: bool = False) -> str:
        if this_week:
            week_start = datetime.now(self.tz).date()
        week_start = week_start or self.next_week_start()
        wk = self.week_key(week_start)
        n = self.brand.get("weekly_post_count", 5)
        lessons = self.store.recent_lessons()

        print(f"▶ Hafta {wk} ({week_start}) için {n} post hazırlanıyor")

        # 1) Araştırma (haftada bir, cache'li)
        res = self.store.research(wk)
        if res is None:
            print("  1/5 Araştırma ajanı çalışıyor (web araması)...")
            res = agents.research(self.llm, lessons)
            self.store.save_research(wk, res)
        else:
            print("  1/5 Bu haftanın araştırması zaten var, tekrar kullanılıyor.")

        # 2) Strateji + içerik (tek çağrı)
        print("  2/5 İçerik ajanı haftayı planlıyor...")
        assets = media.list_assets(self.assets_dir)
        plan = agents.plan_week(self.llm, res, lessons, assets, n)

        # 3) Saatler (kod) + kayıt
        slots = scheduler.plan_slots(len(plan), self.brand, self.store.metrics(), week_start)
        ids = []
        for item, slot in zip(plan, slots):
            if item.get("source_asset") and item["source_asset"] not in (
                    assets["photos"] + assets["videos"]):
                item["source_asset"] = None  # uydurma dosya adına karşı koruma
            if item.get("template") == "reel" and not item.get("source_asset"):
                item["template"] = "text_card"
            ids.append(self.store.add_post(
                week=wk, scheduled_at=slot.isoformat(),
                **{k: item.get(k) for k in ("pillar", "template", "headline", "subline",
                                            "caption", "hashtags", "visual_brief",
                                            "source_asset")}))

        # 4) Görsel üretimi (kod)
        print("  3/5 Görseller şablonlardan üretiliyor...")
        for pid in ids:
            self._render(pid)

        # 5) Denetim döngüsü
        print("  4/5 Denetim ajanı kontrol ediyor...")
        self.review_loop(ids)

        ok = len([p for p in self.store.posts(week=wk) if p["status"] == "reviewed_ok"])
        print(f"  5/5 Bitti: {ok}/{len(ids)} post onayını bekliyor → `python main.py onay`")
        return wk

    def _render(self, pid: int) -> None:
        p = self._get(pid)
        path = media.render(p, self.brand, self.assets_dir, self.out_dir)
        self.store.update_post(pid, media_path=path, status="rendered")

    def _get(self, pid: int) -> dict:
        return next(p for p in self.store.posts() if p["id"] == pid)

    def review_loop(self, ids: list[int]) -> None:
        pending = list(ids)
        for round_no in range(MAX_REVISIONS + 1):
            if not pending:
                return
            posts = [self._get(i) for i in pending]

            # a) ücretsiz kural kontrolü
            rule_issues = {p["id"]: agents.rule_check(p, self.brand) for p in posts}
            # b) tek LLM çağrısı ile tüm postlar
            verdicts = {v["id"]: v for v in agents.review_batch(self.llm, posts)}

            next_round = []
            for p in posts:
                v = verdicts.get(p["id"], {"verdict": "fix", "feedback": "Denetçi yanıt vermedi."})
                issues = rule_issues[p["id"]]
                if issues and v["verdict"] == "ok":
                    v["verdict"] = "fix"
                v["rule_issues"] = issues

                if v["verdict"] == "ok":
                    self.store.update_post(p["id"], status="reviewed_ok", review=v)
                elif v["verdict"] == "reject" or round_no == MAX_REVISIONS:
                    self.store.update_post(p["id"], status="needs_human", review=v)
                else:
                    fb = "\n".join([v.get("feedback", ""), *issues,
                                    *v.get("language_issues", []), *v.get("compliance_issues", [])])
                    fixed = agents.revise(self.llm, p, fb)
                    self.store.update_post(p["id"], revisions=p["revisions"] + 1, review=v,
                                           **{k: fixed[k] for k in
                                              ("headline", "subline", "caption", "hashtags")
                                              if k in fixed})
                    self._render(p["id"])
                    next_round.append(p["id"])
            pending = next_round

    # ------------------------------------------------------------------ yayın
    def publish_due(self, dry_run: bool = False) -> None:
        ig = Instagram(dry_run=dry_run)
        now = datetime.now(self.tz).isoformat()
        due = self.store.due_posts(now)
        if not due:
            print("Zamanı gelen post yok.")
        for p in due:
            caption = f"{p['caption']}\n\n{p['hashtags'] or ''}".strip()
            try:
                mid = ig.publish(p["media_path"], caption, is_video=p["template"] == "reel")
                self.store.update_post(p["id"], status="published", ig_media_id=mid,
                                       published_at=datetime.now(self.tz).isoformat())
                print(f"✓ Post {p['id']} yayınlandı ({mid})")
            except Exception as e:  # noqa: BLE001
                self.store.update_post(p["id"], status="failed", error=str(e))
                print(f"✗ Post {p['id']} yayınlanamadı: {e}")

    # ------------------------------------------------------------------ analiz
    def analyze(self) -> dict | None:
        ig = Instagram()
        for m in ig.recent_media_metrics():
            self.store.upsert_metric(m)
        metrics = self.store.metrics()
        if len(metrics) < 3:
            print("Analiz için en az 3 yayınlanmış post gerekli.")
            return None
        result = agents.analyze(self.llm, metrics)
        text = "\n".join(f"- {x}" for x in result.get("lessons", []))
        if result.get("notes_on_timing"):
            text += f"\n- Zamanlama: {result['notes_on_timing']}"
        self.store.add_lesson(self.week_key(datetime.now(self.tz).date()), text)
        return result
