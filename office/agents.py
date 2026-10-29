"""LLM ajanları. Her ajan tek bir görev için tek (veya çok az) API çağrısı yapar.

Maliyet kuralları:
  * Araştırma: haftada 1 çağrı, web araması sınırlı (max_uses).
  * Strateji + metin yazarlığı TEK çağrıda: haftanın tüm postları birlikte planlanır.
  * Denetim: önce ücretsiz kural kontrolü, sonra tüm postlar TEK hızlı-model çağrısında.
  * Yayın ve saat seçimi LLM kullanmaz.
"""
from __future__ import annotations

import json
import re

from .llm import MODEL_FAST, MODEL_SMART, WEB_SEARCH_TOOL, image_block

# ---------------------------------------------------------------------------
# 1) ARAŞTIRMA AJANI  (senin "2 araştırmacı" fikrin → tek çağrıda iki mercek)
# ---------------------------------------------------------------------------
RESEARCH_PROMPT = """Sen bir padel kulübünün sosyal medya araştırmacısısın.
Görevin: bu hafta için EN ÇOK ETKİLEŞİM ALAN içerik fikirlerini bulmak.
İki mercekle çalış:
  A) Trend merceği: padel ve spor kulüplerinde son 2-3 haftada öne çıkan reel/post
     formatları, sesler, akımlar, hook (açılış) kalıpları.
  B) Rakip merceği: marka rehberindeki rakip hesapların ve benzer kulüplerin en çok
     etkileşim alan içerik türleri.
Kurallar: Az ama isabetli arama yap. Uydurma istatistik yazma; emin değilsen
"tahmini" de. Kopyalanacak içerik değil, uyarlanacak FORMAT öner.
Çıktın SADECE şu JSON olsun:
{"trends":[{"format":"...","why_it_works":"...","example_hook":"...","fit_score":1-10}],
 "competitor_insights":["..."],
 "avoid":["bu hafta işe yaramayan/riskli şeyler"]}"""


def research(llm, lessons: str) -> dict:
    user = f"Geçmiş performanstan dersler:\n{lessons}\n\nBu haftanın araştırmasını yap."
    return llm.ask_json("arastirma", RESEARCH_PROMPT, user, model=MODEL_SMART,
                        tools=[WEB_SEARCH_TOOL], max_tokens=3000)


# ---------------------------------------------------------------------------
# 2) STRATEJİ + İÇERİK AJANI  (senin "2 içerik hazırlayıcı" fikrin)
# ---------------------------------------------------------------------------
PLAN_PROMPT = """Sen padel kulübünün içerik direktörü ve metin yazarısın.
Araştırmayı, geçmiş dersleri ve eldeki medya dosyalarını kullanarak haftanın
postlarını planla. İçerikleri marka rehberindeki content_pillars arasında dağıt.

Şablonlar (görseller kodla üretilir, sen sadece seç ve metni yaz):
  - "photo_overlay": kulüp fotoğrafı + alt kısımda başlık. source_asset bir FOTOĞRAF olmalı.
  - "text_card": marka renginde düz zemin + büyük başlık (ipucu, alıntı, duyuru).
  - "event": tarih rozetli duyuru kartı (turnuva, ders, kampanya). subline'a tarih/saat yaz.
  - "reel": kısa video. source_asset bir VİDEO olmalı; video yoksa reel önerme.

Kurallar:
  - headline en fazla 40 karakter, subline en fazla 60 karakter.
  - caption ilk satırı güçlü bir hook olsun; en sona rezervasyon/CTA ekle.
  - hashtags: rehberdeki "always" etiketleri + havuzdan seç, max_per_post'u aşma.
  - Var olmayan dosya adı uydurma; sadece verilen listeden seç ya da null bırak.
Çıktın SADECE JSON dizi olsun:
[{"pillar":"...","template":"photo_overlay|text_card|event|reel","headline":"...",
  "subline":"...","caption":"...","hashtags":["#..."],"source_asset":"dosya veya null",
  "visual_brief":"görselin neyi anlatması gerektiği, 1 cümle"}]"""


def plan_week(llm, research_data: dict, lessons: str, assets: dict, n_posts: int) -> list[dict]:
    user = (
        f"Hafta için {n_posts} post planla.\n\n"
        f"ARAŞTIRMA:\n{json.dumps(research_data, ensure_ascii=False)}\n\n"
        f"GEÇMİŞ DERSLER:\n{lessons}\n\n"
        f"ELDEKİ FOTOĞRAFLAR: {assets['photos'] or 'yok'}\n"
        f"ELDEKİ VİDEOLAR: {assets['videos'] or 'yok'}"
    )
    return llm.ask_json("icerik", PLAN_PROMPT, user, model=MODEL_SMART, max_tokens=6000)


REVISE_PROMPT = """Sen metin yazarısın. Denetçinin geri bildirimine göre postu düzelt.
Sadece gereken alanları değiştir. Çıktın SADECE aynı yapıda tek bir JSON nesnesi olsun:
{"headline":"...","subline":"...","caption":"...","hashtags":["#..."]}"""


