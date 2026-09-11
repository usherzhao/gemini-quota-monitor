"""
Generate splash screen and icons for Gemini Quota Monitor.
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def generate_assets():
    assets_dir = Path(__file__).resolve().parent / "assets"
    assets_dir.mkdir(exist_ok=True)

    # 1. Generate splash.png (440x220)
    w, h = 440, 220
    splash = Image.new("RGB", (w, h), (15, 23, 42)) # #0F172A
    draw = ImageDraw.Draw(splash)

    # Draw border
    draw.rectangle([0, 0, w - 1, h - 1], outline=(51, 65, 85), width=2) # #334155

    # Draw decorative top gradient bar
    for x in range(w):
        # Gradient from #0284C7 (sky-600) to #6366F1 (indigo-500)
        ratio = x / w
        r = int(2 + (99 - 2) * ratio)
        g = int(132 + (102 - 132) * ratio)
        b = int(199 + (241 - 199) * ratio)
        draw.line([(x, 0), (x, 3)], fill=(r, g, b))

    # Star / Spark symbol
    cx, cy = 60, 100
    # Draw 4-point curved star
    star_color = (56, 189, 248) # #38BDF8
    pts = [
        (cx, cy - 28),
        (cx + 6, cy - 6),
        (cx + 28, cy),
        (cx + 6, cy + 6),
        (cx, cy + 28),
        (cx - 6, cy + 6),
        (cx - 28, cy),
        (cx - 6, cy - 6),
    ]
    draw.polygon(pts, fill=star_color)

    # Text
    try:
        font_title = ImageFont.truetype("msyh.ttc", 22)
        font_sub = ImageFont.truetype("msyh.ttc", 13)
        font_hint = ImageFont.truetype("msyh.ttc", 11)
    except Exception:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()
        font_hint = ImageFont.load_default()

    draw.text((105, 68), "Gemini Quota Monitor", fill=(248, 250, 252), font=font_title)
    draw.text((105, 105), "5小时滚动用量 & 周用量监控工具", fill=(148, 163, 184), font=font_sub)
    draw.text((105, 155), "⚡ 正在初始化监控组件，请稍候...", fill=(100, 116, 139), font=font_hint)

    splash_path = assets_dir / "splash.png"
    splash.save(splash_path)
    print(f"[Assets] Created {splash_path}")

    # 2. Generate icon.png and icon.ico (256x256)
    icon_size = 256
    icon_img = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
    d_icon = ImageDraw.Draw(icon_img)

    # Background circle
    margin = 8
    d_icon.ellipse(
        [margin, margin, icon_size - margin, icon_size - margin],
        fill=(15, 23, 42, 255),
        outline=(56, 189, 248, 200),
        width=6,
    )

    # 4-point star in center
    icx, icy = icon_size // 2, icon_size // 2
    r_outer = 85
    r_inner = 22
    star_pts = [
        (icx, icy - r_outer),
        (icx + r_inner, icy - r_inner),
        (icx + r_outer, icy),
        (icx + r_inner, icy + r_inner),
        (icx, icy + r_outer),
        (icx - r_inner, icy + r_inner),
        (icx - r_outer, icy),
        (icx - r_inner, icy - r_inner),
    ]
    d_icon.polygon(star_pts, fill=(56, 189, 248, 255))

    # Inner bright core
    r_core = 14
    d_icon.ellipse(
        [icx - r_core, icy - r_core, icx + r_core, icy + r_core],
        fill=(255, 255, 255, 255)
    )

    icon_png_path = assets_dir / "icon.png"
    icon_img.save(icon_png_path)
    print(f"[Assets] Created {icon_png_path}")

    icon_ico_path = assets_dir / "icon.ico"
    icon_img.save(icon_ico_path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"[Assets] Created {icon_ico_path}")

if __name__ == "__main__":
    generate_assets()
