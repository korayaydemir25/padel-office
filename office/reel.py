"""Padel Club Ankara marka stilinde hareketli reels üreticisi (1080x1920, 30 fps).

Kare kare Pillow ile çizilir, ffmpeg ile MP4'e dönüştürülür. Müzik telifsizdir:
numpy ile üretilen basit bir ritim (120 BPM); sahne geçişleri vuruşlara oturur.

Kullanım:
    python -m office.reel                  # varsayılan "2026'da neler yaptık" reels'i
    python -m office.reel --out queue/x.mp4
"""
from __future__ import annotations

import argparse
import math
import subprocess
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
IG = ROOT / "assets" / "ig"
FONTS = ROOT / "assets" / "fonts"

W, H, FPS = 1080, 1920, 30
BPM = 120
BEAT = 60 / BPM

# Marka paleti (Instagram sayfasından çıkarıldı)
PINK = (240, 68, 155)
PINK_LIGHT = (252, 217, 234)
GREEN = (14, 61, 42)
GREEN_DARK = (8, 36, 25)
LIME = (212, 227, 15)
CREAM = (246, 241, 230)
WHITE = (255, 255, 255)
INK = (20, 20, 20)


# ---------------------------------------------------------------- yardımcılar
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def prog(t, start, dur):
    return clamp((t - start) / dur)


def ease_out(x):
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_back(x, s=1.7):
    x = x - 1
    return x * x * ((s + 1) * x + s) + 1


@lru_cache(maxsize=None)
def font(name: str, size: int):
    files = {"head": "Anton-Regular.ttf", "bold": "Baloo2-ExtraBold.ttf",
             "semi": "Baloo2-SemiBold.ttf"}
    return ImageFont.truetype(str(FONTS / files[name]), size)


@lru_cache(maxsize=None)
def photo(name: str) -> Image.Image:
    return Image.open(IG / name).convert("RGB")


@lru_cache(maxsize=None)
def logo(size: int) -> Image.Image:
    src = photo("logo_pembe.png").resize((size * 2, size * 2), Image.LANCZOS)
    src = src.filter(ImageFilter.UnsharpMask(2, 80, 2)).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse([6, 6, size * 4 - 6, size * 4 - 6], fill=255)
    out = src.convert("RGBA")
    out.putalpha(mask.resize((size, size), Image.LANCZOS))
    return out


def text_layer(txt, fnt, fill, stroke=0, stroke_fill=None, spacing=0):
    d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = d.multiline_textbbox((0, 0), txt, font=fnt, stroke_width=stroke, spacing=spacing,
                               align="center")
    box = [int(math.floor(box[0])), int(math.floor(box[1])), int(math.ceil(box[2])),
           int(math.ceil(box[3]))]
    im = Image.new("RGBA", (box[2] - box[0] + 4, box[3] - box[1] + 4), (0, 0, 0, 0))
    ImageDraw.Draw(im).multiline_text((-box[0] + 2, -box[1] + 2), txt, font=fnt, fill=fill,
                                      stroke_width=stroke, stroke_fill=stroke_fill,
                                      spacing=spacing, align="center")
    return im


def paste(canvas, layer, cx, cy, alpha=1.0, scale=1.0, angle=0.0):
    """Katmanı (cx, cy) merkezine; ölçek, açı ve saydamlıkla yapıştırır."""
    if alpha <= 0 or scale <= 0:
        return
    if scale != 1.0:
        layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))),
                             Image.BICUBIC)
    if angle:
        layer = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    if alpha < 1:
        a = layer.getchannel("A").point(lambda v: int(v * alpha))
        layer = layer.copy()
        layer.putalpha(a)
    canvas.alpha_composite(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)))


def rise(canvas, layer, cx, cy, t, start, dur=0.45, dist=80):
    """Aşağıdan kayarak beliren yazı."""
    p = ease_out(prog(t, start, dur))
    paste(canvas, layer, cx, cy + (1 - p) * dist, alpha=p)


