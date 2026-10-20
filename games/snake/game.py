"""貪食蛇的遊戲邏輯（不含畫面，方便測試）。"""
from __future__ import annotations

import json
import random
from pathlib import Path

UP, DOWN, LEFT, RIGHT = (0, -1), (0, 1), (-1, 0), (1, 0)
NORMAL, GOLDEN = "normal", "golden"
POINTS = {NORMAL: 1, GOLDEN: 5}
GOLDEN_CHANCE = 0.25
MAX_QUEUED_TURNS = 2


class SnakeGame:
    def __init__(self, cols: int = 24, rows: int = 18, rng: random.Random | None = None):
        self.cols, self.rows = cols, rows
        self.rng = rng or random.Random()
        self.reset()

    def reset(self):
        cx, cy = self.cols // 2, self.rows // 2
        self.snake = [(cx, cy), (cx - 1, cy), (cx - 2, cy)]  # 頭在最前面
        self.direction = RIGHT
        self.turns: list = []     # 排隊中的轉向，連按也不會直接迴轉
        self.score = 0
        self.eaten = 0
        self.alive = True
        self.grow = 0
        self.food, self.food_kind = None, NORMAL
        self.spawn_food()

    # ---- 輸入 ----
    def turn(self, d) -> bool:
        """要求轉向。和「最後一個已排隊方向」相反或相同就忽略。"""
        last = self.turns[-1] if self.turns else self.direction
        if d == last or (d[0] == -last[0] and d[1] == -last[1]):
            return False
        if len(self.turns) >= MAX_QUEUED_TURNS:
            return False
        self.turns.append(d)
        return True

    # ---- 果子 ----
    def spawn_food(self):
        free = [(x, y) for x in range(self.cols) for y in range(self.rows)
                if (x, y) not in set(self.snake)]
        if not free:            # 蛇塞滿整個場地 = 破關
            self.food = None
            self.alive = False
            return
        self.food = self.rng.choice(free)
        self.food_kind = GOLDEN if self.rng.random() < GOLDEN_CHANCE else NORMAL

    # ---- 前進一格 ----
    def step(self) -> str | None:
        """回傳事件：'eat' / 'golden' / 'dead' / None。"""
        if not self.alive:
            return None
        if self.turns:
            self.direction = self.turns.pop(0)
        hx, hy = self.snake[0]
        new = (hx + self.direction[0], hy + self.direction[1])

        if not (0 <= new[0] < self.cols and 0 <= new[1] < self.rows):
            self.alive = False
            return "dead"
        # 尾巴這一步會移開，所以撞到「目前的尾巴」不算（除非正在長大）
        body = self.snake if self.grow else self.snake[:-1]
        if new in body:
            self.alive = False
            return "dead"

        self.snake.insert(0, new)
        event = None
        if new == self.food:
            kind = self.food_kind
            self.score += POINTS[kind]
            self.eaten += 1
            self.grow += 1
            event = "golden" if kind == GOLDEN else "eat"
            self.spawn_food()
        if self.grow:
            self.grow -= 1
        else:
            self.snake.pop()
        return event

    @property
    def speed(self) -> float:
        """每秒幾步，吃越多越快，上限 20。"""
        return min(8 + self.eaten * 0.4, 20)


class HighScore:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.best = int(json.loads(self.path.read_text())["best"])
        except (OSError, ValueError, KeyError, TypeError):
            self.best = 0

    def submit(self, score: int) -> bool:
        """有破紀錄就存檔並回傳 True。"""
        if score <= self.best:
            return False
        self.best = score
        self.path.write_text(json.dumps({"best": score}))
        return True
