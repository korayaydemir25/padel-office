"""Padel Office — komut satırı.

  python main.py hafta            # araştır → planla → görsel → denetle (haftada 1)
  python main.py onizle           # haftanın postlarını HTML önizleme olarak açar
  python main.py onay             # postları tek tek onayla / düzelt / reddet
  python main.py onay --hepsi     # denetimden geçenlerin hepsini onayla
  python main.py yayinla          # zamanı gelen onaylı postları yayınla (cron: 15 dk'da bir)
  python main.py analiz           # metrikleri çek, ders çıkar (haftada 1)
  python main.py durum            # post durumları ve token kullanımı
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from office.pipeline import Office

ROOT = Path(__file__).parent


def cmd_onay(office: Office, all_ok: bool) -> None:
    posts = office.store.posts("reviewed_ok") + office.store.posts("needs_human")
    if not posts:
        print("Onay bekleyen post yok.")
        return
    for p in posts:
        if all_ok:
            if p["status"] == "reviewed_ok":
                office.store.update_post(p["id"], status="approved")
                print(f"✓ {p['id']} onaylandı")
            continue
        review = json.loads(p["review"] or "{}")
        print("\n" + "=" * 70)
        print(f"#{p['id']}  {p['scheduled_at']}  [{p['template']}]  {p['pillar']}")
        if p["status"] == "needs_human":
            print("⚠ DENETÇİ İNSAN KARARI İSTİYOR:", review.get("feedback", ""))
        print(f"Başlık : {p['headline']}\nAlt    : {p['subline']}")
        print(f"Metin  :\n{p['caption']}\n{p['hashtags']}")
        print(f"Dosya  : {p['media_path']}")
        ch = input("[e]vet onayla / [h]ayır reddet / [m]etni düzelt / [s]aat değiştir / [g]eç : ")
        ch = ch.strip().lower()
        if ch == "e":
            office.store.update_post(p["id"], status="approved")
        elif ch == "h":
            office.store.update_post(p["id"], status="rejected")
        elif ch == "m":
            new = input("Yeni metin (tek satır, boş bırak = değiştirme): ").strip()
            if new:
                office.store.update_post(p["id"], caption=new, status="approved")
        elif ch == "s":
            new = input("Yeni zaman (ör. 2026-11-03T19:30:00+03:00): ").strip()
            if new:
                office.store.update_post(p["id"], scheduled_at=new, status="approved")


def cmd_onizle(office: Office) -> None:
    posts = [p for p in office.store.posts() if p["status"] not in ("published", "rejected")]
    cards = []
    for p in posts:
        mp = Path(p["media_path"] or "")
        rel = mp.name
        media_tag = (f'<video src="{rel}" controls></video>' if mp.suffix == ".mp4"
                     else f'<img src="{rel}">')
        cards.append(f"""<div class="c"><div class="m">{media_tag}</div>
<div class="t"><b>#{p['id']} · {html.escape(p['scheduled_at'] or '')}</b>
<span class="s {p['status']}">{p['status']}</span>
<p>{html.escape(p['caption'] or '').replace(chr(10), '<br>')}</p>
<small>{html.escape(p['hashtags'] or '')}</small></div></div>""")
    page = f"""<!doctype html><meta charset="utf-8"><title>Haftalık Önizleme</title>
<style>body{{font-family:system-ui;background:#f4f4f2;margin:0;padding:24px}}
.c{{display:flex;gap:20px;background:#fff;border-radius:12px;padding:16px;margin:0 auto 16px;max-width:900px}}
.m img,.m video{{width:300px;border-radius:8px}} .t{{flex:1}} p{{white-space:normal}}
.s{{margin-left:8px;padding:2px 8px;border-radius:99px;background:#eee;font-size:12px}}
.reviewed_ok,.approved{{background:#d6f5c8}} .needs_human{{background:#ffe0b3}}
@media(max-width:700px){{.c{{flex-direction:column}} .m img,.m video{{width:100%}}}}</style>
<h2 style="text-align:center">Haftalık İçerik Önizleme</h2>{''.join(cards)}"""
    out = Path(office.out_dir) / "onizleme.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"Önizleme: {out}")


def cmd_durum(office: Office) -> None:
    from collections import Counter
    c = Counter(p["status"] for p in office.store.posts())
    print("Postlar:", dict(c) or "yok")
    print("\nToken kullanımı (ajan bazında):")
    for u in office.store.usage_summary():
        print(f"  {u['agent']:<16} {u['model']:<20} çağrı={u['calls']:<4} "
              f"girdi={u['inp']:<8} çıktı={u['out']:<7} cache={u['cache_read']:<8} "
              f"arama={u['searches']}")


def main() -> None:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="Padel Club sosyal medya ajan ofisi")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hafta"); h.add_argument("--bu-hafta", action="store_true",
                                                help="Önümüzdeki hafta yerine bugünden başla")
    sub.add_parser("onizle")
    o = sub.add_parser("onay"); o.add_argument("--hepsi", action="store_true")
    y = sub.add_parser("yayinla"); y.add_argument("--dry", action="store_true")
    sub.add_parser("analiz")
    sub.add_parser("durum")
    args = ap.parse_args()

    office = Office(ROOT)
    if args.cmd == "hafta":
        office.run_week(this_week=args.bu_hafta)
        cmd_onizle(office)
    elif args.cmd == "onizle":
        cmd_onizle(office)
    elif args.cmd == "onay":
        cmd_onay(office, args.hepsi)
    elif args.cmd == "yayinla":
        office.publish_due(dry_run=args.dry)
    elif args.cmd == "analiz":
        r = office.analyze()
        if r:
            print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.cmd == "durum":
        cmd_durum(office)


if __name__ == "__main__":
    sys.exit(main())