def pill(txt, fnt, bg, fg, pad=(34, 14)):
    t = text_layer(txt, fnt, fg)
    im = Image.new("RGBA", (t.width + pad[0] * 2, t.height + pad[1] * 2), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, im.width - 1, im.height - 1],
                                         radius=im.height // 2, fill=bg)
    im.alpha_composite(t, (pad[0], pad[1]))
    return im


def card(name, w, h, zoom=1.0, border=14, radius=34, focus=(0.5, 0.5), crop=None):
    """Fotoğrafı beyaz çerçeveli, gölgeli, yuvarlak köşeli karta çevirir (Ken Burns zoom).
    crop=(x0, y0, x1, y1) verilirse önce o bölge kesilir (ör. afişteki bir bilgiyi gizlemek için)."""
    src = photo(name)
    if crop:
        src = src.crop(crop)
    target = w / h
    sw, sh = src.size
    if sw / sh > target:
        cw, ch = sh * target, sh
    else:
        cw, ch = sw, sw / target
    cw, ch = cw / zoom, ch / zoom
    x0 = (sw - cw) * focus[0]
    y0 = (sh - ch) * focus[1]
    img = src.resize((w, h), Image.LANCZOS, box=(x0, y0, x0 + cw, y0 + ch))
    img = img.filter(ImageFilter.UnsharpMask(1.6, 70, 2))

    cw2, ch2 = w + border * 2, h + border * 2
    pad = 40
    out = Image.new("RGBA", (cw2 + pad * 2, ch2 + pad * 2), (0, 0, 0, 0))
    sh_ = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh_).rounded_rectangle([pad, pad + 14, pad + cw2, pad + ch2 + 14],
                                          radius=radius + border, fill=(0, 0, 0, 90))
    out.alpha_composite(sh_.filter(ImageFilter.GaussianBlur(18)))
    ImageDraw.Draw(out).rounded_rectangle([pad, pad, pad + cw2, pad + ch2],
                                          radius=radius + border, fill=WHITE)
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    out.paste(img, (pad + border, pad + border), m)
    return out


