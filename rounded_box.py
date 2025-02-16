# fmt: off
import math
from functools import cache
from typing import Sequence

import numpy as np
import pygame as pg
from pygame import gfxdraw

from own_types import (V_T, BugError, Color, Dimension, Float4Tuple, Radii, Rect,
                       Surface, Vector2)
from util import all_equal
# fmt: on

def mul_tup(tup1: Dimension, tup2: Dimension) -> tuple[float, float]:
    x1, y1 = tup1
    x2, y2 = tup2
    return (x1 * x2, y1 * y2)


def abs_tup(tup1: Dimension) -> Dimension:
    return type(tup1)([abs(x) for x in tup1])  # type: ignore


side_vectors: list[tuple[int, int]] = [(1, 0), (0, 1), (-1, 0), (0, -1)]

corner_vectors: list[tuple[int, int]] = [
    (1, 1),  # topleft
    (-1, 1),  # topright
    (-1, -1),  # bottomright
    (1, -1),  # bottomleft
]


def counter_vector(vector: Dimension) -> Vector2:
    r: tuple[float, float]
    match tuple(vector):
        case (x, 0):
            r = (0, x)
        case (0, x):
            r = (-x, 0)
    return Vector2(r)


_side2corners = (
    (0, 3),  # top then left
    (0, 1),  # top then right
    (2, 1),  # bottom then right
    (2, 3),  # bottom then left
)


def side2corners(sides: Sequence[V_T]) -> list[tuple[V_T, V_T]]:
    """
    Convert side data to corner data:
    [
        "black", # top
        "blue", # bottom
        "green", # left
        "red" # right
    ]
    ->
    [
        ("black","green"), # topleft
        ("black","red"), # topright
        ("blue","red"), # bottomright
        ("blue","green") # bottomleft
    ]
    """
    return [(sides[i1], sides[i2]) for i1, i2 in _side2corners]


_corner2sides = (
    (0, 1),  # topleft, topright
    (1, 2),  # topright, bottomright
    (2, 3),  # bottomright, bottomleft
    (3, 0),  # bottomleft, topleft
)


def adj_corners(corners: list[V_T]) -> list[tuple[V_T, V_T]]:
    """
    Return the adjacent corners of all four sides
    """
    return [(corners[i1], corners[i2]) for i1, i2 in _corner2sides]


