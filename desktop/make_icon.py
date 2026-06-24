#!/usr/bin/env python3
"""
make_icon.py — 生成 CodexAutoAI 桌面圖示（GS 暗金主題）。

warm-black 圓角底 + 金色環 + 金色「CA」字。多尺寸打包成 desktop/codexautoai.ico。
產物入庫，平時不必重跑；改設計才重生。需要 Pillow（build 腳本會確保）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (15, 17, 21, 255)      # #0f1115 warm-black
GOLD = (212, 175, 55, 255)  # #d4af37

OUT = Path(__file__).resolve().parent / "codexautoai.ico"


def _font(size: int):
    for name in ("seguisb.ttf", "segoeui.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render(px: int) -> Image.Image:
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = max(1, px // 16)
    # 圓角底
    d.rounded_rectangle([m, m, px - m, px - m], radius=px // 5, fill=BG)
    # 金色環
    ring = max(1, px // 16)
    d.ellipse([px * 0.16, px * 0.16, px * 0.84, px * 0.84], outline=GOLD, width=ring)
    # 「CA」字（小尺寸只放一個點，避免糊）
    if px >= 32:
        f = _font(int(px * 0.42))
        text = "CA"
        try:
            bbox = d.textbbox((0, 0), text, font=f)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            d.text(((px - tw) / 2 - bbox[0], (px - th) / 2 - bbox[1]), text, font=f, fill=GOLD)
        except Exception:
            pass
    else:
        d.ellipse([px * 0.4, px * 0.4, px * 0.6, px * 0.6], fill=GOLD)
    return img


def main() -> int:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base = render(256)
    base.save(OUT, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"已生成 {OUT}（尺寸 {sizes}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