def ball(size):
    im = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = size * 2
    d.ellipse([0, 0, s - 1, s - 1], fill=LIME)
    w = max(3, s // 18)
    d.arc([-s * 0.55, s * 0.08, s * 0.45, s * 0.92], -50, 50, fill=WHITE, width=w)
    d.arc([s * 0.55, s * 0.08, s * 1.55, s * 0.92], 130, 230, fill=WHITE, width=w)
    return im.resize((size, size), Image.LANCZOS)


def bouncing_ball(canvas, t, x0, x1, y_floor, height, t0, dur, size=70, bounces=3):
    p = prog(t, t0, dur)
    if p <= 0 or p >= 1:
        return
    x = x0 + (x1 - x0) * p
    ph = (p * bounces) % 1
    decay = 0.75 ** int(p * bounces)
    y = y_floor - 4 * height * decay * ph * (1 - ph)
    paste(canvas, ball(size), x, y, angle=-p * 720)


def bg(color):
    return Image.new("RGBA", (W, H), color + (255,))


def big_circle(canvas, cx, cy, r, color):
    if r > 0:
        ImageDraw.Draw(canvas).ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def number_tag(canvas, t, start, num, bgc, fg):
    tag = pill(num, font("head", 54), bgc, fg, pad=(30, 10))
    p = ease_back(prog(t, start, 0.4))
    paste(canvas, tag, 90 + tag.width / 2, 180, scale=max(0.01, p))


# ---------------------------------------------------------------- sahneler
# Her sahne fonksiyonu sahne içi zamanı (lt, saniye) alır.

def s_intro(lt, D):
    c = bg(PINK)
    big_circle(c, W / 2, H * 0.47, ease_out(prog(lt, 0, 0.7)) * 640, PINK_LIGHT + (255,))
    paste(c, logo(330), W / 2, 560, scale=max(0.01, ease_back(prog(lt, 0.2, 0.6))),
          angle=(1 - ease_out(prog(lt, 0.2, 0.6))) * 90)
    rise(c, text_layer("2026'DA", font("head", 150), PINK), W / 2, 920, lt, 0.7)
    rise(c, text_layer("NELER\nYAPTIK?", font("head", 230), GREEN, spacing=-10), W / 2, 1230, lt, 0.9)
    bouncing_ball(c, lt, -60, W + 60, 1700, 260, 0.6, 2.4, size=86)
    return c


def s_topluluk(lt, D):
    c = bg(CREAM)
    big_circle(c, W * 0.85, H * 0.2, 420, PINK_LIGHT + (255,))
    number_tag(c, lt, 0.05, "01", PINK, WHITE)
    p = ease_out(prog(lt, 0, 0.6))
    cd = card("sosyal.png", 720, 780, zoom=1.0 + 0.06 * lt / D, focus=(0.5, 0.4))
    paste(c, cd, W / 2 + (1 - p) * 900, 800, angle=-3)
    rise(c, text_layer("ANKARA'NIN PADEL\nTOPLULUĞU", font("head", 112), GREEN, spacing=4),
         W / 2, 1490, lt, 0.35)
    n = int(829 * ease_out(prog(lt, 0.6, 1.4)))
    rise(c, pill(f"{n}+ takipçi", font("bold", 56), LIME, GREEN), W / 2, 1700, lt, 0.55)
    return c


def s_turnuva(lt, D):
    c = bg(GREEN)
    number_tag(c, lt, 0.05, "02", LIME, GREEN)
    p1 = ease_out(prog(lt, 0.0, 0.55))
    p2 = ease_out(prog(lt, 0.2, 0.55))
    z = 1.0 + 0.04 * lt / D
    # Afişlerdeki ödül tutarı görünmesin diye yalnızca başlık bölümleri kesilerek kullanılır
    paste(c, card("afis_turnuva_30agu.png", 640, 290, zoom=z, crop=(0, 370, 512, 602)),
          W * 0.56 + (1 - p2) * 900, 900, angle=4 - (1 - p2) * 15)
    paste(c, card("afis_turnuva_02agu.png", 640, 362, zoom=z, crop=(0, 60, 512, 350)),
          W * 0.44 - (1 - p1) * 900, 560, angle=-4 + (1 - p1) * 15)
    rise(c, text_layer("TURNUVALAR", font("head", 190), LIME), W / 2, 1360, lt, 0.4)
    rise(c, text_layer("Ödüllü karma turnuvalar", font("bold", 62), WHITE), W / 2, 1530,
         lt, 0.6)
    rise(c, pill("Her seviyeye uygun", font("bold", 50), PINK, WHITE), W / 2, 1660, lt, 0.75)
    return c


def s_pilates(lt, D):
    c = bg(PINK_LIGHT)
    big_circle(c, W * 0.95, H * 0.12, 380, (247, 160, 203, 255))
    number_tag(c, lt, 0.05, "03", GREEN, LIME)
    p = ease_out(prog(lt, 0, 0.6))
    paste(c, card("pilates_kort.png", 600, 860, zoom=1.0 + 0.08 * lt / D, focus=(0.6, 0.5)),
          W * 0.42, 800 + (1 - p) * 1100, angle=-2)
    p2 = ease_back(prog(lt, 0.45, 0.5))
    paste(c, card("afis_padel_pilates.png", 300, 375, border=10, radius=22), W * 0.79, 1150,
          scale=max(0.01, p2), angle=7)
    l1 = text_layer("PADEL", font("head", 170), GREEN)
    amp = text_layer("&", font("head", 170), PINK)
    l2 = text_layer("PİLATES", font("head", 170), GREEN)
    head = Image.new("RGBA", (l1.width + amp.width + l2.width + 40, max(l1.height, l2.height)),
                     (0, 0, 0, 0))
    head.alpha_composite(l1, (0, 0))
    head.alpha_composite(amp, (l1.width + 20, 0))
    head.alpha_composite(l2, (l1.width + amp.width + 40, 0))
    rise(c, head, W / 2, 1480, lt, 0.35)
    rise(c, text_layer("Önce esneme, sonra turnuva", font("bold", 60), PINK), W / 2, 1640, lt, 0.55)
    return c


def s_dj(lt, D):
    c = bg(GREEN_DARK)
    z = 1.08 - 0.06 * lt / D
    src = photo("dj.png")
    sw, sh = src.size
    tw, th = sw / z, sw / z * H / W
    if th > sh:
        th, tw = sh / z, sh / z * W / H
    x0, y0 = (sw - tw) * 0.45, (sh - th) * 0.5
    img = src.resize((W, H), Image.LANCZOS, box=(x0, y0, x0 + tw, y0 + th)).convert("RGBA")
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    c.alpha_composite(img)
    grad = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(v * 0.92))
    shade = Image.new("RGBA", (W, H), GREEN_DARK + (255,))
    shade.putalpha(grad)
    c.alpha_composite(shade)
    number_tag(c, lt, 0.05, "04", PINK, WHITE)
    rise(c, text_layer("DJ'Lİ SOSYAL\nBULUŞMALAR", font("head", 150), WHITE, spacing=6),
         W / 2, 1300, lt, 0.25)
    rise(c, text_layer("Maçtan sonra müzik, sohbet, yeni dostluklar", font("semi", 46), LIME),
         W / 2, 1520, lt, 0.5)
    # ekolayzer çubukları (vuruşla oynar)
    d = ImageDraw.Draw(c)
    beat = (lt % BEAT) / BEAT
    for i in range(16):
        hgt = 40 + 140 * abs(math.sin(i * 1.7 + lt * 6)) * (1.15 - beat * 0.5)
        x = 140 + i * 52
        d.rounded_rectangle([x, 1760 - hgt, x + 34, 1760], radius=12,
                            fill=LIME if i % 3 else PINK)
    return c


