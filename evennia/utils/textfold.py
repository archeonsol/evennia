"""
Fit Unicode text to what a client can display.

Game text is Unicode: frames drawn with box-drawing characters, bars made of
blocks, arrows, typographic quotes and dashes. A client that decodes UTF-8
shows all of it. A telnet client left in ASCII or Latin-1 mode does not: each
multi-byte character arrives as two or three bytes it cannot decode, and it
shows them as replacement diamonds or as mojibake (`â”€` for `─`), which turns
a status screen into noise.

`fold_text` rewrites the text for such a client. Characters with a plain
equivalent get it (`─` is `-`, `┌` is `+`, `→` is `>`, `—` is `-`, `█` is
`#`), letters lose their accents (`é` is `e`), and anything left is a single
`?`. Given the name of a legacy encoding the player chose, such as cp437 or
latin-1, it keeps every character that encoding can carry and folds only the
rest.

Every character folds to exactly one. Screens are laid out one column per
character, and a fold that wrote `--` for `—` or `->` for `→` pushed every
border after it out of line.

ASCII text is returned unchanged (the same object), so the common case costs
one `str.isascii()` call.

"""

import codecs
import unicodedata
from functools import lru_cache

__all__ = ["fold_text"]

_UTF8_NAMES = frozenset(("utf-8", "utf8", "utf_8", "u8", "utf"))

_HORIZONTAL = frozenset(("LEFT", "RIGHT", "HORIZONTAL"))
_VERTICAL = frozenset(("UP", "DOWN", "VERTICAL"))


def _box_drawing(char):
    """The plain character a box-drawing character stands for."""
    name = unicodedata.name(char, "")
    if "DIAGONAL" in name:
        if "CROSS" in name:
            return "X"
        return "/" if "UPPER RIGHT TO LOWER LEFT" in name else "\\"
    words = set(name.split())
    horizontal = bool(words & _HORIZONTAL)
    vertical = bool(words & _VERTICAL)
    if horizontal and not vertical:
        return "=" if "DOUBLE HORIZONTAL" in name else "-"
    if vertical and not horizontal:
        return "|"
    # Corners, tees and crosses.
    return "+"


def _build_table():
    """Map every character with a plain equivalent to that equivalent."""
    table = {}
    for code in range(0x2500, 0x2580):
        table[code] = _box_drawing(chr(code))
    # Block elements: the shades read as density, everything solid as a fill.
    for code in range(0x2580, 0x25A0):
        table[code] = "#"
    table.update({ord("░"): ".", ord("▒"): ":", ord("▓"): "#"})
    plain = {
        # Shapes. Filled reads as a full segment of a bar, hollow as an empty one.
        "■": "#",
        "▪": "#",
        "▬": "#",
        "▮": "#",
        "▰": "#",
        "◼": "#",
        "◾": "#",
        "□": ".",
        "▫": ".",
        "▭": ".",
        "▯": ".",
        "▱": ".",
        "◻": ".",
        "◽": ".",
        "●": "*",
        "•": "*",
        "◆": "*",
        "♦": "*",
        "★": "*",
        "☆": "*",
        "◉": "*",
        "○": "o",
        "◇": "o",
        "◯": "o",
        "◦": "o",
        "▲": "^",
        "△": "^",
        "▴": "^",
        "▵": "^",
        "▼": "v",
        "▽": "v",
        "▾": "v",
        "▿": "v",
        "►": ">",
        "▶": ">",
        "▸": ">",
        "▹": ">",
        "▷": ">",
        "◄": "<",
        "◀": "<",
        "◂": "<",
        "◃": "<",
        "◁": "<",
        "✓": "+",
        "✔": "+",
        "✗": "x",
        "✘": "x",
        "×": "x",
        "÷": "/",
        # Arrows.
        "→": ">",
        "←": "<",
        "↑": "^",
        "↓": "v",
        "↔": "-",
        "↕": "|",
        "⇒": ">",
        "⇐": "<",
        "⇔": "=",
        "➜": ">",
        "➤": ">",
        # Typography.
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "′": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "″": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "−": "-",
        "—": "-",
        "―": "-",
        "…": ".",
        "·": ".",
        "‧": ".",
        "∙": ".",
        "⁄": "/",
        "‹": "<",
        "›": ">",
        "«": "<",
        "»": ">",
        "±": "+",
        "°": "o",
        "©": "c",
        "®": "R",
        "™": "T",
        "¢": "c",
        "£": "L",
        "¥": "Y",
        "€": "E",
        "¡": "!",
        "¿": "?",
        "§": "S",
        "¶": "P",
        # Spaces of every width are a space, and invisible joiners too, so the
        # line keeps its width.
        "\u00a0": " ",
        "\u2002": " ",
        "\u2003": " ",
        "\u2007": " ",
        "\u2008": " ",
        "\u2009": " ",
        "\u200a": " ",
        "\u202f": " ",
        "\u3000": " ",
        "\u200b": " ",
        "\u200c": " ",
        "\u200d": " ",
        "\u2060": " ",
        "\ufeff": " ",
        # Letters NFKD does not decompose.
        "ß": "s",
        "Æ": "A",
        "æ": "a",
        "Œ": "O",
        "œ": "o",
        "Ø": "O",
        "ø": "o",
        "Đ": "D",
        "đ": "d",
        "Ł": "L",
        "ł": "l",
        "Þ": "T",
        "þ": "t",
        "ı": "i",
    }
    table.update({ord(char): repl for char, repl in plain.items()})
    assert all(len(repl) == 1 for repl in table.values()), "every fold is one character"
    return table


_TABLE = _build_table()


@lru_cache(maxsize=4096)
def _fold_char(char):
    """A character with no entry in the table, folded to one ASCII character."""
    decomposed = unicodedata.normalize("NFKD", char)
    base = "".join(c for c in decomposed if not unicodedata.combining(c)).translate(_TABLE)
    # One character only: "½" decomposes to "1/2", which would widen the line.
    return base if len(base) == 1 and base.isascii() else "?"


@lru_cache(maxsize=32)
def _codec(encoding):
    """The normalised codec name for an encoding, or None if there is none."""
    try:
        return codecs.lookup(encoding).name
    except (LookupError, TypeError):
        return None


@lru_cache(maxsize=8192)
def _carries(codec, char):
    """Whether a codec can encode a character."""
    try:
        char.encode(codec)
    except UnicodeEncodeError:
        return False
    return True


def fold_text(text, encoding="ascii"):
    """
    Rewrite text so a client in `encoding` can display all of it.

    Args:
        text (str): The text to send. ANSI and other escape sequences are
            ASCII and pass through untouched.
        encoding (str, optional): What the client decodes. The default,
            "ascii", folds every non-ASCII character. A legacy encoding
            (e.g. "cp437", "latin-1") keeps the characters it can carry.
            UTF-8 needs no folding. An unknown encoding is treated as ASCII.

    Returns:
        str: The folded text. Text that needs no folding is returned as the
        same object.

    """
    if not text or text.isascii():
        return text
    codec = _codec(encoding) or "ascii"
    if codec.replace("_", "-") == "utf-8" or (encoding or "").lower() in _UTF8_NAMES:
        return text
    if codec == "ascii":
        # The table in one C-level pass, then only what it did not cover.
        text = text.translate(_TABLE)
        if text.isascii():
            return text
        return "".join(char if char.isascii() else _fold_char(char) for char in text)
    out = []
    for char in text:
        if char.isascii() or _carries(codec, char):
            out.append(char)
        else:
            code = ord(char)
            out.append(_TABLE[code] if code in _TABLE else _fold_char(char))
    return "".join(out)
