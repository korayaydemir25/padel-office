# Padel Office — Sosyal Medya Ajan Ofisi

Bir padel kulübünün Instagram hesabını yöneten, maliyeti optimize edilmiş çok ajanlı sistem.

## Günlük mod (şu an aktif olan)

Her gün zamanlanmış görev çalışır → Claude günün postunu yazar ve denetler → `daily.py` görseli üretir → görsel telefonuna bildirimle gelir → sen onaylarsın → onay `queue/` klasörüne commit'lenir → **GitHub Actions** (`publish.py`) postu belirlenen saatte Instagram'a yayınlar ve durumu repo'ya geri yazar.

- Bu modda ayrı bir Anthropic API anahtarı gerekmez.
- Instagram bilgileri yalnızca **GitHub Secrets**'ta durur: `IG_ACCESS_TOKEN`, `IG_USER_ID`. Instagram ile giriş tipi bir token kullanıyorsan Variables'a `IG_API_HOST=graph.instagram.com` ekle.
- Görseller Instagram'a repo'nun herkese açık adresinden verilir; bu yüzden repo **public** olmalı. Gizli bilgi repo'da değil, Secrets'ta durur.
- Elle çalıştırma: Actions sekmesi → "Instagram yayın" → Run workflow.

## Optimizasyon fikri: 12 ajan değil, 4 akıllı ajan + kod

Orijinal fikirdeki 8-13 ajanın işi korunuyor ama her işi **onu en ucuza yapan şey** üstleniyor:

| Orijinal rol | Burada kim yapıyor | Neden |
|---|---|---|
| 2 araştırma ajanı | **1 Araştırma ajanı** (Sonnet + web araması), tek çağrıda iki mercek: trend + rakip | Aynı işi yapan 2 ajan = 2 kat fatura |
| 2 içerik ajanı | **1 İçerik ajanı** (Sonnet) haftanın tüm postlarını TEK çağrıda yazar + **şablon motoru** (Python/Pillow, ffmpeg) görselleri üretir | Görsel üretim bedava ve marka tutarlı; gerçek kulüp fotoğrafları daha çok etkileşim alır |
| 2 denetim ajanı | **Kural motoru** (bedava: yasaklı kelime, uzunluk, hashtag, emoji) + **1 Denetim ajanı** (Haiku, görseli de görür) tüm postlara TEK çağrıda bakar | Ucuz model yeterli, toplu inceleme çağrı sayısını düşürür |
| 2 yayın ajanı | **Zamanlayıcı + Instagram API** (sıfır LLM) | Saat seçmek ve paylaşmak karar değil, işlem |
| (yeni) Yönetici | **Python orkestratör** | Akışı yönetmek için LLM gerekmez |
| (yeni) Analiz | **1 Analiz ajanı** (Haiku) haftalık metrikleri derse çevirir; dersler sonraki haftanın araştırmasına girer | Sistemi zamanla akıllandıran döngü |

Ek tasarruflar: marka rehberi **prompt cache** ile tüm ajanlarda paylaşılır; araştırma haftada bir kez yapılır ve önbelleğe alınır; denetim en fazla 2 düzeltme turu yapar, sonra karar sana kalır.

**Haftalık tipik maliyet:** ~5-7 API çağrısı + ≤8 web araması. Gerçek token sayılarını `python main.py durum` gösterir.

## Akış

```
Pazartesi (cron)        Sen (5 dk)          Her 15 dk (cron)        Pazar (cron)
python main.py hafta → python main.py onay → python main.py yayinla → python main.py analiz
 ├ Araştırma (web)       onizleme.html'e bak   zamanı gelenleri        metrikleri çek
 ├ İçerik planı          onayla/düzelt/reddet   Instagram'a gönder      dersleri kaydet ──┐
 ├ Saat seçimi (kod)                                                                      │
 ├ Görsel üretimi (kod)  ◄────────────── gelecek haftanın araştırmasına girer ────────────┘
 └ Denetim + düzeltme
```

## Kurulum

```bash
cd padel_office
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # ANTHROPIC_API_KEY'i yaz
```

1. `config/brand.yaml` dosyasını kendi kulübüne göre doldur (ton, renkler, hashtag'ler, yasaklı ifadeler, saat pencereleri).
2. Kulübün fotoğraf ve videolarını `assets/` klasörüne koy (açıklayıcı isimlerle: `aksam_maci_kort2.jpg`, `ders_cocuklar.mp4`). Logo varsa `assets/logo.png`.
3. Deneme: `python main.py hafta --bu-hafta` → `output/onizleme.html` dosyasını aç.

Instagram bilgileri boşsa `yayinla` komutu **DRY-RUN** modunda çalışır, hiçbir şey paylaşılmaz. Gerçek yayın için:
- Instagram Business/Creator hesabı + bağlı Facebook sayfası
- Meta Developer'dan uzun ömürlü token (`instagram_basic`, `instagram_content_publish`, `instagram_manage_insights`)
- `output/` klasörünün herkese açık bir URL'de olması (S3, Cloudinary, kendi siten) → `PUBLIC_MEDIA_BASE_URL`

## Otomasyon (cron örneği, Linux/Mac)

```cron
0 9 * * 1     cd /yol/padel_office && .venv/bin/python main.py hafta
*/15 * * * *  cd /yol/padel_office && .venv/bin/python main.py yayinla
0 21 * * 0    cd /yol/padel_office && .venv/bin/python main.py analiz
```

Onay adımı bilerek manuel bırakıldı. Sisteme güvendiğinde `python main.py onay --hepsi` komutunu cron'a ekleyerek tam otomatiğe geçebilirsin; denetçinin "insan kararı" dediği postlar yine bekler.

## Dosyalar

```
config/brand.yaml     marka rehberi (tüm ajanların ortak hafızası)
office/agents.py      araştırma, içerik, denetim, analiz ajanlarının promptları
office/pipeline.py    yönetici / iş akışı
office/media.py       görsel şablonları ve reel kesimi
office/scheduler.py   en iyi saat seçimi (veriden öğrenir)
office/instagram.py   yayın ve metrik çekme
office/store.py       SQLite iş panosu (data/office.db)
main.py               komutlar
```

## Sonraki adımlar
- Yorum/DM cevap taslağı ajanı (Haiku) eklenebilir.
- Telegram/WhatsApp üzerinden onay (telefondan "evet" demek).
- TikTok için ikinci yayın kanalı.
