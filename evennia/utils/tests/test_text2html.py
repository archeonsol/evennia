"""Tests for text2html"""

import json
import unittest
from html.parser import HTMLParser

import mock
from django.test import SimpleTestCase

from evennia.utils import ansi, text2html


class _LinkParser(HTMLParser):
    """Read the handler after the browser's HTML entity decoding step."""

    def handle_starttag(self, tag, attrs):
        """Capture the generated command attribute."""
        if tag == "a":
            self.handler = dict(attrs)["onclick"]


class TestText2Html(SimpleTestCase):
    def test_command_link_roundtrip(self):
        """Command punctuation remains data in one serialized string argument."""
        for command in (
            'say "hello"',
            'say \\"hello"',
            "look C:\\rooms\\",
            "say <&> &quot;",
            "say café 😀",
        ):
            with self.subTest(command=command):
                parser = _LinkParser()
                parser.feed(text2html.parse_html(f"|lc{command}|ltclick|le"))
                prefix = 'Evennia.msg("text",['
                suffix = "],{});return false;"
                self.assertTrue(parser.handler.startswith(prefix))
                self.assertTrue(parser.handler.endswith(suffix))
                self.assertEqual(json.loads(parser.handler[len(prefix) : -len(suffix)]), command)

    def test_format_styles(self):
        parser = text2html.HTML_PARSER
        self.assertEqual("foo", parser.format_styles("foo"))
        self.assertEqual(
            '<span class="color-001">red</span>foo',
            parser.format_styles(
                ansi.ANSI_UNHILITE + ansi.ANSI_RED + "red" + ansi.ANSI_NORMAL + "foo"
            ),
        )
        self.assertEqual(
            '<span class="bgcolor-001">red</span>foo',
            parser.format_styles(ansi.ANSI_BACK_RED + "red" + ansi.ANSI_NORMAL + "foo"),
        )
        self.assertEqual(
            '<span class="bgcolor-001 color-002">red</span>foo',
            parser.format_styles(
                ansi.ANSI_BACK_RED
                + ansi.ANSI_UNHILITE
                + ansi.ANSI_GREEN
                + "red"
                + ansi.ANSI_NORMAL
                + "foo"
            ),
        )
        self.assertEqual(
            'a <span class="underline">red</span>foo',
            parser.format_styles("a " + ansi.ANSI_UNDERLINE + "red" + ansi.ANSI_NORMAL + "foo"),
        )
        self.assertEqual(
            'a <span class="blink">red</span>foo',
            parser.format_styles("a " + ansi.ANSI_BLINK + "red" + ansi.ANSI_NORMAL + "foo"),
        )
        self.assertEqual(
            'a <span class="bgcolor-007 color-000">red</span>foo',
            parser.format_styles("a " + ansi.ANSI_INVERSE + "red" + ansi.ANSI_NORMAL + "foo"),
        )

        # True Color
        self.assertEqual(
            '<span class="" style="color: #ff0000;">red</span>foo',
            parser.format_styles(f"\x1b[38;2;255;0;0m" + "red" + ansi.ANSI_NORMAL + "foo"),
        )

    def test_remove_bells(self):
        parser = text2html.HTML_PARSER
        self.assertEqual("foo", parser.remove_bells("foo"))
        self.assertEqual(
            "a red" + ansi.ANSI_NORMAL + "foo",
            parser.remove_bells("a " + ansi.ANSI_BEEP + "red" + ansi.ANSI_NORMAL + "foo"),
        )

    def test_remove_backspaces(self):
        parser = text2html.HTML_PARSER
        self.assertEqual("foo", parser.remove_backspaces("foo"))
        self.assertEqual("redfoo", parser.remove_backspaces("a\010redfoo"))

    def test_convert_linebreaks(self):
        parser = text2html.HTML_PARSER
        self.assertEqual("foo", parser.convert_linebreaks("foo"))
        self.assertEqual("a<br> redfoo<br>", parser.convert_linebreaks("a\n redfoo\n"))

    def test_convert_urls(self):
        parser = text2html.HTML_PARSER
        self.assertEqual("foo", parser.convert_urls("foo"))
        self.assertEqual(
            'a <a href="http://redfoo" target="_blank">http://redfoo</a> runs',
            parser.convert_urls("a http://redfoo runs"),
        )

    def test_sub_mxp_links(self):
        parser = text2html.HTML_PARSER
        mocked_match = mock.Mock()
        mocked_match.groups.return_value = ["cmd", "text"]
        self.assertEqual(
            r"""<a id="mxplink" href="#" """
            """onclick="Evennia.msg(&quot;text&quot;,[&quot;cmd&quot;],{});"""
            """return false;">text</a>""",
            parser.sub_mxp_links(mocked_match),
        )

    def test_sub_text(self):
        parser = text2html.HTML_PARSER

        mocked_match = mock.Mock()

        mocked_match.groupdict.return_value = {"htmlchars": "foo"}
        self.assertEqual("foo", parser.sub_text(mocked_match))

        mocked_match.groupdict.return_value = {"htmlchars": "", "lineend": "foo"}
        self.assertEqual("<br>", parser.sub_text(mocked_match))

        parser.tabstop = 2
        mocked_match.groupdict.return_value = {
            "htmlchars": "",
            "lineend": "",
            "tab": "\t",
            "space": "",
        }
        self.assertEqual("  ", parser.sub_text(mocked_match))

        mocked_match.groupdict.return_value = {
            "htmlchars": "",
            "lineend": "",
            "tab": "\t\t",
            "space": " ",
            "spacestart": " ",
        }
        self.assertEqual("    ", parser.sub_text(mocked_match))

        mocked_match.groupdict.return_value = {
            "htmlchars": "",
            "lineend": "",
            "tab": "",
            "space": "",
            "spacestart": "",
        }
        self.assertEqual(None, parser.sub_text(mocked_match))

    def test_parse_tab_to_html(self):
        """Test entire parse mechanism"""
        parser = text2html.HTML_PARSER
        parser.tabstop = 4
        # single tab
        self.assertEqual(parser.parse("foo|>foo"), "foo    foo")

        # space and tab
        self.assertEqual(parser.parse("foo |>foo"), "foo     foo")

        # space, tab, space
        self.assertEqual(parser.parse("foo |> foo"), "foo      foo")

    def test_parse_html(self):
        self.assertEqual("foo", text2html.parse_html("foo"))
        self.maxDiff = None
        self.assertEqual(
            text2html.parse_html("|^|[CHello|n|u|rW|go|yr|bl|md|c!|[G!"),
            '<span class="blink bgcolor-006">'
            "Hello"
            '</span><span class="underline color-009">'
            "W"
            '</span><span class="underline color-010">'
            "o"
            '</span><span class="underline color-011">'
            "r"
            '</span><span class="underline color-012">'
            "l"
            '</span><span class="underline color-013">'
            "d"
            '</span><span class="underline color-014">'
            "!"
            '</span><span class="underline bgcolor-002 color-014">'
            "!"
            "</span>",
        )

    def test_parse_html_escapes_user_html(self):
        # parse_html is the trust boundary for the webclient: the default_out
        # plugin renders this output via .html()/string concat, so any raw <,
        # >, & from upstream text must come out as entities, never as live
        # tags. (" and ' are only sensitive inside attribute values, and
        # parse_html controls its own attribute construction.)
        for src, needle in (
            ("<script>alert(1)</script>", "<script>"),
            ("<img src=x onerror=alert(1)>", "<img"),
            ("a & b", "a & b"),
        ):
            out = text2html.parse_html(src)
            self.assertNotIn(needle, out, f"parse_html leaked unescaped HTML for: {src!r}")