def revise(llm, post: dict, feedback: str) -> dict:
    user = (f"POST:\n{json.dumps(_post_view(post), ensure_ascii=False)}\n\n"
            f"DENETÇİ GERİ BİLDİRİMİ:\n{feedback}")
    return llm.ask_json("icerik_duzeltme", REVISE_PROMPT, user, model=MODEL_SMART, max_tokens=1500)


# ---------------------------------------------------------------------------
# 3) DENETİM AJANI  (senin "2 denetçi" fikrin → kural motoru + hızlı model)
# ---------------------------------------------------------------------------
def rule_check(post: dict, brand: dict) -> list[str]:
    """Ücretsiz, deterministik kontroller. LLM'e gitmeden önce elenir."""
    issues = []
    caption = post.get("caption") or ""
    tags = (post.get("hashtags") or "").split()
    text_all = " ".join([post.get("headline") or "", post.get("subline") or "", caption]).lower()

    if len(caption) > 2200:
        issues.append("Caption 2200 karakter sınırını aşıyor.")
    if len(post.get("headline") or "") > 40:
        issues.append("Başlık 40 karakterden uzun, görsele sığmaz.")
    if len(post.get("subline") or "") > 60:
        issues.append("Alt başlık 60 karakterden uzun.")
    if len(tags) > brand["hashtags"]["max_per_post"]:
        issues.append(f"Hashtag sayısı {len(tags)}, sınır {brand['hashtags']['max_per_post']}.")
    for must in brand["hashtags"]["always"]:
        if must.lower() not in [t.lower() for t in tags]:
            issues.append(f"Zorunlu hashtag eksik: {must}")
    for w in brand.get("banned_words", []):
        if w.lower() in text_all:
            issues.append(f"Yasaklı ifade: '{w}'")
    emoji = re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", caption)
    if len(emoji) > 3:
        issues.append(f"{len(emoji)} emoji var, rehber en fazla 3 diyor.")
    if post.get("template") == "reel" and not post.get("media_path"):
        issues.append("Reel için video dosyası yok.")
    return issues


REVIEW_PROMPT = """Sen padel kulübünün yayın öncesi denetçisisin. Her postu iki açıdan incele:
  1) DİL: yazım, noktalama, Türkçe karakter, anlam bütünlüğü, hook gücü.
  2) UYGUNLUK: marka tonu, etik (yanıltıcı vaat, ayrımcılık, sağlık iddiası, telif riski,
     izinsiz kişi/çocuk görüntüsü şüphesi), platform kurallarına uygunluk,
     görselin metinle uyumu ve okunabilirliği.
Ciddiyet: "ok" = yayına hazır, "fix" = küçük düzeltme gerekli, "reject" = yayınlanmamalı.
Çıktın SADECE JSON dizi olsun, her post için:
[{"id":<post id>,"verdict":"ok|fix|reject","language_issues":["..."],
  "compliance_issues":["..."],"feedback":"metin yazarına somut talimat"}]"""


def review_batch(llm, posts: list[dict]) -> list[dict]:
    content = [{"type": "text", "text": "Aşağıdaki postları denetle."}]
    for p in posts:
        content.append({"type": "text",
                        "text": f"--- POST id={p['id']} ---\n"
                                f"{json.dumps(_post_view(p), ensure_ascii=False)}"})
        if p.get("media_path") and p["media_path"].lower().endswith((".jpg", ".jpeg", ".png")):
            content.append(image_block(p["media_path"]))
    return llm.ask_json("denetim", REVIEW_PROMPT, content, model=MODEL_FAST, max_tokens=3000)


# ---------------------------------------------------------------------------
# 4) ANALİZ AJANI  (döngüyü kapatır: sonuçları derse çevirir)
# ---------------------------------------------------------------------------
ANALYST_PROMPT = """Sen sosyal medya analistisin. Verilen post metriklerinden
gelecek haftanın araştırma ve içerik ajanları için kısa, uygulanabilir dersler çıkar.
Etkileşim oranı = (beğeni + yorum + kaydetme + paylaşım) / erişim.
Çıktın SADECE JSON olsun:
{"lessons":["en fazla 6 madde, somut"],
 "best_formats":["..."],"worst_formats":["..."],
 "notes_on_timing":"saat/gün gözlemi, veri azsa bunu söyle"}"""


def analyze(llm, metrics: list[dict]) -> dict:
    slim = [{k: m.get(k) for k in ("posted_at", "media_type", "reach", "likes", "comments",
                                   "saved", "shares", "caption")} for m in metrics[:40]]
    for s in slim:
        s["caption"] = (s["caption"] or "")[:150]
    return llm.ask_json("analiz", ANALYST_PROMPT, json.dumps(slim, ensure_ascii=False),
                        model=MODEL_FAST, max_tokens=1500)


def _post_view(p: dict) -> dict:
    return {k: p.get(k) for k in ("pillar", "template", "headline", "subline", "caption",
                                  "hashtags", "visual_brief", "scheduled_at")}
