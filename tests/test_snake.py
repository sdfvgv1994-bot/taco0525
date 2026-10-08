import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "snake"))

from game import DOWN, GOLDEN, LEFT, NORMAL, RIGHT, UP, HighScore, SnakeGame  # noqa: E402


def new_game(**kw):
    return SnakeGame(10, 10, rng=random.Random(1), **kw)


def test_moves_forward():
    g = new_game()
    head = g.snake[0]
    g.food = (0, 0)
    g.step()
    assert g.snake[0] == (head[0] + 1, head[1]) and len(g.snake) == 3


def test_cannot_reverse_directly():
    g = new_game()
    assert not g.turn(LEFT)  # 正在往右，不能直接往左


def test_rapid_double_press_does_not_reverse():
    g = new_game()
    # 往右走時快速按 上、左：應依序執行，不會在同一格迴轉撞到自己
    assert g.turn(UP) and g.turn(LEFT)
    g.food = (0, 0)
    assert g.step() is None and g.direction == UP
    assert g.step() is None and g.direction == LEFT
    assert g.alive


def test_down_then_up_is_rejected_when_queued():
    g = new_game()
    assert g.turn(DOWN)
    assert not g.turn(UP)  # 和排隊中的 DOWN 相反


def test_eat_normal_food():
    g = new_game()
    hx, hy = g.snake[0]
    g.food, g.food_kind = (hx + 1, hy), NORMAL
    assert g.step() == "eat"
    assert g.score == 1
    g.food = (0, 0)
    g.step()
    assert len(g.snake) == 4


def test_eat_golden_food():
    g = new_game()
    hx, hy = g.snake[0]
    g.food, g.food_kind = (hx + 1, hy), GOLDEN
    assert g.step() == "golden" and g.score == 5


def test_golden_probability_about_quarter():
    g = SnakeGame(10, 10, rng=random.Random(42))
    kinds = []
    for _ in range(4000):
        g.spawn_food()
        kinds.append(g.food_kind)
    ratio = kinds.count(GOLDEN) / len(kinds)
    assert 0.22 < ratio < 0.28


def test_food_never_on_snake():
    g = new_game()
    for _ in range(500):
        g.spawn_food()
        assert g.food not in g.snake


def test_wall_kills():
    g = new_game()
    g.food = (0, 0)
    for _ in range(10):
        g.step()
    assert not g.alive


def test_self_collision_kills():
    g = new_game()
    g.snake = [(5, 5), (4, 5), (4, 4), (5, 4), (6, 4), (6, 5)]
    g.direction = UP
    g.food = (0, 0)
    assert g.step() == "dead"


def test_can_follow_own_tail():
    g = new_game()
    # 四格繞圈，頭移到尾巴目前的位置是合法的（尾巴同時移開）
    g.snake = [(5, 5), (5, 4), (4, 4), (4, 5)]
    g.direction = LEFT
    g.food = (0, 0)
    g.step()
    assert g.alive


def test_highscore(tmp_path):
    p = tmp_path / "hs.json"
    hs = HighScore(p)
    assert hs.best == 0
    assert hs.submit(7)
    assert not hs.submit(3)
    assert HighScore(p).best == 7
    p.write_text("garbage")
    assert HighScore(p).best == 0


def test_speed_increases_and_caps():
    g = new_game()
    s0 = g.speed
    g.eaten = 5
    assert g.speed > s0
    g.eaten = 1000
    assert g.speed == 20


def test_headless_frames(monkeypatch):
    """用 dummy 視訊驅動實際跑幾個畫面，確認繪圖不會出錯。"""
    import os
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    import pygame
    import snake as ui
    pygame.init()
    screen = pygame.display.set_mode((ui.COLS * ui.CELL, ui.TOP + ui.ROWS * ui.CELL))
    fonts = (ui.load_font(40), ui.load_font(20))
    g = SnakeGame(ui.COLS, ui.ROWS)
    hs = HighScore(Path("/nonexistent/hs.json"))
    for t in range(5):
        g.step()
        ui.draw(screen, g, hs, fonts, False, False, t)
    g.alive = False
    ui.draw(screen, g, hs, fonts, False, True, 0)
    pygame.quit()
