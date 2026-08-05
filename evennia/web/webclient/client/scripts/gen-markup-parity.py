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
    # Real newlines and tabs in the body, as opposed to the |/ and |- codes.
    # The shell used to leave these alone, so a multi-line body rendered as one
    # run-on line -- `\n` is just whitespace to HTML.
    out += [
        "line one\nline two",
        "a\tb",
        "a\t\tb",
        "crlf\r\nend",
        "cr\rend",
        "trailing\n",
        "|rred\nstill red|n",
        "|rred|n\nplain",
        "\n\nleading blanks",
    ]
    # MXP links. The whole family was missing from this corpus, so the parity
    # suite stayed green while the shell rendered `|lc@xp attrs|lt[X]|le` as the
    # literal "c@xp attrst[X]e" -- it read `|lc` as the unknown colour code `|l`
    # and let the orphaned letter fall through as text.
    out += [
        # The shape the game actually emits (see world/rpg/xp_shell.py).
        "|lc@xp attrs|lt|w[Attributes]|n|n|le",
        "|lclook|ltlook here|le",
        "plain then |lclook|ltclick|le then plain",
        # Colour spanning across a link, and colour only inside one.
        "|r|lclook|ltred link|le|n",
        "|lclook|lt|gcoloured|n|le",
        # Quotes and HTML metacharacters in both halves: the server escapes the
        # groups in its text pass and then turns `"` into a backslashed entity,
        # which is not what it does to `"` in ordinary text.
        '|lcsay "hi"|ltquoted|le',
        "|lcsay <b>|lt<b>bold</b>|le",
        "|lcsay a & b|lta & b|le",
        # Degenerate: empty halves, and markers that never complete.
        "|lc|lt|le",
        "|lc|ltonly text|le",
        "unclosed |lclook|ltclick",
        "|lt orphan separator |le",
        "|le alone",
        # Two links in one line, the dossier's actual layout.
        "|lc@xp attrs|lt[A]|le · |lc@xp skills|lt[S]|le",
        # URL links are the other half of the family.
        "|luhttps://example.com|ltsite|le",
        "|luhttps://example.com/a?b=1&c=2|ltquery|le",
    ]
    # Bare URLs. Upstream auto-links them in a final pass, and does it with
    # `search` rather than `sub` -- so only the *first* URL in a string becomes a
    # link and any others stay text. Reproduced, not corrected.
    out += [
        "visit https://example.com now",
        "https://example.com",
        "http://example.com/a?b=1&c=2",
        "www.example.com",
        "ftp.example.com/pub",
        # Only the first is linked, which is the quirk worth pinning.
        "https://one.example.com and https://two.example.com",
        # Trailing punctuation is handed back outside the anchor.
        "see https://example.com.",
        "see https://example.com. and more",
        # No protocol and not a valid bare host: upstream bails on the whole
        # string, linking nothing at all.
        "www.x",
        "https://",
        # Already inside an anchor: the lookbehind must stop a second pass.
        "|luhttps://example.com|ltsite|le",
        "|lchttps://example.com|ltcmd|le",
        # Colour around and inside a URL.
        "|rhttps://example.com|n",
        "before |ghttps://example.com|n after",
        # Escaped entities adjacent to a URL.
        "https://example.com&amp; trailing",
        "a <b> https://example.com",
        # `$` in the upstream pattern is Python's, which also matches before a
        # final newline; JavaScript's does not. Pin the difference either way.
        "https://example.com\n",
        "https://example.com.\n",
        "line one\nhttps://example.com",
        "https://example.com|/after",
        # Punctuation and case handling around the host.
        "HTTPS://EXAMPLE.COM",
        "https://example.com/a_b~c",
        "(https://example.com)",
        "https://example.com, next",
    ]
    # Every single-character code, known or not. The shell used to drop anything
    # it did not recognise, while the server leaves an unknown code as literal
    # text -- so `|lz` came out as "z". Sweeping the whole range makes the
    # fixture the specification instead of relying on someone listing the codes
    # that matter, which is how the `|l` family got missed in the first place.
    printable = [ch for ch in map(chr, range(0x21, 0x7F))]
    out += [f"|{ch}x" for ch in printable]
    out += [f"|[{ch}x" for ch in printable]
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
