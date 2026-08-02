"""Regenerate the markup parity fixture.

The shell can render Evennia pipe-codes itself (``src/lib/markup.ts``) instead of
relying on the server's pre-parsed ``html``. That only stays safe if the two
parsers agree, so this dumps the server's :func:`evennia.utils.text2html.parse_html`
output for a representative corpus and the client asserts it matches.

Run it from inside a game directory (it needs Django settings):

    GAME_ENV=development python \\
        ../evennia/evennia/web/webclient/client/scripts/gen-markup-parity.py

Writes ``src/lib/__fixtures__/markup-parity.json`` relative to this script.
"""

from __future__ import annotations

import json
import os
import pathlib
import string

OUT = pathlib.Path(__file__).resolve().parent.parent / "src" / "lib" / "__fixtures__"

#: Single named foreground codes, bright and dark.
_FG = "nrgybmcwxRGYBMCWX"
#: Named background codes.
_BG = "rgybmcwxRGYBMCWX"


def cases() -> list[str]:
    """Build the corpus.

    Returns:
        list[str]: markup strings covering every code family the shell may meet.
    """
    out: list[str] = [
        "",
        "plain text",
        "pipe || literal",
        'amp & lt < gt > quote "',
        "trailing reset|n",
        "|rred|n then plain",
        "|r|gstacked|n",
        "unterminated |rcolour",
    ]
    out += [f"|{code}text|n" for code in _FG]
    out += [f"|[{code}text|n" for code in _BG]
    # xterm256 cube, foreground and background.
    for r in "05":
        for g in "05":
            for b in "05":
                out.append(f"|{r}{g}{b}rgb|n")
                out.append(f"|[{r}{g}{b}rgb|n")
    out += [f"|{c}0{c}mid|n" for c in "1234"]
    # Greyscale ramp, foreground and background.
    out += [f"|={ch}grey|n" for ch in string.ascii_lowercase]
    out += [f"|[={ch}grey|n" for ch in string.ascii_lowercase]
    # Formatting and whitespace codes.
    out += ["|uunder|n", "|u|rboth|n", "line|/break", "a|/|/b"]
    return out


def main() -> None:
    """Write the fixture next to the client sources."""
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.conf.settings")
    django.setup()

    from evennia.utils.text2html import parse_html

    payload = [{"src": src, "html": parse_html(src)} for src in cases()]
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "markup-parity.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(payload)} cases to {target}")


if __name__ == "__main__":
    main()
