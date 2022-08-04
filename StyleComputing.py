import re
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from functools import cache
from itertools import chain
from typing import Generic, Mapping, Protocol, TypeVar

from config import (
    abs_border_width,
    abs_font_size,
    abs_font_weight,
    abs_length_units,
    g,
    rel_font_size,
)
from own_types import (
    Auto,
    AutoType,
    Color,
    FontStyle,
    Normal,
    NormalType,
    Number,
    NumPerc,
    Percentage,
    ReadChain,
    Sentinel,
    style_computed,
    computed_value
)
from util import bc_keys, bw_keys, dec_re, inset_keys, int_re, marg_keys, noop, pad_keys

################## Acceptor #################################


ComputedValue_T = TypeVar("ComputedValue_T", bound = computed_value, covariant=True)

class Acceptor(Protocol[ComputedValue_T]):
    def __call__(self, value: str, p_style: style_computed) -> None | ComputedValue_T:
        ...


def _length(dimension: tuple[float, str], p_style):
    """
    Gets a dimension (a tuple of a number and any unit)
    and returns a pixel value as a Number
    Raises ValueError or TypeError if something is wrong with the input.

    See: https://developer.mozilla.org/en-US/docs/Web/CSS/length
    """
    num, s = dimension  # Raises ValueError if dimension has not exactly 2 entries
    if num == 0:
        return 0  # we don't even have to look at the unit. Especially because the unit might be the empty string
    abs_length: dict[str, float] = abs_length_units
    w: int = g["W"]
    h: int = g["H"]
    rv: float
    match num, s:
        # source:
        # https://developer.mozilla.org/en-US/docs/Learn/CSS/Building_blocks/Values_and_units
        # absolute values first--------------------------------------
        case x, key if key in abs_length:
            rv = abs_length[key] * x
        # now relative values --------------------------------------
        case x, "em":
            rv = p_style["font-size"] * x
        case x, "rem":
            rv = g["root"]._style["font-size"] * x
        # view-port-relative values --------------------------------------
        case x, "vw":
            rv = x * 0.01 * w
        case x, "vh":
            rv = x * 0.01 * h
        case x, "vmin":
            rv = x * 0.01 * min(w, h)
        case x, "vmax":
            rv = x * 0.01 * max(w, h)
        # TODO: ex, ic, ch, ((lh, rlh, cap)), (vb, vi, sv*, lv*, dv*)
        # See: https://developer.mozilla.org/en-US/docs/Web/CSS/length#relative_length_units_based_on_viewport
        case x, s if isinstance(x, Number) and isinstance(s, str):
            raise ValueError(f"{s} is not an accepted unit")
        case _:
            raise TypeError()
    return rv


pdc = r"\s*,\s*"
w = rf"((?:{int_re})*)"
rgb_re = re.compile(
    rf"rgb\({w}{pdc}{w}{pdc}{w}\)"
)  # TODO: Make that rgba is also accepted in rgb
rgba_re = re.compile(rf"rgba\({w}{pdc}{w}{pdc}{w}{pdc}{w}\)")


def color(value: str, p_style):
    if value == "currentcolor":
        return p_style["color"]
    elif (match := rgb_re.match(value)) is not None:
        return Color(*map(int, match.groups()))
    elif (match := rgba_re.match(value)) is not None:
        return Color(*map(int, match.groups()))
    with suppress(ValueError):
        return Color(value)


def font_size(value: str, p_style):
    if value in abs_font_size:
        return g["default_font_size"] * 1.2 ** abs_font_size[value]
    p_size: float = p_style["font-size"]
    if value in rel_font_size:
        return p_size * 1.2 ** rel_font_size[value]
    else:
        return length_percentage(value, p_style, p_size)


def font_weight(value: str, p_style):
    # https://drafts.csswg.org/css-fonts/#relative-weights
    p_size: float = p_style["font-weight"]
    if value == "lighter":
        if p_size < 100:
            return p_size
        elif p_size < 550:
            return 100
        elif p_size < 700:
            return 400
        elif p_size <= 1000:
            return 700
        else:
            raise ValueError
    elif value == "bolder":
        if p_size < 350:
            return 400
        elif p_size < 550:
            return 700
        elif p_size < 900:
            return 900
        else:
            return p_size
    else:
        with suppress(ValueError):
            n = float(value)
            if 0 < n <= 1000:
                return n


def font_style(value: str, p_style):
    # FontStyle does the most for us
    split = value.split()[:2]
    with suppress(AssertionError):
        return FontStyle(*split)  # type: ignore


split_units_pattern = re.compile(f"({dec_re})([a-z%]*)")


def split_units(attr: str) -> tuple[float, str]:
    """Split a dimension or percentage into a tuple of number and the "unit" """
    match = split_units_pattern.fullmatch(attr.strip())
    num, unit = match.groups()  # type: ignore
    return float(num), unit


