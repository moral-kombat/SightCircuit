"""The box panel's name font: fixed-cell glyph bitmaps for exactly the characters a label can hold.

Drawn for this project, by hand, as original work; no font file or
third-party glyph data is used. A glyph is ``CELL_HEIGHT`` rows of
``CELL_WIDTH`` cells (``#`` set, ``.`` clear): rows 0 to 6 hold a capital,
rows 2 to 6 a small letter's body, rows 7 and 8 a descender and the
underscore. Characters advance by ``ADVANCE`` cells, so a text of ``n``
characters is ``n * ADVANCE - 1`` cells wide. The renderer scales cells by a
whole number and draws them itself, so a name's pixels do not depend on any
library's text rasteriser. ``FONT_SHA256`` pins every glyph, and the box
panel's configuration carries it.
"""
import hashlib
import json

__all__ = ("ADVANCE", "CAP_ROWS", "CELL_HEIGHT", "CELL_WIDTH", "CHARACTERS", "FONT_ID", "FONT_SHA256", "GLYPHS",
           "text_cells", "text_width")

FONT_ID = "sightcircuit-cell-5x9-v1"
CELL_WIDTH, CELL_HEIGHT = 5, 9
ADVANCE = 6  # One clear column between characters.
CAP_ROWS = 7  # Rows a capital or a digit fills, from the top of the cell.
# What ``contract.row_label`` can emit: ASCII letters, digits, space, underscore and hyphen.
CHARACTERS = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ" "abcdefghijklmnopqrstuvwxyz" "0123456789" " _-")

_TALL = {  # Capitals and digits: seven rows; the two descender rows are clear.
    "A": ".###. #...# #...# ##### #...# #...# #...#",
    "B": "####. #...# #...# ####. #...# #...# ####.",
    "C": ".###. #...# #.... #.... #.... #...# .###.",
    "D": "####. #...# #...# #...# #...# #...# ####.",
    "E": "##### #.... #.... ####. #.... #.... #####",
    "F": "##### #.... #.... ####. #.... #.... #....",
    "G": ".###. #...# #.... #.### #...# #...# .###.",
    "H": "#...# #...# #...# ##### #...# #...# #...#",
    "I": ".###. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
    "J": "..### ...#. ...#. ...#. ...#. #..#. .##..",
    "K": "#...# #..#. #.#.. ##... #.#.. #..#. #...#",
    "L": "#.... #.... #.... #.... #.... #.... #####",
    "M": "#...# ##.## #.#.# #.#.# #...# #...# #...#",
    "N": "#...# ##..# ##..# #.#.# #..## #..## #...#",
    "O": ".###. #...# #...# #...# #...# #...# .###.",
    "P": "####. #...# #...# ####. #.... #.... #....",
    "Q": ".###. #...# #...# #...# #.#.# #..#. .##.#",
    "R": "####. #...# #...# ####. #.#.. #..#. #...#",
    "S": ".#### #.... #.... .###. ....# ....# ####.",
    "T": "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    "U": "#...# #...# #...# #...# #...# #...# .###.",
    "V": "#...# #...# #...# #...# .#.#. .#.#. ..#..",
    "W": "#...# #...# #...# #.#.# #.#.# ##.## #...#",
    "X": "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
    "Y": "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..",
    "Z": "##### ....# ...#. ..#.. .#... #.... #####",
    "0": ".###. #...# #..## #.#.# ##..# #...# .###.",
    "1": "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
    "2": ".###. #...# ....# ...#. ..#.. .#... #####",
    "3": "####. ....# ....# .###. ....# ....# ####.",
    "4": "...#. ..##. .#.#. #..#. ##### ...#. ...#.",
    "5": "##### #.... ####. ....# ....# #...# .###.",
    "6": ".###. #.... #.... ####. #...# #...# .###.",
    "7": "##### ....# ...#. ..#.. ..#.. ..#.. ..#..",
    "8": ".###. #...# #...# .###. #...# #...# .###.",
    "9": ".###. #...# #...# .#### ....# ....# .###.",
    "b": "#.... #.... ####. #...# #...# #...# ####.",
    "d": "....# ....# .#### #...# #...# #...# .####",
    "f": "..##. .#... ###.. .#... .#... .#... .#...",
    "h": "#.... #.... ####. #...# #...# #...# #...#",
    "i": "..#.. ..... .##.. ..#.. ..#.. ..#.. .###.",
    "k": "#.... #.... #..#. #.#.. ##... #.#.. #..#.",
    "l": ".##.. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
    "t": ".#... .#... ###.. .#... .#... .#..# ..##.",
}
_SMALL = {  # Small letters without an ascender: five rows from row 2, then any descender rows.
    "a": ".###. ....# .#### #...# .####",
    "c": ".###. #...# #.... #...# .###.",
    "e": ".###. #...# ##### #.... .###.",
    "g": ".#### #...# #...# #...# .#### ....# .###.",
    "m": "##.#. #.#.# #.#.# #.#.# #.#.#",
    "n": "####. #...# #...# #...# #...#",
    "o": ".###. #...# #...# #...# .###.",
    "p": "####. #...# #...# #...# ####. #.... #....",
    "q": ".#### #...# #...# #...# .#### ....# ....#",
    "r": "#.##. ##..# #.... #.... #....",
    "s": ".#### #.... .###. ....# ####.",
    "u": "#...# #...# #...# #...# .####",
    "v": "#...# #...# #...# .#.#. ..#..",
    "w": "#...# #.#.# #.#.# #.#.# .#.#.",
    "x": "#...# .#.#. ..#.. .#.#. #...#",
    "y": "#...# #...# #...# #...# .#### ....# .###.",
    "z": "##### ...#. ..#.. .#... #####",
}
_CLEAR = "." * CELL_WIDTH


def _cell(rows, top):
    rows = [_CLEAR] * top + rows.split()
    return tuple(rows + [_CLEAR] * (CELL_HEIGHT - len(rows)))


GLYPHS = {
    **{character: _cell(rows, 0) for character, rows in _TALL.items()},
    **{character: _cell(rows, 2) for character, rows in _SMALL.items()},
    "j": _cell("...#. ..... ..##. ...#. ...#. ...#. ...#. #..#. .##..", 0),
    " ": _cell("", 0),
    "-": _cell(".###.", 4),
    "_": _cell("#####", 7),
}
if (set(GLYPHS) != set(CHARACTERS) or len(CHARACTERS) != 65 or
        any(len(glyph) != CELL_HEIGHT or any(len(row) != CELL_WIDTH or set(row) - set("#.") for row in glyph)
            for glyph in GLYPHS.values())):
    raise ValueError("box_font_glyphs_invalid")
FONT_SHA256 = hashlib.sha256(json.dumps(
    {"id": FONT_ID, "cell": [CELL_WIDTH, CELL_HEIGHT], "advance": ADVANCE, "glyphs": GLYPHS},
    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def text_width(text, scale=1):
    """Width of ``text`` in pixels at a whole-number ``scale``; 0 for the empty text."""
    return max(0, len(text) * ADVANCE - (ADVANCE - CELL_WIDTH)) * scale


def text_cells(text):
    """``text`` as ``CELL_HEIGHT`` rows of ``text_width(text)`` booleans; a character without a glyph is a ``KeyError``."""
    gap = [False] * (ADVANCE - CELL_WIDTH)
    rows = []
    for row in range(CELL_HEIGHT):
        cells = []
        for index, character in enumerate(text):
            cells += ([] if index == 0 else gap) + [cell == "#" for cell in GLYPHS[character][row]]
        rows.append(cells)
    return rows
