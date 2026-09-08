# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Counting what a routed path crosses.

``_path_collisions`` used to evaluate the skip-endpoint test inside the
segment loop, although it depends only on the OBSTACLE. Hoisting it is
the same arithmetic done once per obstacle rather than once per
obstacle-times-segment, and these tests pin the semantics it must keep.

The skip rule matters: a pin's home and the centroid sit INSIDE an
obstacle by construction, so counting those as crossings would make
every path look blocked and the router would prefer nonsense.
"""
from __future__ import annotations

from eda_agent.design.router import _path_collisions

BOX = (0, 0, 100, 100)


def test_a_path_clear_of_everything_crosses_nothing():
    assert _path_collisions([(200, 200, 300, 200)], [BOX], ()) == 0


def test_a_path_through_a_box_counts_once():
    assert _path_collisions([(-50, 50, 150, 50)], [BOX], ()) == 1


def test_an_obstacle_holding_a_skip_point_is_ignored():
    """The pin's own home sits inside its body by construction."""
    assert _path_collisions([(-50, 50, 150, 50)], [BOX], ((50, 50),)) == 0


def test_the_skip_rule_is_per_obstacle_not_per_segment():
    """The hoist must not change which obstacles are skipped.

    Two boxes, one holding the skip point and one not, and a path that
    crosses both. Exactly one should count, whatever order the loops
    run in.
    """
    far = (300, 0, 400, 100)
    segs = [(-50, 50, 500, 50)]
    assert _path_collisions(segs, [BOX, far], ((50, 50),)) == 1
    assert _path_collisions(segs, [far, BOX], ((50, 50),)) == 1


def test_a_segment_crossing_two_boxes_counts_once_for_that_segment():
    """The inner loop breaks on the first hit: the count is segments
    blocked, not obstacles hit."""
    far = (300, 0, 400, 100)
    assert _path_collisions([(-50, 50, 500, 50)], [BOX, far], ()) == 1


def test_each_segment_is_counted_separately():
    far = (300, 0, 400, 100)
    segs = [(-50, 50, 150, 50), (250, 50, 450, 50)]
    assert _path_collisions(segs, [BOX, far], ()) == 2


def test_no_obstacles_means_no_collisions():
    assert _path_collisions([(0, 0, 999, 999)], [], ()) == 0


def test_the_inlined_collision_test_agrees_with_the_function_it_replaced():
    """_path_collisions inlines _segment_crosses_rect, so the two can drift.

    The inlining is not for the arithmetic, which is four comparisons
    either way; it is for the CALL COUNT. That loop reached the function
    226 million times on one demo sheet, and removing the call took four
    sheets from 148.7s to 116.9s, a 21% cut, with rp2040 alone going
    136.0 to 105.9.

    A copy that silently disagrees would change routing decisions rather
    than crash, so this drives both over random axis-aligned geometry
    and requires identical answers.
    """
    import random

    from eda_agent.design.router import _path_collisions, _segment_crosses_rect

    def reference(segs, obstacles, skip):
        live = [
            o for o in obstacles
            if not any(min(o[0], o[2]) <= ex <= max(o[0], o[2])
                       and min(o[1], o[3]) <= ey <= max(o[1], o[3])
                       for ex, ey in skip)
        ]
        n = 0
        for seg in segs:
            for o in live:
                if _segment_crosses_rect(*seg, *o):
                    n += 1
                    break
        return n

    rng = random.Random(20260903)
    for _ in range(3000):
        segs = []
        for _s in range(rng.randint(1, 4)):
            x, y = rng.randrange(0, 40) * 10, rng.randrange(0, 40) * 10
            if rng.random() < 0.5:
                segs.append((x, y, rng.randrange(0, 40) * 10, y))
            else:
                segs.append((x, y, x, rng.randrange(0, 40) * 10))
        obstacles = [
            (rng.randrange(0, 30) * 10, rng.randrange(0, 30) * 10,
             rng.randrange(10, 40) * 10, rng.randrange(10, 40) * 10)
            for _o in range(rng.randint(0, 4))
        ]
        skip = tuple((rng.randrange(0, 40) * 10, rng.randrange(0, 40) * 10)
                     for _p in range(rng.randint(0, 2)))
        assert _path_collisions(segs, obstacles, skip) == reference(
            segs, obstacles, skip), (segs, obstacles, skip)