@cache
def full_ellipse(size: tuple[int, int], color: Color, width: int) -> Surface:
    """
    Returns a surface with an ellipse drawn in it.
    """
    surface = pg.Surface(size, flags=pg.SRCALPHA)
    rect = Rect(0, 0, *size)
    if width == 0:
        dx, dy = size
        gfxdraw.filled_ellipse(surface, *rect.center, dx // 2, dy // 2, color)
    else:
        pg.draw.ellipse(surface, color, rect, width)
    return surface


def draw_rounded_border(
    surf: Surface,
    box: Rect,
    colors: tuple[Color, Color, Color, Color],
    widths: Float4Tuple,
    radii: tuple[tuple[int, int], ...],
):
    """
    Draw a rounded border
    """
    # TODO: add border-styles
    widths: tuple[int, int, int, int] = tuple(int(x) for x in widths)  # type: ignore
    if not any(widths):
        return
    if all_equal(widths) and all_equal(colors) and all(r1 == r2 for r1, r2 in radii):
        new_radii: list[int] = [r1 for r1, _ in radii]
        if all_equal(new_radii):
            pg.draw.rect(surf, colors[0], box, widths[0], new_radii[0])
        else:
            pg.draw.rect(surf, colors[0], box, widths[0], -1, *new_radii)
    else:
        # advanced border draw
        def draw_rect(color, rect):
            gfxdraw.box(surf, rect, color)

        def draw_arc(color, rect, start_angle, stop_angle, width):
            pg.draw.arc(surf, color, rect, start_angle, stop_angle, width)

        corners = [Vector2(x) for x in box.corners]
        vectors = [
            Vector2(mul_tup(vec, rad)) for vec, rad in zip(corner_vectors, radii)
        ]
        for corner, vec, ecolors, ewidths in zip(
            corners, vectors, side2corners(colors), side2corners(widths)
        ):
            angle_vec = vec * -1
            corner_rect = Rect.from_span(corner, corner + vec)
            ell_rect = Rect.from_span(corner, corner + 2 * vec)
            _, x_angle = Vector2(mul_tup(angle_vec, (1, 0))).as_polar()
            _, y_angle = Vector2(mul_tup(angle_vec, (0, 1))).as_polar()
            _, mid_angle = angle_vec.as_polar()
            x_angle, y_angle, mid_angle = (
                math.radians(-x) for x in (x_angle, y_angle, mid_angle)
            )
            switch = vec.x * vec.y > 0
            start_angle, stop_angle = (
                (y_angle, x_angle) if switch else (x_angle, y_angle)
            )
            if all(ewidths):
                if all_equal(ewidths) and all_equal(ecolors):
                    # draw a single arc
                    ellipse = full_ellipse(ell_rect.size, ecolors[0], ewidths[0])
                    ell_center = ellipse.get_rect().center
                    surf.blit(
                        ellipse,
                        corner_rect.topleft,
                        Rect.from_span(ell_center, angle_vec + ell_center),
                    )
                else:
                    # draw two arcs
                    corner_angles = (
                        ((start_angle, mid_angle), (mid_angle, stop_angle))
                        if switch
                        else ((mid_angle, stop_angle), (start_angle, mid_angle))
                    )  # if t[0] else
                    for color, angles, width in zip(ecolors, (corner_angles), ewidths):
                        # Problem with pygames arc function
                        # TODO: find solution (for example creating an svg file on the fly in memory and loading it in)
                        draw_arc(color, ell_rect, *angles, width)

        for vector, adjcorners, adjvectors, color, width in zip(
            side_vectors, adj_corners(corners), adj_corners(vectors), colors, widths
        ):
            absvec = abs_tup(vector)
            startpoint, stoppoint = adjcorners[0] + mul_tup(
                absvec, adjvectors[0]
            ), adjcorners[1] + mul_tup(absvec, adjvectors[1])
            counter = counter_vector(vector)
            draw_rect(color, Rect.from_span(startpoint, stoppoint + counter * width))


def draw_rounded_background(
    surf: Surface, box: Rect, bgcolor: Color, radii: tuple[tuple[int, int], ...]
):
    """
    Draw just a solid color background
    """
    if all(r1 == r2 for r1, r2 in radii):
        new_radii: list[int] = [r1 for r1, _ in radii]
        if all_equal(new_radii):
            pg.draw.rect(surf, bgcolor, box, border_radius=new_radii[0])
        else:
            pg.draw.rect(surf, bgcolor, box, 0, -1, *new_radii)
    else:

        def draw_rect(color, rect):
            gfxdraw.box(surf, rect, color)
            # pg.draw.rect(surf, color, rect)

        corners = [Vector2(x) for x in box.corners]
        vectors = [
            Vector2(mul_tup(vec, rad)) for vec, rad in zip(corner_vectors, radii)
        ]
        # draw the corners
        for corner, vec in zip(corners, vectors):
            angle_vec = vec * -1
            corner_rect = Rect.from_span(corner, corner + vec)
            ell_rect = Rect.from_span(corner, corner + 2 * vec)
            ellipse = full_ellipse(ell_rect.size, bgcolor, 0)
            ell_center = ellipse.get_rect().center
            surf.blit(
                ellipse,
                corner_rect.topleft,
                Rect.from_span(ell_center, angle_vec + ell_center),
            )
        # draw the rects between the sides and the corners
        for vector, adjcorners, adjvectors in zip(
            side_vectors, adj_corners(corners), adj_corners(vectors)
        ):
            absvec = abs_tup(vector)
            startpoint, stoppoint = adjcorners[0] + mul_tup(
                absvec, adjvectors[0]
            ), adjcorners[1] + mul_tup(absvec, adjvectors[1])
            counter = counter_vector(vector)
            abscounter = abs_tup(counter)
            draw_rect(
                bgcolor,
                Rect.from_span(
                    startpoint,
                    stoppoint
                    + max((mul_tup(abscounter, x) for x in adjvectors), key=len),
                ),
            )
        # draw the center part
        draw_rect(
            bgcolor, Rect.from_span(corners[0] + vectors[0], corners[2] + vectors[2])
        )


def round_surf(surf: Surface, size: Dimension, radii: tuple[tuple[int, int], ...]):
    """
    Clip the surface on the box with the given radii
    """
    box = Rect(0,0, *size)
    if not any(any(t) for t in radii):
        return surf
    size = surf.get_size()
    assert size == box.size, BugError(f"Surface is not equal to box")
    surf.convert_alpha()
    clip_surf = Surface(size, flags=pg.SRCALPHA)
    draw_rounded_background(clip_surf, box, Color("black"), radii)
    # here we need to copy the alpha value from the clip_surf to the surface
    surf_alpha = pg.surfarray.pixels_alpha(surf)
    np.copyto(surf_alpha, src=pg.surfarray.pixels_alpha(clip_surf))
