"""萬花筒畫板。

滑鼠拖曳畫圖，筆畫會自動做旋轉對稱，顏色沿筆畫變彩虹。

按鍵：
  R          自動產生隨機花樣
  U / Ctrl+Z 撤銷上一筆
  C          清空
  S          儲存成 SVG（kaleidoscope/drawings/）
  + / -      增減對稱數（1~24，預設 8）
  M          鏡像開關
  [ / ]      筆畫粗細
  H          顯示 / 隱藏說明
  Esc        離開
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pattern import MAX_SYM, MIN_SYM, Stroke, hsv, random_pattern, symmetric_polylines, to_svg  # noqa: E402

W, H = 900, 900
BG = (12, 12, 20)
TEXT = (220, 220, 235)
SAVE_DIR = Path(__file__).parent / "drawings"
HELP = ["拖曳滑鼠畫圖", "R 隨機花樣   U 撤銷   C 清空   S 存 SVG",
        "+/- 對稱數   M 鏡像   [ ] 粗細   H 隱藏說明   Esc 離開"]


def load_font(size):
    for name in ("microsoftjhenghei", "pingfangtc", "notosanscjktc", "notosanscjk",
                 "wenquanyizenhei", "arialunicodems"):
        path = pygame.font.match_font(name)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, size + 6)


class App:
    def __init__(self, screen):
        self.screen = screen
        self.font = load_font(18)
        self.strokes: list[Stroke] = []
        self.current: Stroke | None = None
        self.n, self.mirror, self.width = 8, False, 3
        self.show_help = True
        self.message, self.message_until = "", 0
        self.rng = random.Random()
        self.hue = 0.0
        self.canvas = pygame.Surface((W, H))
        self.dirty = True

    # ---- 繪圖 ----
    def draw_stroke(self, surf, stroke: Stroke):
        cx, cy = W / 2, H / 2
        for line in symmetric_polylines(stroke, self.n, self.mirror):
            if len(line) == 1:
                x, y, hue = line[0]
                pygame.draw.circle(surf, hsv(hue), (cx + x, cy + y), max(1, stroke.width / 2))
                continue
            for (x1, y1, h1), (x2, y2, _) in zip(line, line[1:]):
                pygame.draw.line(surf, hsv(h1), (cx + x1, cy + y1), (cx + x2, cy + y2),
                                 int(stroke.width))

    def rebuild(self):
        self.canvas.fill(BG)
        for st in self.strokes:
            self.draw_stroke(self.canvas, st)
        self.dirty = False

    def render(self):
        if self.dirty:
            self.rebuild()
        self.screen.blit(self.canvas, (0, 0))
        if self.current:
            self.draw_stroke(self.screen, self.current)
        status = f"對稱 {self.n}{' ＋鏡像' if self.mirror else ''}   粗細 {self.width}   筆畫 {len(self.strokes)}"
        lines = [status] + (HELP if self.show_help else [])
        for i, t in enumerate(lines):
            self.screen.blit(self.font.render(t, True, TEXT), (12, 10 + i * 24))
        if self.message and pygame.time.get_ticks() < self.message_until:
            surf = self.font.render(self.message, True, (255, 220, 120))
            self.screen.blit(surf, surf.get_rect(midbottom=(W // 2, H - 14)))
        pygame.display.flip()

    def flash(self, text):
        self.message, self.message_until = text, pygame.time.get_ticks() + 2500

    # ---- 動作 ----
    def undo(self):
        if self.strokes:
            self.strokes.pop()
            self.dirty = True

    def clear(self):
        self.strokes.clear()
        self.dirty = True

    def randomize(self):
        self.strokes = random_pattern(self.rng, min(W, H) * 0.45)
        self.n = self.rng.choice([6, 8, 10, 12, 16])
        self.mirror = self.rng.random() < 0.5
        self.dirty = True

    def save(self) -> Path:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        path = SAVE_DIR / f"kaleido_{datetime.now():%Y%m%d_%H%M%S}.svg"
        path.write_text(to_svg(self.strokes, self.n, self.mirror, (W, H), BG), encoding="utf-8")
        self.flash(f"已儲存 {path.name}")
        return path

    def set_sym(self, n):
        self.n = max(MIN_SYM, min(MAX_SYM, n))
        self.dirty = True

    # ---- 事件 ----
    def handle(self, ev) -> bool:
        """回傳 False 表示要離開。"""
        if ev.type == pygame.QUIT:
            return False
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            self.current = Stroke(width=self.width)
            x, y = ev.pos
            self.current.add(x - W / 2, y - H / 2, self.hue)
        elif ev.type == pygame.MOUSEMOTION and self.current:
            x, y = ev.pos
            self.current.add(x - W / 2, y - H / 2)
        elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and self.current:
            self.hue = (self.current.points[-1][2] + 40) % 360  # 下一筆接著換色
            self.strokes.append(self.current)
            self.draw_stroke(self.canvas, self.current)
            self.current = None
        elif ev.type == pygame.KEYDOWN:
            k, mods = ev.key, ev.mod
            if k == pygame.K_ESCAPE:
                return False
            if k == pygame.K_u or (k == pygame.K_z and mods & (pygame.KMOD_CTRL | pygame.KMOD_META)):
                self.undo()
            elif k == pygame.K_r:
                self.randomize()
            elif k == pygame.K_c:
                self.clear()
            elif k == pygame.K_s:
                self.save()
            elif k in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                self.set_sym(self.n + 1)
            elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self.set_sym(self.n - 1)
            elif k == pygame.K_m:
                self.mirror = not self.mirror
                self.dirty = True
            elif k == pygame.K_LEFTBRACKET:
                self.width = max(1, self.width - 1)
            elif k == pygame.K_RIGHTBRACKET:
                self.width = min(20, self.width + 1)
            elif k == pygame.K_h:
                self.show_help = not self.show_help
        return True


def main():
    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("萬花筒畫板")
    app = App(screen)
    clock = pygame.time.Clock()
    while True:
        for ev in pygame.event.get():
            if not app.handle(ev):
                pygame.quit()
                return
        app.render()
        clock.tick(60)


if __name__ == "__main__":
    main()
