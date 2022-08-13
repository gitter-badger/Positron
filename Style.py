import re
from abc import ABC
from collections import defaultdict
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import cache
from itertools import chain
from typing import (Callable, Generic, Iterable, Mapping, Protocol, TypeVar,
                    overload)

import tinycss

from config import (abs_border_width, abs_font_size, abs_font_weight,
                    abs_length_units, g, rel_font_size)
from own_types import (Auto, AutoType, Color, CompValue, FontStyle, Normal,
                       NormalType, Number, NumPerc, Percentage, ReadChain,
                       Sentinel, StyleComputed, StyleInput, frozendict)
from util import (bc_keys, bs_keys, bw_keys, check_regex, dec_re, fetch_src,
                  group_by_bool, inset_keys, int_re, log_error, marg_keys,
                  noop, not_neg, pad_keys)

################## Acceptor #################################


CompValue_T = TypeVar("CompValue_T", bound=CompValue, covariant=True)


class Acceptor(Protocol[CompValue_T]):
    def __call__(self, value: str, p_style: StyleComputed) -> None | CompValue_T:
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


# Melody
rgb_re = re.compile(
    r"(?:(?:rgb\(([+-]?\d+),([+-]?\d+),([+-]?\d+),?\))|(?:rgb\(([+-]?\d+),([+-]?\d+),([+-]?\d+),([+-]?\d+),?\)))"
)
rgba_re = re.compile(r"rgba\(([+-]?\d+),([+-]?\d+),([+-]?\d+),([+-]?\d+),?\)")


def color(value: str, p_style):
    if value == "currentcolor":
        return p_style["color"]
    value = ''.join(value.split()) # remove whitespace
    with suppress(ValueError):
        if (match := rgb_re.match(value) or rgba_re.match(value)) is not None:
            return Color(*map(int, filter(None, match.groups())))
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


split_units_pattern = re.compile(fr"({dec_re})(\w*|%)")