def s_fairplay(lt, D):
    c = bg(PINK)
    number_tag(c, lt, 0.05, "05", WHITE, PINK)
    p = ease_out(prog(lt, 0, 0.6))
    paste(c, card("tokalasma.png", 640, 900, zoom=1.0 + 0.1 * lt / D, focus=(0.4, 0.55)),
          W / 2, 820, scale=0.85 + 0.15 * p, alpha=p, angle=2)
    rise(c, text_layer("FAIR-PLAY RUHU", font("head", 150), WHITE), W / 2, 1450, lt, 0.35)
    rise(c, text_layer("Rakibini, partnerini ve yeni\narkadaşlarını burada bulursun",
                       font("bold", 54), GREEN, spacing=6), W / 2, 1640, lt, 0.55)
    return c


def s_outro(lt, D):
    c = bg(PINK)
    big_circle(c, W / 2, H * 0.42, 650 + 18 * math.sin(lt * 4), PINK_LIGHT + (255,))
    paste(c, logo(300), W / 2, 520, scale=max(0.01, ease_back(prog(lt, 0.05, 0.5))))
    rise(c, text_layer("SIRADAKİ MAÇ", font("head", 160), GREEN), W / 2, 860, lt, 0.3)
    rise(c, text_layer("SENİNLE!", font("head", 210), PINK), W / 2, 1070, lt, 0.45)
    rise(c, pill("@padelclubankara", font("bold", 62), GREEN, WHITE), W / 2, 1460, lt, 0.7)
    rise(c, text_layer("Katılmak için DM at", font("bold", 56), WHITE), W / 2, 1600, lt, 0.9)
    bouncing_ball(c, lt, W + 60, -60, 1800, 220, 0.4, 3.0, size=76)
    return c


SCENES = [  # (fonksiyon, süre — vuruşa oturtulmuş)
    (s_intro, 6 * BEAT),
    (s_topluluk, 6 * BEAT),
    (s_turnuva, 7 * BEAT),
    (s_pilates, 6 * BEAT),
    (s_dj, 6 * BEAT),
    (s_fairplay, 6 * BEAT),
    (s_outro, 9 * BEAT),
]


def wipe(canvas, p, colors=(LIME, GREEN)):
    """Çapraz bant geçişi; p 0→1 ekranı soldan sağa süpürür."""
    d = ImageDraw.Draw(canvas)
    span = W * 2.6
    x = -W * 1.3 + span * p
    skew = 500
    for i, col in enumerate(colors):
        off = i * 120
        x0 = x - W * 0.9 + off
        x1 = x + W * 0.9 - off
        d.polygon([(x0, 0), (x1, 0), (x1 - skew, H), (x0 - skew, H)], fill=col)


