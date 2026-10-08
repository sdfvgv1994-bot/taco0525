import math
import os
import random
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kaleidoscope"))

from pattern import Stroke, copies, random_pattern, symmetric_polylines, to_svg  # noqa: E402


def test_copies_rotation():
    pts = copies(100, 0, 4, False)
    expect = [(100, 0), (0, 100), (-100, 0), (0, -100)]
    for (x, y), (ex, ey) in zip(pts, expect):
        assert x == pytest.approx(ex, abs=1e-9) and y == pytest.approx(ey, abs=1e-9)


def test_copies_keep_radius_and_count():
    for n in (1, 6, 8, 13):
        for mirror in (False, True):
            pts = copies(30, 40, n, mirror)
            assert len(pts) == n * (2 if mirror else 1)
            assert all(math.hypot(x, y) == pytest.approx(50) for x, y in pts)


def test_mirror_reflects():
    (x0, y0), (x1, y1) = copies(10, 20, 1, True)
    assert (x0, y0) == (10, 20) and (x1, y1) == (10, -20)


def test_hue_changes_along_stroke():
    st = Stroke()
    st.add(0, 0, 0)
    st.add(100, 0)
    st.add(200, 0)
    hues = [p[2] for p in st.points]
    assert hues[0] < hues[1] < hues[2]


def test_symmetric_polylines_shape():
    st = Stroke()
    for i in range(5):
        st.add(i * 10, 5)
    lines = symmetric_polylines(st, 8, False)
    assert len(lines) == 8 and all(len(l) == 5 for l in lines)
    assert symmetric_polylines(Stroke(), 8, False) == []


def test_random_pattern_is_reproducible_and_nonempty():
    a = random_pattern(random.Random(5), 400)
    b = random_pattern(random.Random(5), 400)
    assert [s.points for s in a] == [s.points for s in b]
    assert 3 <= len(a) <= 5
    assert all(len(s.points) > 100 for s in a)
    assert all(math.hypot(x, y) <= 400 + 1e-6 for s in a for x, y, _ in s.points)


def test_svg_is_valid_xml():
    st = Stroke(width=4)
    st.add(0, 0, 0)
    st.add(50, 50)
    dot = Stroke()
    dot.add(10, 10, 120)
    svg = to_svg([st, dot], 8, True, (900, 900))
    root = ET.fromstring(svg.split("\n", 1)[1])
    ns = "{http://www.w3.org/2000/svg}"
    assert len(root.findall(f".//{ns}line")) == 16      # 8 份 × 2（鏡像）× 1 段
    assert len(root.findall(f".//{ns}circle")) == 16


def test_app_headless(tmp_path, monkeypatch):
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    import pygame
    import kaleidoscope as ui
    monkeypatch.setattr(ui, "SAVE_DIR", tmp_path)
    pygame.init()
    app = ui.App(pygame.display.set_mode((ui.W, ui.H)))

    def ev(t, **kw):
        return pygame.event.Event(t, **kw)

    app.handle(ev(pygame.MOUSEBUTTONDOWN, button=1, pos=(500, 450)))
    for x in range(500, 600, 10):
        app.handle(ev(pygame.MOUSEMOTION, pos=(x, 400), buttons=(1, 0, 0)))
    app.render()
    app.handle(ev(pygame.MOUSEBUTTONUP, button=1, pos=(600, 400)))
    assert len(app.strokes) == 1

    app.handle(ev(pygame.KEYDOWN, key=pygame.K_EQUALS, mod=0))
    assert app.n == 9
    app.handle(ev(pygame.KEYDOWN, key=pygame.K_z, mod=pygame.KMOD_CTRL))
    assert app.strokes == []
    app.handle(ev(pygame.KEYDOWN, key=pygame.K_r, mod=0))
    assert app.strokes
    app.render()
    app.handle(ev(pygame.KEYDOWN, key=pygame.K_s, mod=0))
    assert list(tmp_path.glob("*.svg"))
    assert app.handle(ev(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0)) is False
    pygame.quit()


def test_random_pattern_is_smooth():
    """相鄰兩點不應該跳太遠（曲線要連續）。"""
    for seed in range(30):
        for st in random_pattern(random.Random(seed), 400):
            for (x1, y1, _), (x2, y2, _) in zip(st.points, st.points[1:]):
                assert math.hypot(x2 - x1, y2 - y1) < 60, seed
