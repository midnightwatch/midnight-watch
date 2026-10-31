"""Draws data/share.png, the picture chat apps show when the link is shared."""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

IRON, SOOT, CHAR = (26, 21, 19), (38, 30, 26), (53, 41, 31)
ASH, SMOKE, BONE, EMBER = (217, 207, 196), (143, 129, 118), (243, 236, 226), (224, 86, 29)
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]


def font(size):
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def polar(cx, cy, deg, r):
    a = math.radians(deg - 90)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def draw_share_image(seconds, previous, date_text, out_path):
    W, H = 1200, 630
    S = 2  # draw at 2x then shrink for smooth edges
    img = Image.new("RGB", (W * S, H * S), IRON)
    d = ImageDraw.Draw(img)

    # soft ember glow behind the clock
    glow = Image.new("RGB", (W * S, H * S), IRON)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([80 * S, -40 * S, 600 * S, 480 * S], fill=(60, 32, 22))
    glow = glow.filter(ImageFilter.GaussianBlur(120 * S))
    img = Image.blend(img, glow, 0.6)
    d = ImageDraw.Draw(img)

    # clock
    cx, cy, R = 315 * S, 315 * S, 225 * S
    d.ellipse([cx - R - 12 * S, cy - R - 12 * S, cx + R + 12 * S, cy + R + 12 * S], outline=CHAR, width=14 * S)
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=SOOT, outline=CHAR, width=3 * S)
    for i in range(60):
        hour = i % 5 == 0
        x1, y1 = polar(cx, cy, i * 6, R - (28 if hour else 15) * S)
        x2, y2 = polar(cx, cy, i * 6, R - 6 * S)
        d.line([x1, y1, x2, y2], fill=ASH if hour else SMOKE, width=(4 if hour else 2) * S)
    mins = seconds / 60
    visual = max(mins * 6, 0.6) * 4
    d.pieslice([cx - R + 3 * S, cy - R + 3 * S, cx + R - 3 * S, cy + R - 3 * S],
               start=270 - visual, end=270, fill=EMBER)
    hx, hy = polar(cx, cy, 360 - (mins / 60) * 30, R - 110 * S)
    mx, my = polar(cx, cy, 360 - mins * 6, R - 32 * S)
    d.line([cx, cy, hx, hy], fill=BONE, width=12 * S)
    d.line([cx, cy, mx, my], fill=BONE, width=7 * S)
    d.ellipse([cx - 9 * S, cy - 9 * S, cx + 9 * S, cy + 9 * S], fill=BONE)

    # text
    x = 620 * S
    d.text((x, 150 * S), "MIDNIGHT WATCH", fill=SMOKE, font=font(30 * S))
    big = font(150 * S)
    d.text((x, 190 * S), str(seconds), fill=EMBER, font=big)
    nw = d.textlength(str(seconds), font=big)
    d.text((x + nw + 20 * S, 275 * S), "seconds", fill=BONE, font=font(60 * S))
    d.text((x, 350 * S), "to midnight", fill=BONE, font=font(60 * S))
    delta = seconds - previous
    sub = ("no change from yesterday" if delta == 0 else
           f"{abs(delta)} second{'s' if abs(delta) != 1 else ''} {'closer' if delta < 0 else 'further'} than yesterday")
    d.text((x, 445 * S), sub, fill=EMBER if delta < 0 else ASH, font=font(30 * S))
    d.text((x, 495 * S), f"Daily AI Doomsday Clock  ·  {date_text}", fill=SMOKE, font=font(22 * S))
    d.text((x, 540 * S), "midnightwatch.github.io/midnight-watch", fill=SMOKE, font=font(20 * S))

    img = img.resize((W, H), Image.LANCZOS)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)


if __name__ == "__main__":
    draw_share_image(84, 85, "31 October 2026", "data/share.png")