def length(value: str, p_style):
    with suppress(AttributeError):
        return _length(split_units(value), p_style)


def length_percentage(value: str, p_style, mult: float | None = None):
    with suppress(AttributeError):
        num, unit = split_units(value)
        if unit == "%":
            return Percentage(num) if mult is None else mult * Percentage(num)
        else:
            return _length((num, unit), p_style)


################################## Style Data ################################
# To add a new style key, document it, add it here and then implement it in the draw or layout methods

StrSent = str | Sentinel

@dataclass
class StyleAttr(Generic[ComputedValue_T]):
    initial: str
    kws: Mapping[str, ComputedValue_T]
    accept: Acceptor[ComputedValue_T]
    inherits: bool

    def __init__(
        self,
        initial: str,
        kws: set[StrSent] | Mapping[str, ComputedValue_T] = {},
        acc: Acceptor[ComputedValue_T] = noop,
        inherits: bool = None,
    ):
        self.initial = initial
        self.kws = self.set2dict(kws) if isinstance(kws, set) else kws
        self.accept = acc
        inherits = acc is not length_percentage if inherits is None else inherits
        self.inherits = (
            inherits if inherits is not None else acc is not length_percentage
        )

    def __repr__(self) -> str:
        return f"StyleAttr(initial={self.initial}, kws={self.kws}, accept={self.accept.__name__}, inherits={self.inherits})"  # type: ignore

    def set2dict(self, s: set) -> Mapping[str, ComputedValue_T]:
        return {x if isinstance(x, str) else x.name.lower(): x for x in s}

    def convert(
        self, value: str, p_style: style_computed
    ) -> ComputedValue_T | None:
        kw = self.kws.get(value)
        return kw if kw is not None else self.accept(value, p_style)


####### Helpers ########
_color_style = ({"canvastext": Color("black"), "transparent": Color(0, 0, 0, 0)}, color)

# we don't want copies of these + better readibility
auto: dict[str, AutoType] = {"auto": Auto}
normal: dict[str, NormalType] = {"normal": Normal}

alp = (auto, length_percentage)
aalp: tuple[str, dict[str, AutoType], Acceptor[NumPerc]] = (
    "auto",
    auto,
    length_percentage,
)
AALP = StyleAttr(*aalp)
BorderAttr: StyleAttr[NumPerc] = StyleAttr(
    "medium", abs_border_width, length_percentage
)

def no_change(value: str, p_style) -> str:
    return value

Types = StyleAttr[computed_value]

style_attrs: dict[str, Types] = {
    "color": StyleAttr("canvastext", *_color_style),
    "font-weight": StyleAttr("normal", abs_font_weight, font_weight),
    "font-family": StyleAttr("Arial", acc=no_change),
    "font-size": StyleAttr("medium", acc=font_size),
    "font-style": StyleAttr("normal", acc=font_style),
    "line-height": StyleAttr("normal", normal, length_percentage, True),
    "word-spacing": StyleAttr("normal", normal, length_percentage, True),
    "display": StyleAttr("inline", {"inline", "block", "none"}),
    "background-color": StyleAttr("transparent", *_color_style),
    "width": AALP,
    "height": AALP,
    "position": StyleAttr(
        "static", {"static", "relative", "absolute", "sticky", "fixed"}, acc=noop
    ),
    "box-sizing": StyleAttr("content-box", {"content-box", "border-box"}),
    **{key: AALP for key in chain(inset_keys, pad_keys, marg_keys)},
    **{key: BorderAttr for key in bw_keys},
    **{key: StyleAttr("black", *_color_style) for key in bc_keys},
}

abs_default_style = {
    k: "inherit" if v.inherits else v.initial for k, v in style_attrs.items()
}
""" The default style for a value (just like "unset") """

element_styles = defaultdict(
    dict,
    {
        "html": {
            **{k: attr.initial for k, attr in style_attrs.items() if attr.inherits},
            "display": "block",
        },
        # special elements
        "comment": {"display": "none"},
        "head": {
            "display": "none",
        },
        # "h1": {"font-size": "30px"},
        # "p": {
        #     "display": "block",
        #     "margin-top": "1em",
        #     "margin-bottom": "1em",
        # },
    },
)


@cache
def get_style(tag: str):
    return ReadChain(element_styles[tag], abs_default_style)


def test():
    import pytest

    assert split_units("3px") == (3, "px")
    assert split_units("0") == (0, "")
    assert split_units("70%") == (70, "%")

    assert color("rgb(120,120,120)", {}) == Color(*(120,) * 3)
    assert color("rgba(120,120,120,120)", {}) == Color(*(120,) * 4)
    assert color("currentcolor", {"color": Color("blue")}) == Color("blue")
    with pytest.raises(ValueError):
        split_units("blue")


if __name__ == "__main__":
    # print StyleAttrs
    for key, attr in style_attrs.items():
        if attr.inherits:
            print(attr)
