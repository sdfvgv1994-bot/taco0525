"""萬花筒的幾何與資料（不含畫面，方便測試）。

每一筆畫存成一串 (x, y, hue)，畫的時候依「對稱數 n」旋轉複製 n 份；
開啟鏡像時，每份再加一條沿半徑翻轉的線。
"""
from __future__ import annotations

import colorsys
import math
import random
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

HUE_PER_PIXEL = 0.6   # 每畫 1 像素，色相往前幾度（顏色沿筆畫變彩虹）
MIN_SYM, MAX_SYM = 1, 24


@dataclass
class Stroke:
    width: float = 3.0
    points: list = field(default_factory=list)   # [(x, y, hue), ...]，座標以畫布中心為原點

    def add(self, x: float, y: float, hue: float | None = None) -> None:
        if hue is None:
            if self.points:
                px, py, ph = self.points[-1]
                hue = (ph + math.hypot(x - px, y - py) * HUE_PER_PIXEL) % 360
            else:
                hue = 0.0
        self.points.append((x, y, hue))


def hsv(hue: float, s: float = 0.85, v: float = 1.0) -> tuple:
    r, g, b = colorsys.hsv_to_rgb((hue % 360) / 360, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def copies(x: float, y: float, n: int, mirror: bool) -> list:
    """一個點的所有對稱位置（中心為原點）。"""
    out = []
    for k in range(n):
        a = 2 * math.pi * k / n
        c, s = math.cos(a), math.sin(a)
        out.append((x * c - y * s, x * s + y * c))
        if mirror:
            out.append((x * c + y * s, x * s - y * c))  # 先對 x 軸翻轉再旋轉
    return out


def symmetric_polylines(stroke: Stroke, n: int, mirror: bool) -> list:
    """回傳 [[(x, y, hue), ...], ...]，每條是一份對稱副本。"""
    per_point = [copies(x, y, n, mirror) for x, y, _ in stroke.points]
    lines = []
    for j in range(len(per_point[0]) if per_point else 0):
        lines.append([(per_point[i][j][0], per_point[i][j][1], stroke.points[i][2])
                      for i in range(len(stroke.points))])
    return lines


def random_pattern(rng: random.Random, radius: float, start_hue: float | None = None) -> list:
    """產生幾條漂亮的曲線（玫瑰線、利薩如、螺線），回傳 Stroke 列表。"""
    strokes = []
    hue = rng.uniform(0, 360) if start_hue is None else start_hue
    for _ in range(rng.randint(3, 5)):
        kind = rng.choice(["rose", "lissajous", "spiral", "wave"])
        st = Stroke(width=rng.choice([2, 3, 4, 5]))
        steps = 240
        r0 = rng.uniform(0.45, 1.0) * radius
        a, b = rng.randint(1, 7), rng.randint(1, 7)
        phase = rng.uniform(0, math.tau)
        turns = rng.choice([1, 1.5, 2])
        for i in range(steps + 1):
            t = i / steps
            if kind == "rose":
                th = t * math.pi * 2 / max(a, 1)
                r = r0 * abs(math.cos(a * th + phase))
                x, y = r * math.cos(th), r * math.sin(th)
            elif kind == "lissajous":
                th = t * math.tau / 4
                x = r0 * 0.6 * math.sin(a * th + phase)
                y = r0 * 0.35 * math.sin(b * th) + r0 * 0.55
            elif kind == "spiral":
                th = t * math.tau * turns
                r = r0 * t
                x, y = r * math.cos(th + phase), r * math.sin(th + phase)
            else:
                x = (t - 0.5) * r0 * 1.2
                y = r0 * 0.55 + r0 * 0.2 * math.sin(t * math.tau * a + phase)
            st.add(x, y, (hue + t * 180) % 360)
        hue = (hue + rng.uniform(60, 140)) % 360
        strokes.append(st)
    return strokes


def to_svg(strokes: list, n: int, mirror: bool, size: tuple, bg: tuple = (12, 12, 20)) -> str:
    """把目前畫作輸出成 SVG 字串（每段線各自上色，呈現彩虹漸層）。"""
    w, h = size
    cx, cy = w / 2, h / 2
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">',
        f'<title>{escape(f"萬花筒（{n} 向對稱）")}</title>',
        f'<rect width="100%" height="100%" fill="rgb{bg}"/>',
        '<g fill="none" stroke-linecap="round" stroke-linejoin="round">',
    ]
    for st in strokes:
        for line in symmetric_polylines(st, n, mirror):
            if len(line) == 1:
                x, y, hue = line[0]
                parts.append(f'<circle cx="{cx + x:.1f}" cy="{cy + y:.1f}" r="{st.width / 2:.1f}" '
                             f'fill="rgb{hsv(hue)}"/>')
                continue
            for (x1, y1, h1), (x2, y2, _) in zip(line, line[1:]):
                parts.append(f'<line x1="{cx + x1:.1f}" y1="{cy + y1:.1f}" x2="{cx + x2:.1f}" '
                             f'y2="{cy + y2:.1f}" stroke="rgb{hsv(h1)}" '
                             f'stroke-width="{st.width:g}"/>')
    parts += ["</g>", "</svg>"]
    return "\n".join(parts)
