"""貪食蛇。

操作：方向鍵 / WASD 移動，P 暫停，空白鍵重新開始，Esc 離開。
紅色果子 +1 分、金色果子 +5 分（1/4 機率出現）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if __name__ == "__main__":  # 先自動更新，再匯入 pygame
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from common.updater import check_and_update
    check_and_update()

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from game import DOWN, GOLDEN, LEFT, RIGHT, UP, HighScore, SnakeGame  # noqa: E402

CELL = 28
COLS, ROWS = 24, 18
TOP = 48  # 上方分數列
BG, GRID = (18, 20, 28), (28, 31, 42)
SNAKE_HEAD, SNAKE_BODY = (120, 230, 120), (70, 180, 90)
FOOD_COLOR = {GOLDEN: (255, 200, 40), "normal": (235, 70, 70)}
TEXT = (230, 230, 240)

KEYS = {pygame.K_UP: UP, pygame.K_w: UP, pygame.K_DOWN: DOWN, pygame.K_s: DOWN,
        pygame.K_LEFT: LEFT, pygame.K_a: LEFT, pygame.K_RIGHT: RIGHT, pygame.K_d: RIGHT}


def load_font(size):
    # 找系統裡有中文的字型，找不到就用預設字型
    for name in ("microsoftjhenghei", "pingfangtc", "notosanscjktc", "notosanscjk",
                 "wenquanyizenhei", "arialunicodems"):
        path = pygame.font.match_font(name)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, size + 6)


def draw(screen, game, hs, fonts, paused, new_record, tick):
    big, small = fonts
    screen.fill(BG)
    for x in range(COLS):
        for y in range(ROWS):
            if (x + y) % 2:
                pygame.draw.rect(screen, GRID, (x * CELL, TOP + y * CELL, CELL, CELL))

    if game.food:
        fx, fy = game.food
        r = CELL // 2 - 3
        if game.food_kind == GOLDEN:
            r += (tick // 6) % 2  # 金色果子會閃
        pygame.draw.circle(screen, FOOD_COLOR[game.food_kind],
                           (fx * CELL + CELL // 2, TOP + fy * CELL + CELL // 2), r)

    for i, (x, y) in enumerate(game.snake):
        color = SNAKE_HEAD if i == 0 else SNAKE_BODY
        pygame.draw.rect(screen, color, (x * CELL + 2, TOP + y * CELL + 2, CELL - 4, CELL - 4),
                         border_radius=6)

    screen.blit(small.render(f"分數 {game.score}   最高 {hs.best}   長度 {len(game.snake)}",
                             True, TEXT), (12, 12))

    def center(text, font, dy=0, color=TEXT):
        surf = font.render(text, True, color)
        screen.blit(surf, surf.get_rect(center=(COLS * CELL // 2, TOP + ROWS * CELL // 2 + dy)))

    if not game.alive:
        center("遊戲結束", big, -30)
        if new_record:
            center("新紀錄！", small, 15, FOOD_COLOR[GOLDEN])
        center("空白鍵重新開始，Esc 離開", small, 50)
    elif paused:
        center("暫停中（P 繼續）", big)
    pygame.display.flip()


def main():
    pygame.init()
    screen = pygame.display.set_mode((COLS * CELL, TOP + ROWS * CELL))
    pygame.display.set_caption("貪食蛇")
    fonts = (load_font(40), load_font(20))
    clock = pygame.time.Clock()
    game = SnakeGame(COLS, ROWS)
    hs = HighScore(Path(__file__).parent / "highscore.json")
    paused, new_record, acc, tick = False, False, 0.0, 0

    while True:
        dt = clock.tick(60) / 1000
        tick += 1
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                return
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    pygame.quit()
                    return
                if ev.key in KEYS and game.alive and not paused:
                    game.turn(KEYS[ev.key])
                elif ev.key == pygame.K_p and game.alive:
                    paused = not paused
                elif ev.key in (pygame.K_SPACE, pygame.K_RETURN) and not game.alive:
                    game.reset()
                    new_record, acc = False, 0.0

        if game.alive and not paused:
            acc += dt
            step_time = 1 / game.speed
            while acc >= step_time and game.alive:
                acc -= step_time
                if game.step() == "dead" or not game.alive:
                    new_record = hs.submit(game.score)
        draw(screen, game, hs, fonts, paused, new_record, tick)


if __name__ == "__main__":
    main()
