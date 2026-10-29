"""Görsel/video üretimi — LLM kullanmaz, şablon + kulübün kendi fotoğraf/videoları.

Neden yapay zekâ görsel üretimi değil? Kulüp sayfasında gerçek kort, gerçek üye
görüntüleri hem daha çok etkileşim alır hem de bedavadır. Şablon marka tutarlılığı sağlar.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350  # Instagram 4:5 portre
PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_EXT = (".mp4", ".mov", ".m4v")

_FONT_FALLBACKS_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
_FONT_FALLBACKS_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def list_assets(assets_dir: str | Path) -> dict:
    d = Path(assets_dir)
    files = [p for p in d.rglob("*") if p.is_file()] if d.exists() else []
    return {
        "photos": sorted(str(p.relative_to(d)) for p in files
                         if p.suffix.lower() in PHOTO_EXT and p.stem != "logo"),
        "videos": sorted(str(p.relative_to(d)) for p in files if p.suffix.lower() in VIDEO_EXT),
    }


def _font(path: str, fallbacks: list[str], size: int):
    for p in [path, *fallbacks]:
        if p and Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def _hex(c: str) -> tuple:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _cover(img: Image.Image) -> Image.Image:
    r = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * r) + 1, int(img.height * r) + 1), Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((left, top, left + W, top + H))


def _draw_text_block(img, headline, subline, v, y_bottom, align_center=False):
    d = ImageDraw.Draw(img)
    fb = _font(v.get("font_bold", ""), _FONT_FALLBACKS_BOLD, 88)
    fr = _font(v.get("font_regular", ""), _FONT_FALLBACKS_REG, 40)
    pad = 80
    h_lines = _wrap(d, headline, fb, W - 2 * pad)
    s_lines = _wrap(d, subline, fr, W - 2 * pad)
    block_h = len(h_lines) * 100 + (len(s_lines) * 54 + 24 if s_lines else 0)
    y = y_bottom - block_h
    for line in h_lines:
        x = (W - d.textlength(line, font=fb)) / 2 if align_center else pad
        d.text((x, y), line, font=fb, fill=_hex(v["text_color"]))
        y += 100
    y += 24
    for line in s_lines:
        x = (W - d.textlength(line, font=fr)) / 2 if align_center else pad
        d.text((x, y), line, font=fr, fill=_hex(v["accent_color"]))
        y += 54


def _add_logo(img, v):
    lp = v.get("logo_path")
    if lp and Path(lp).exists():
        logo = Image.open(lp).convert("RGBA")
        logo.thumbnail((180, 180))
        img.paste(logo, (W - logo.width - 50, 50), logo)


def render_image(post: dict, brand: dict, assets_dir: str, out_dir: str) -> str:
    v = brand["visual"]
    tpl = post["template"]
    src = post.get("source_asset")

    if tpl == "photo_overlay" and src and (Path(assets_dir) / src).exists():
        img = _cover(Image.open(Path(assets_dir) / src).convert("RGB"))
        # alttan koyulaşan gradyan → metin her fotoğrafta okunur
        grad = Image.new("L", (1, H))
        for y in range(H):
            grad.putpixel((0, y), int(230 * max(0, (y - H * 0.45) / (H * 0.55))))
        shade = Image.new("RGB", (W, H), _hex(v["primary_color"]))
        img = Image.composite(shade, img, grad.resize((W, H)))
        _draw_text_block(img, post["headline"], post.get("subline"), v, H - 110)
    else:
        img = Image.new("RGB", (W, H), _hex(v["primary_color"]))
        d = ImageDraw.Draw(img)
        d.rectangle([0, H - 40, W, H], fill=_hex(v["accent_color"]))
        if tpl == "event":
            fs = _font(v.get("font_bold", ""), _FONT_FALLBACKS_BOLD, 44)
            tw = d.textlength("ETKİNLİK", font=fs)
            d.rounded_rectangle([80, 120, 80 + tw + 80, 220], radius=50, fill=_hex(v["accent_color"]))
            d.text((120, 145), "ETKİNLİK", font=fs, fill=_hex(v["primary_color"]))
        _draw_text_block(img, post["headline"], post.get("subline"), v, H // 2 + 200,
                         align_center=(tpl == "text_card"))

    _add_logo(img, v)
    out = Path(out_dir) / f"{post.get('file_stem') or 'post_' + str(post['id'])}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=92)
    return str(out)


def render_reel(post: dict, assets_dir: str, out_dir: str, max_sec: int = 30) -> str | None:
    src = Path(assets_dir) / (post.get("source_asset") or "")
    if not src.is_file():
        return None
    out = Path(out_dir) / f"post_{post['id']}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg"):
        # 9:16, 1080x1920, en fazla 30 sn, Instagram uyumlu H.264/AAC
        vf = ("scale=1080:1920:force_original_aspect_ratio=decrease,"
              "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black")
        subprocess.run(["ffmpeg", "-y", "-i", str(src), "-t", str(max_sec), "-vf", vf,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)],
                       check=True, capture_output=True)
    else:
        shutil.copy(src, out)
    return str(out)


def render(post: dict, brand: dict, assets_dir: str, out_dir: str) -> str | None:
    if post["template"] == "reel":
        return render_reel(post, assets_dir, out_dir)
    return render_image(post, brand, assets_dir, out_dir)