def split_units(attr: str) -> tuple[float, str]:
    """Split a dimension or percentage into a tuple of number and the "unit" """
    match = split_units_pattern.fullmatch(attr.strip())
    num, unit = match.groups()  # type: ignore # Raises AttributeError
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
class StyleAttr(Generic[CompValue_T]):
    initial: str
    kws: Mapping[str, CompValue_T]
    accept: Acceptor[CompValue_T]
    inherits: bool

    def __init__(
        self,
        initial: str,
        kws: set[StrSent] | Mapping[str, CompValue_T] = {},
        acc: Acceptor[CompValue_T] = noop,
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

    def set2dict(self, s: set) -> Mapping[str, CompValue_T]:
        return {x if isinstance(x, str) else x.name.lower(): x for x in s}

    def convert(self, value: str, p_style: StyleComputed) -> CompValue_T | None:
        kw = self.kws.get(value)
        return kw if kw is not None else self.accept(value, p_style)


####### Helpers ########

# we don't want copies of these (memory) + better readibility
auto: dict[str, AutoType] = {"auto": Auto}
normal: dict[str, NormalType] = {"normal": Normal}

alp = (auto, length_percentage)
aalp: tuple[str, dict[str, AutoType], Acceptor[NumPerc]] = (
    "auto",
    auto,
    length_percentage,
)
AALP = StyleAttr(*aalp)
BorderWidthAttr: StyleAttr[NumPerc] = StyleAttr(
    "medium", abs_border_width, length_percentage
)
BorderStyleAttr: StyleAttr[str] = StyleAttr(
    "none",
    {
        "none",  # implemented
        "hidden",  # partially implemented
        "dotted",
        "dashed",
        "solid",  # partially implemented
        "double",
        "groove",
        "ridge",
        "inset",
        "outset",
    },
    inherits=False,
)


def no_change(value: str, p_style) -> str:
    return value


Types = StyleAttr[CompValue]


prio_keys = {"color", "font-size"}


style_attrs: dict[str, Types] = {
    "color": StyleAttr("canvastext", acc=color),
    "font-weight": StyleAttr("normal", abs_font_weight, font_weight),
    "font-family": StyleAttr("Arial", acc=no_change),
    "font-size": StyleAttr("medium", acc=font_size),
    "font-style": StyleAttr("normal", acc=font_style),
    "line-height": StyleAttr("normal", normal, length_percentage, True),
    "word-spacing": StyleAttr("normal", normal, length_percentage, True),
    "display": StyleAttr("inline", {"inline", "block", "none"}),
    "background-color": StyleAttr("transparent", acc=color),
    "width": AALP,
    "height": AALP,
    "position": StyleAttr(
        "static", {"static", "relative", "absolute", "sticky", "fixed"}
    ),
    "box-sizing": StyleAttr("content-box", {"content-box", "border-box"}),
    **{key: AALP for key in chain(inset_keys, pad_keys, marg_keys)},
    **{key: BorderWidthAttr for key in bw_keys},
    **{key: StyleAttr("currentcolor", acc=color) for key in bc_keys},
    **{key: BorderStyleAttr for key in bs_keys},
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


###########################  CSS-Parsing ############################

"""
A list of stylesheets that applies styles to elements automatically
"""
############################# Types #######################################
class AtRule(ABC):
    pass


class ImportRule(tinycss.css21.ImportRule, AtRule):
    pass


class MediaRule(AtRule):
    def __init__(self, media: list[str], rules: "SourceSheet"):
        self.media = media
        self.rules = rules

    def matches(self, media: "Media"):
        """Whether a MediaRule matches a Media"""
        return True  # TODO


class PageRule(tinycss.css21.PageRule, AtRule):
    pass


Media = tuple[int, int]  # just the window size right now
Value = tuple[str, bool]  # actual value + important
Property = tuple[str, Value]
Style = dict[str, tuple[str, bool]]
StyleRule = tuple[str, Style]
"""
A style with a selector
Example:
p {
    color: red !important;
} -> (p, {color: (red, True)})
"""
StyleSheet = dict[str, Style]
Rule = AtRule | StyleRule


def join_styles(style1: Style, style2: Style):
    """
    Join two styles. Prefers the first
    """
    fused = style1 | style2
    return {
        **{
            k: fused[k] for k in style1 ^ style2.keys()
        },  # all keys that are in one but not both # type: ignore[list-item]
        **{
            k: style2[k] if style2[k][1] and not style1[k][1] else style1[k]
            for k in style1 & style2.keys()
        },  # all keys that are in both
    }


def is_imp(t: Value):
    return t[1]


IMPORTANT = "!important"


def parse_important(s: str) -> Value:
    return (s[: -len(IMPORTANT)], True) if s.endswith(IMPORTANT) else (s, False)


@overload
def remove_important(style: list[Property]) -> list[tuple[str, str]]:
    ...


@overload
def remove_important(style: Style) -> StyleInput:
    ...


def remove_important(style):
    """
    Remove the information whether a value in the style is important
    """
    d = style if isinstance(style, list) else style.items()
    return type(style)((k, v[0]) for k, v in d)


def add_important(style: StyleInput, imp: bool) -> Style:
    """
    Add the information whether a value in the style is important
    """
    return {k: (v, imp) for k, v in style.items()}


def group_imp(iter: Iterable[Value]):
    return group_by_bool(
        iter,
        lambda t: is_imp(t[1]),
    )


def get_media() -> Media:
    return g["W"], g["H"]


class SourceSheet(list[Rule]):
    """
    A list of AtRules or StyleRules.
    Represents a sheet from a source file.
    """

    _last_media_rules: tuple[Media, list[StyleRule]] | None = None

    @property
    def all_rules(self) -> list[StyleRule]:
        current_media = get_media()
        if self._last_media_rules is not None:
            lastmedia, lastrules = self._last_media_rules
            if lastmedia == current_media:
                return lastrules
        rv: list[StyleRule] = []
        for rule in self:
            if isinstance(rule, MediaRule) and rule.matches(current_media):
                rv.extend(rule.rules.all_rules)
            elif isinstance(rule, tuple):  # Just a regular StyleRule
                rv.append(rule)
        self._last_media_rules = (current_media, rv)
        return rv

    def __add__(self, other):
        return type(self)([*self, *other])

    def __hash__(self):
        return hash(tuple(self))

    @classmethod
    def join(cls, sheets):
        return cls(chain.from_iterable(sheets))

    def append(self, __object) -> None:
        raise ValueError("Immutable List")

    def extend(self, __iterable) -> None:
        raise ValueError("Immutable List")

    def __setitem__(self, __i, __v):
        raise ValueError("Immutable List")

    def __delitem__(self, __i) -> None:
        raise ValueError("Immutable List")


g["global_sheet"] = SourceSheet()

############################### Parsing functions #######################################


def parse_style(s: str) -> StyleInput:
    """
    Parse a style string. For example an inline style.
    Self-written right now
    """
    if not s:
        return {}
    data = s.removeprefix("{").removesuffix("}").strip().split(";")
    pre_parsed: list[tuple[str, str]] = [
        tuple(key.strip() for key in splitted)  # type: ignore # we assert that the length is 2
        for value in data
        if len(splitted := value.split(":")) == 2
        or log_error(f"CSS: Invalid style declaration ({value})")
    ]
    # Update the unimportant style with the important style
    return process_input(
        sorted(
            [(k, parse_important(string)) for k, string in pre_parsed],
            key=lambda t: is_imp(t[1]),
        )
    )


Parser = tinycss.CSS21Parser()


current_file: str | None = None  # TODO: not thread-safe


@contextmanager
def set_curr_file(file: str):
    global current_file
    current_file = file
    try:
        yield
    finally:
        current_file = None


def parse_file(source: str) -> SourceSheet:
    """
    Parses a file.
    It sets the current_file globally which is just for debugging purposes.
    """
    with set_curr_file(source):
        data = fetch_src(source)
        return parse_sheet(data)


def parse_sheet(source: str) -> SourceSheet:
    """
    Parses a whole css sheet
    """
    tiny_sheet: tinycss.css21.Stylesheet = Parser.parse_stylesheet(source)
    return SourceSheet(handle_rule(rule) for rule in tiny_sheet.rules)


def handle_rule(
    rule: tinycss.css21.RuleSet
    | tinycss.css21.ImportRule
    | tinycss.css21.MediaRule
    | tinycss.css21.PageRule
    | tinycss.css21.AtRule,
) -> Rule:
    """
    Converts a tinycss rule into an appropriate Rule
    """
    if isinstance(rule, tinycss.css21.RuleSet):
        return (
            rule.selector.as_css(),
            frozendict(
                process(
                    [
                        (decl.name, (decl.value.as_css().strip(), bool(decl.priority)))
                        for decl in rule.declarations
                    ]
                )
            ),
        )
    elif isinstance(rule, tinycss.css21.MediaRule):
        assert rule.at_keyword == "@media"
        return MediaRule(
            rule.media, SourceSheet(handle_rule(rule) for rule in rule.rules)
        )
    else:
        raise NotImplementedError("Not implemented AtRule: " + type(rule).__name__)


###########################  CSS Processing #########################
from pygame.colordict import THECOLORS

THECOLORS.update({"canvastext": (0, 0, 0, 255), "transparent": (0, 0, 0, 0)})

colors = set(["currentcolor", *THECOLORS])

guessing: dict[str, Callable[[str], bool]] = {
    "border-width": lambda value: value in BorderStyleAttr.kws,
    "border-color": lambda value: value in colors or color(value,{"color":"black"}),
    "border-style": lambda value: value in BorderWidthAttr.kws
    or check_regex("dimension", value),
}

directions = ("top", "right", "bottom", "left")
global_values = frozenset({"inherit", "initial", "unset", "revert"})

dir_fallbacks = {"right": "top", "bottom": "top", "left": "right"}
dir_shorthands = dict(
    [
        ("margin", "margin-{}"),
        ("padding", "padding-{}"),
        ("border-width", "border-{}-width"),
        ("border-color", "border-{}-color"),
        ("border-style", "border-{}-style"),
        ("inset", "{}"),
    ]
)
# smart shorthands are when the split depends on the values
smart_shorthands = {
    "border": {"border-width", "border-style", "border-color"},
    **{
        f"border-{k}": {
            f"border-{k}-width",
            f"border-{k}-style",
            f"border-{k}-color",
        }
        for k in directions
    },
}


def process(d: list[Property] | Style) -> Style:
    itr = d if isinstance(d, list) else d.items()
    imp, not_imp = group_by_bool(itr, lambda t: is_imp(t[1]))
    return add_important(
        process_input(remove_important(not_imp)), False
    ) | add_important(process_input(remove_important(imp)), True)


def process_input(d: list[tuple[str, str]]) -> StyleInput:
    """
    Unpacks shorthands and filters and reports invalid declarations
    """
    done: dict[str, str] = {}
    def inner(d: Iterable[tuple[str, str]]):
        for key, value in d:
            try:
                done.update(process_property(key, value))
            except AssertionError as e:
                log_error(f"CSS: {e.args[0] or 'Invalid Property'} ({key}: {value})")
    inner(d)
    while True:
        _done, todo = group_by_bool(
            done.items(), lambda item: item[0] in style_attrs
        )
        done = dict(_done)
        if not todo: break
        inner(todo)
    return done


def is_valid(name: str, value: str):
    """
    Checks whether the given CSS property is valid
    """
    if name in global_values:
        return True
    elif (validator := guessing.get(name)) is not None:
        return validator(value)
    elif (attr := style_attrs.get(name)) is not None:
        if name in attr.kws:
            return True
        with suppress(KeyError):
            if attr.accept(value, {}) is None:
                return False
        return True
    return False


def split(s: str):
    """
    This function is for splitting css values that include functions
    """
    rec = True
    result = []
    curr_string = ""
    brackets = 0
    for c in s:
        is_w = re.match(r"\s", c)
        if rec and not brackets and is_w:
            rec = False
            result.append(curr_string)
            curr_string = ""
        if not is_w:
            rec = True
            curr_string += c
        if c == "(":
            brackets += 1
        elif c == ")":
            assert brackets
            brackets -= 1
    if rec:
        result.append(curr_string)
    return result


def process_property(key: str, value: str) -> dict[str, str]:
    """Processes a single Property as described in the main process functions"""
    # TODO: font
    # TODO: border-radii
    arr = split(value)
    if key == "all":
        assert len(arr) == 1
        assert value in global_values
        return {k: value for k in style_attrs}
    elif key in dir_shorthands:
        assert len(arr) <= len(
            directions
        ), f"Too many values: {len(arr)}/{len(directions)}"
        fstring = dir_shorthands[key]
        _res = dict(zip(directions, arr))
        for k in directions[len(_res) :]:
            _res[k] = _res[dir_fallbacks[k]]
        return {fstring.format(k): v for k, v in _res.items()}
    elif (shorthand := smart_shorthands.get(key)) is not None:
        assert len(arr) <= len(
            shorthand
        ), f"Too many values: {len(arr)}, max {len(shorthand)}"
        result = {k: v for v in arr for k in shorthand if is_valid(k,v)}
        left = shorthand ^ result.keys()
        assert not left, f"Invalid key(s): {', '.join(left)}"
        return result
    else:
        assert len(arr) == 1
        value = arr[0]
        assert key in style_attrs, "Unknown Property"
        assert is_valid(key, value), "Invalid Value"
        return {key: value}
