"""
Generates the system tray icon dynamically based on current usage.

Four selectable styles:
  - frame   : thick coloured ring + dark centre + big number (default)
  - solid   : fully filled rounded square in the band colour + number
  - donut   : circular progress arc + number in centre
  - bar     : square with number on top and a horizontal coloured bar below
"""

from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont


# Native Windows tray slot is 16x16. We render at 64x64 and let Windows
# scale down — gives crisp results at any DPI.
ICON_SIZE = 64
OUTER_PAD = 2

STYLES = ("frame", "solid", "donut", "bar")


def _gradient_color(pct: int) -> Tuple[int, int, int]:
    if pct < 50:
        return (34, 197, 94)    # green-500
    if pct < 75:
        return (250, 204, 21)   # yellow-400
    if pct < 90:
        return (249, 115, 22)   # orange-500
    return (239, 68, 68)        # red-500


def _idle_color(theme: str, error: bool) -> Tuple[int, int, int]:
    dark = (theme == "dark")
    if error:
        return (90, 90, 90) if dark else (120, 120, 120)
    return (71, 85, 105) if dark else (148, 163, 184)


def _text_luminance(bg: Tuple[int, int, int]) -> Tuple[int, int, int]:
    r, g, b = bg
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return (20, 20, 20) if lum > 160 else (255, 255, 255)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "arialbd.ttf", "Arial Bold.ttf",
        "DejaVuSans-Bold.ttf", "Helvetica-Bold.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _fit_font(draw: ImageDraw.ImageDraw, text: str,
              max_w: float, max_h: float, start_size: int) -> ImageFont.FreeTypeFont:
    size = start_size
    while size > 8:
        font = _load_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_w and (bbox[3] - bbox[1]) <= max_h:
            return font
        size -= 2
    return _load_font(8)


def _glyph(pct: Optional[int], error: bool) -> str:
    if pct is None:
        return "!" if error else "?"
    if pct >= 100:
        return "!!"
    return str(pct)