def frame_at(t):
    acc = 0.0
    bounds = []
    for fn, dur in SCENES:
        bounds.append((fn, acc, dur))
        acc += dur
    for i, (fn, start, dur) in enumerate(bounds):
        if t < start + dur or i == len(bounds) - 1:
            im = fn(t - start, dur)
            T = 0.22  # geçiş yarı süresi
            if i + 1 < len(bounds) and t > start + dur - T:
                wipe(im, 0.5 * (t - (start + dur - T)) / T,
                     (LIME, PINK if i % 2 else GREEN))
            if i > 0 and t < start + T:
                wipe(im, 0.5 + 0.5 * (t - start) / T, (LIME, PINK if (i - 1) % 2 else GREEN))
            return im.convert("RGB")
    raise ValueError


def total_duration():
    return sum(d for _, d in SCENES)


# ---------------------------------------------------------------- müzik
def make_music(path, dur, sr=44100):
    n = int(dur * sr)
    t = np.arange(n) / sr
    out = np.zeros(n)

    def env(length, decay):
        x = np.arange(int(length * sr)) / sr
        return np.exp(-x * decay)

    def add(sig, at):
        i = int(at * sr)
        j = min(n, i + len(sig))
        if i < n:
            out[i:j] += sig[: j - i]

    kick_len = 0.35
    kt = np.arange(int(kick_len * sr)) / sr
    kick = np.sin(2 * np.pi * (50 + 120 * np.exp(-kt * 30)) * kt) * env(kick_len, 9)
    rng = np.random.default_rng(7)
    hat = rng.standard_normal(int(0.05 * sr)) * env(0.05, 80)
    hat = np.diff(hat, prepend=0)  # tiz filtre benzeri
    clap = rng.standard_normal(int(0.18 * sr)) * env(0.18, 22)
    # Akor ilerlemesi (Am - F - C - G), yumuşak pad
    chords = [[220.0, 261.63, 329.63], [174.61, 220.0, 261.63],
              [261.63, 329.63, 392.0], [196.0, 246.94, 293.66]]
    beats = int(dur / BEAT) + 1
    for b in range(beats):
        at = b * BEAT
        add(kick * 0.9, at)
        if b % 2 == 1:
            add(clap * 0.25, at)
        add(hat * 0.18, at + BEAT / 2)
        if b % 4 == 0:
            ch = chords[(b // 4) % 4]
            L = 4 * BEAT
            x = np.arange(int(L * sr)) / sr
            pad = sum(np.sin(2 * np.pi * f * x) + 0.3 * np.sin(2 * np.pi * 2 * f * x) for f in ch)
            pad *= np.minimum(1, x / 0.05) * np.exp(-x * 0.9) * 0.07
            add(pad, at)
        # pluck melodisi
        if b % 2 == 0:
            ch = chords[(b // 4) % 4]
            f = ch[(b // 2) % 3] * 2
            L = 0.3
            x = np.arange(int(L * sr)) / sr
            add(np.sin(2 * np.pi * f * x) * np.exp(-x * 14) * 0.12, at + BEAT * 0.5)
    fade = np.minimum(1, (dur - t) / 1.2)
    out *= np.clip(fade, 0, 1)
    out = np.tanh(out * 1.4) * 0.8
    pcm = (out * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- render
def render(out_path: Path, preview_every: float | None = None):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dur = total_duration()
    wav = out_path.with_suffix(".wav")
    make_music(wav, dur)
    nframes = int(round(dur * FPS))
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(wav),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(nframes):
        proc.stdin.write(frame_at(i / FPS).tobytes())
    proc.stdin.close()
    proc.wait()
    wav.unlink(missing_ok=True)
    return out_path, dur


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "output" / "reel_neler_yaptik.mp4"))
    ap.add_argument("--still", type=float, nargs="*", help="Sadece bu saniyelerin karelerini PNG kaydet")
    a = ap.parse_args()
    if a.still:
        for s in a.still:
            p = Path(a.out).with_name(f"kare_{s:05.2f}.png")
            p.parent.mkdir(parents=True, exist_ok=True)
            frame_at(s).save(p)
            print(p)
    else:
        print(*render(a.out))
