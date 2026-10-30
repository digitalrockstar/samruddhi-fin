"""Regenerates every icon as PNG from the Devanagari 'स' mark (same mark as the sidebar logo). No SVG."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
BLUE, WHITE, S = (0, 122, 255), (255, 255, 255), 1024


def master(shape="squircle", glyph=0.60):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if shape == "squircle":
        d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.225), fill=BLUE)
    elif shape == "circle":
        d.ellipse((0, 0, S - 1, S - 1), fill=BLUE)
    else:                                                  # full bleed (maskable)
        d.rectangle((0, 0, S, S), fill=BLUE)
    f = ImageFont.truetype(FONT, int(S * glyph * 1.25))
    l, t, r, b = d.textbbox((0, 0), "स", font=f)
    d.text(((S - (r - l)) / 2 - l, (S - (b - t)) / 2 - t - S * 0.01), "स", font=f, fill=WHITE)
    return im


def save(im, path, px):
    path.parent.mkdir(parents=True, exist_ok=True)
    im.resize((px, px), Image.LANCZOS).save(path, optimize=True)


icons, res = ROOT / "static/icons", ROOT / "android/app/src/main/res"
sq, circ, mask = master(), master("circle", 0.56), master("bleed", 0.46)
for px in (16, 32, 48):
    save(sq, icons / f"favicon-{px}.png", px)
save(sq, icons / "apple-touch-icon.png", 180)
save(sq, icons / "icon-192.png", 192)
save(sq, icons / "icon-512.png", 512)
save(mask, icons / "icon-maskable-512.png", 512)
for name, px in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
    save(sq, res / f"mipmap-{name}/ic_launcher.png", px)
    save(circ, res / f"mipmap-{name}/ic_launcher_round.png", px)
save(sq, ROOT / "android/play-store-icon-512.png", 512)
print("icons written")