def _draw_centered_text(draw: ImageDraw.ImageDraw, text: str,
                        box: Tuple[float, float, float, float],
                        color: Tuple[int, int, int],
                        start_size: int = 48):
    """Draw text centred inside the box (x1, y1, x2, y2)."""
    x1, y1, x2, y2 = box
    w = x2 - x1
    h = y2 - y1
    font = _fit_font(draw, text, w - 2, h - 2, start_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = x1 + (w - tw) / 2 - bbox[0]
    ty = y1 + (h - th) / 2 - bbox[1]
    draw.text((tx, ty), text, fill=color + (255,), font=font)


# --- Styles ---------------------------------------------------------------

def _render_frame(pct: Optional[int], error: bool, theme: str) -> Image.Image:
    """Rounded square with a thick coloured ring and a dark contrast centre."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    ring = _idle_color(theme, error) if pct is None or error else _gradient_color(pct)
    dark = (theme == "dark")
    center = (245, 245, 245) if dark else (24, 24, 27)
    text_color = (15, 23, 42) if dark else (255, 255, 255)

    ring_width = 9
    draw.rounded_rectangle(
        [OUTER_PAD, OUTER_PAD, ICON_SIZE - OUTER_PAD, ICON_SIZE - OUTER_PAD],
        radius=14, fill=ring + (255,),
    )
    inner = OUTER_PAD + ring_width
    draw.rounded_rectangle(
        [inner, inner, ICON_SIZE - inner, ICON_SIZE - inner],
        radius=7, fill=center + (255,),
    )

    _draw_centered_text(
        draw, _glyph(pct, error),
        (inner, inner, ICON_SIZE - inner, ICON_SIZE - inner),
        text_color, start_size=48,
    )
    return img


def _render_solid(pct: Optional[int], error: bool, theme: str) -> Image.Image:
    """Filled rounded square in the band colour, contrasting number."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bg = _idle_color(theme, error) if pct is None or error else _gradient_color(pct)
    text_color = _text_luminance(bg)

    draw.rounded_rectangle(
        [OUTER_PAD, OUTER_PAD, ICON_SIZE - OUTER_PAD, ICON_SIZE - OUTER_PAD],
        radius=14, fill=bg + (255,),
    )
    _draw_centered_text(
        draw, _glyph(pct, error),
        (OUTER_PAD + 4, OUTER_PAD + 4,
         ICON_SIZE - OUTER_PAD - 4, ICON_SIZE - OUTER_PAD - 4),
        text_color, start_size=56,
    )
    return img


def _render_donut(pct: Optional[int], error: bool, theme: str) -> Image.Image:
    """Circular progress arc — the arc length matches the percentage."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    ring = _idle_color(theme, error) if pct is None or error else _gradient_color(pct)
    dark = (theme == "dark")
    center = (245, 245, 245) if dark else (24, 24, 27)
    text_color = (15, 23, 42) if dark else (255, 255, 255)
    track = (210, 210, 215) if not dark else (60, 60, 70)

    pad = 3
    box = [pad, pad, ICON_SIZE - pad, ICON_SIZE - pad]

    # Outer track (light grey ring)
    draw.ellipse(box, fill=track + (255,))

    # Coloured progress arc — full circle for unknown
    arc_pct = 100 if pct is None else max(0, min(100, pct))
    end_angle = -90 + (arc_pct / 100.0) * 360
    if arc_pct > 0:
        draw.pieslice(box, start=-90, end=end_angle, fill=ring + (255,))

    # Inner circle hole — creates the donut
    inner_pad = pad + 9
    draw.ellipse(
        [inner_pad, inner_pad,
         ICON_SIZE - inner_pad, ICON_SIZE - inner_pad],
        fill=center + (255,),
    )

    _draw_centered_text(
        draw, _glyph(pct, error),
        (inner_pad, inner_pad,
         ICON_SIZE - inner_pad, ICON_SIZE - inner_pad),
        text_color, start_size=40,
    )
    return img


def _render_bar(pct: Optional[int], error: bool, theme: str) -> Image.Image:
    """Number on top, coloured horizontal progress bar at the bottom."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    color = _idle_color(theme, error) if pct is None or error else _gradient_color(pct)
    dark = (theme == "dark")
    bg = (245, 245, 245) if dark else (24, 24, 27)
    text_color = (15, 23, 42) if dark else (255, 255, 255)
    track = (60, 60, 70) if not dark else (210, 210, 215)

    draw.rounded_rectangle(
        [OUTER_PAD, OUTER_PAD, ICON_SIZE - OUTER_PAD, ICON_SIZE - OUTER_PAD],
        radius=12, fill=bg + (255,),
    )

    # Reserve bottom strip for the bar
    bar_h = 12
    bar_top = ICON_SIZE - OUTER_PAD - bar_h - 4
    bar_l = OUTER_PAD + 6
    bar_r = ICON_SIZE - OUTER_PAD - 6

    # Number above the bar
    _draw_centered_text(
        draw, _glyph(pct, error),
        (OUTER_PAD + 4, OUTER_PAD + 4, ICON_SIZE - OUTER_PAD - 4, bar_top - 2),
        text_color, start_size=44,
    )

    # Track
    draw.rounded_rectangle(
        [bar_l, bar_top, bar_r, bar_top + bar_h],
        radius=bar_h / 2, fill=track + (255,),
    )

    # Fill
    fill_pct = 100 if pct is None else max(0, min(100, pct))
    if fill_pct > 0:
        fill_w = max(int(round((fill_pct / 100.0) * (bar_r - bar_l))), bar_h)
        draw.rounded_rectangle(
            [bar_l, bar_top, bar_l + fill_w, bar_top + bar_h],
            radius=bar_h / 2, fill=color + (255,),
        )
    return img


# --- Dispatcher -----------------------------------------------------------

def render_icon(pct: Optional[int], error: bool = False,
                theme: str = "light", style: str = "frame") -> Image.Image:
    if style == "solid":
        return _render_solid(pct, error, theme)
    if style == "donut":
        return _render_donut(pct, error, theme)
    if style == "bar":
        return _render_bar(pct, error, theme)
    return _render_frame(pct, error, theme)


if __name__ == "__main__":
    import os
    out_dir = "icon_preview"
    os.makedirs(out_dir, exist_ok=True)
    for style in STYLES:
        for p in [None, 5, 25, 50, 67, 85, 95, 100]:
            img = render_icon(p, theme="light", style=style)
            img.save(f"{out_dir}/{style}_{p}.png")
            img.resize((16, 16)).save(f"{out_dir}/{style}_{p}_16.png")
    print("Saved samples to icon_preview/")
