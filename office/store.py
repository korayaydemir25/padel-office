"""SQLite tabanlı ortak hafıza: ajanların paylaştığı iş panosu."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week TEXT, pillar TEXT, media_type TEXT, template TEXT,
    headline TEXT, subline TEXT, caption TEXT, hashtags TEXT,
    visual_brief TEXT, source_asset TEXT, media_path TEXT,
    scheduled_at TEXT, status TEXT DEFAULT 'draft',
    review TEXT, revisions INTEGER DEFAULT 0,
    ig_media_id TEXT, published_at TEXT, error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS research (
    week TEXT PRIMARY KEY, data TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS metrics (
    ig_media_id TEXT PRIMARY KEY, posted_at TEXT, media_type TEXT,
    reach INTEGER, likes INTEGER, comments INTEGER, saved INTEGER, shares INTEGER,
    caption TEXT, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT, week TEXT, text TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent TEXT, model TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cache_read INTEGER,
    cache_write INTEGER, searches INTEGER
);
"""

# Statü akışı: draft → rendered → reviewed_ok / needs_fix → approved → published
#                                                   ↘ rejected / failed


class Store:
    def __init__(self, path: str | Path = "data/office.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- posts -------------------------------------------------------------
    def add_post(self, **f) -> int:
        if isinstance(f.get("hashtags"), list):
            f["hashtags"] = " ".join(f["hashtags"])
        cols = ", ".join(f)
        cur = self.db.execute(f"INSERT INTO posts ({cols}) VALUES ({', '.join('?' * len(f))})",
                              list(f.values()))
        self.db.commit()
        return cur.lastrowid

    def update_post(self, pid: int, **f) -> None:
        if isinstance(f.get("hashtags"), list):
            f["hashtags"] = " ".join(f["hashtags"])
        if isinstance(f.get("review"), (dict, list)):
            f["review"] = json.dumps(f["review"], ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in f)
        self.db.execute(f"UPDATE posts SET {sets} WHERE id=?", [*f.values(), pid])
        self.db.commit()

    def posts(self, status: str | None = None, week: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM posts WHERE 1=1", []
        if status:
            q += " AND status=?"; args.append(status)
        if week:
            q += " AND week=?"; args.append(week)
        return [dict(r) for r in self.db.execute(q + " ORDER BY scheduled_at", args)]

    def due_posts(self, now_iso: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM posts WHERE status='approved' AND scheduled_at<=? ORDER BY scheduled_at",
            [now_iso])
        return [dict(r) for r in rows]

    # --- research / lessons -----------------------------------------------
    def save_research(self, week: str, data) -> None:
        self.db.execute("INSERT OR REPLACE INTO research (week, data) VALUES (?, ?)",
                        [week, json.dumps(data, ensure_ascii=False)])
        self.db.commit()

    def research(self, week: str):
        r = self.db.execute("SELECT data FROM research WHERE week=?", [week]).fetchone()
        return json.loads(r["data"]) if r else None

    def add_lesson(self, week: str, text: str) -> None:
        self.db.execute("INSERT INTO lessons (week, text) VALUES (?, ?)", [week, text])
        self.db.commit()

    def recent_lessons(self, n: int = 3) -> str:
        rows = self.db.execute("SELECT week, text FROM lessons ORDER BY id DESC LIMIT ?", [n])
        return "\n\n".join(f"[{r['week']}] {r['text']}" for r in rows) or "Henüz veri yok."

    # --- metrics ----------------------------------------------------------
    def upsert_metric(self, m: dict) -> None:
        m = {**m, "fetched_at": datetime.utcnow().isoformat()}
        cols = ", ".join(m)
        self.db.execute(f"INSERT OR REPLACE INTO metrics ({cols}) VALUES ({', '.join('?' * len(m))})",
                        list(m.values()))
        self.db.commit()

    def metrics(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM metrics ORDER BY posted_at DESC")]

    # --- usage ------------------------------------------------------------
    def log_usage(self, **u) -> None:
        u["ts"] = datetime.utcnow().isoformat()
        cols = ", ".join(u)
        self.db.execute(f"INSERT INTO usage ({cols}) VALUES ({', '.join('?' * len(u))})",
                        list(u.values()))
        self.db.commit()

    def usage_summary(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT agent, model, COUNT(*) calls, SUM(input_tokens) inp, SUM(output_tokens) out, "
            "SUM(cache_read) cache_read, SUM(searches) searches FROM usage GROUP BY agent, model")
        return [dict(r) for r in rows]
