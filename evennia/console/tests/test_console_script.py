"""Scope checks over the console's browser script.

``console.js`` is four thousand lines with no linter, no bundler, and no test
runner: the repository has no ``package.json`` and CI runs no Node. Nothing
read this file until a browser did.

That is how the flag dossier shipped broken. ``duration`` and ``reach`` were
declared inside ``editor()``, used only inside ``flagDossier()``, and resolved
in neither, so clicking any flag raised ``ReferenceError`` and the one path
that creates a sanction never opened. The panel's own tests all passed: they
test the service, and the service was correct.

This closes that class of defect using the toolchain that already exists. It is
a scope check, not a linter -- it answers one question, which is whether a
top-level function uses a name that nothing in scope declares.

The analysis is heuristic, so it is deliberately generous about what counts as
declared and conservative about what counts as used: a missed defect is the
cost of never crying wolf at somebody who did nothing wrong. If a real browser
global is reported, add it to :data:`BROWSER_GLOBALS`.

"""

import re
from pathlib import Path

from django.test import SimpleTestCase

import evennia

SCRIPT = Path(evennia.__file__).parent / "web" / "static" / "console" / "console.js"

#: Names the browser provides. Not exhaustive: extended when one is reported.
BROWSER_GLOBALS = frozenset(
    """
    window document console Math JSON Object Array String Number Boolean Date
    RegExp Promise Set Map WeakMap WeakSet Symbol Proxy Reflect BigInt
    Error TypeError RangeError SyntaxError fetch setTimeout clearTimeout
    setInterval clearInterval requestAnimationFrame cancelAnimationFrame
    alert confirm prompt localStorage sessionStorage location history
    navigator URL URLSearchParams FormData Blob File FileReader
    AbortController EventSource WebSocket CustomEvent Event Intl
    encodeURIComponent decodeURIComponent encodeURI decodeURI
    parseInt parseFloat isNaN isFinite undefined NaN Infinity globalThis
    performance structuredClone TextEncoder TextDecoder atob btoa crypto
    queueMicrotask Response Request Headers Node Element HTMLElement
    MutationObserver ResizeObserver IntersectionObserver getComputedStyle
    matchMedia scrollTo Image Audio DOMParser AbortSignal
    """.split()
)

#: Reserved words. They tokenize as identifiers and are never free variables.
KEYWORDS = frozenset(
    """
    this arguments super new typeof instanceof void delete in of let const var
    function return if else for while do switch case default break continue
    try catch finally throw class extends static get set async await yield
    import export from as null true false debugger with
    """.split()
)


def strip_noise(text):
    """Remove comments, strings, template literals, and regex literals.

    Regex literals matter. Without stripping them, ``replace(/_/g, " ")``
    contributes ``g`` as a free identifier, and a check that reports three
    invented names on a clean file is a check nobody runs twice.

    Args:
        text: JavaScript source.

    Returns:
        str: The same source with every literal replaced by a placeholder, so
        offsets stay roughly aligned and nothing inside a literal tokenizes.
    """

    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    text = re.sub(r"`(?:[^`\\]|\\.)*`", ' "" ', text, flags=re.S)
    text = re.sub(r'"(?:[^"\\\n]|\\.)*"', ' "" ', text)
    text = re.sub(r"'(?:[^'\\\n]|\\.)*'", ' "" ', text)
    # A regex literal, recognised by what may precede one. Division cannot
    # follow any of these characters, so there is no ambiguity to resolve.
    text = re.sub(
        r"(?<=[=(,:!&|?+\-*%~^{;\[])\s*/(?:[^/\\\n\[]|\\.|\[(?:[^\]\\\n]|\\.)*\])+/[gimsuyd]*",
        " 0 ",
        text,
    )
    return text


def top_level_functions(lines):
    """Yield ``(name, first_line, last_line)`` for each unindented function.

    The file is formatted with every top-level function starting at column zero
    and closing on a line that is exactly ``}``, which makes the boundary exact
    without parsing.
    """

    for index, line in enumerate(lines):
        match = re.match(r"(?:async\s+)?function\s+(\w+)\s*\(", line)
        if not match:
            continue
        end = index + 1
        while end < len(lines) and lines[end] != "}":
            end += 1
        yield match.group(1), index, end


def declared_names(body):
    """Return every name this body binds. Over-approximated on purpose."""

    names = set(re.findall(r"\b(?:const|let|var|function|class)\s+(\w+)", body))
    for match in re.finditer(r"\b(?:const|let|var)\s*[\{\[]([^\}\]]*)[\}\]]", body):
        names.update(re.findall(r"\w+", match.group(1)))
    for match in re.finditer(r"\(([^()]*)\)\s*(?:=>|\{)", body):
        names.update(re.findall(r"\b[a-zA-Z_$]\w*", match.group(1)))
    names.update(re.findall(r"catch\s*\((\w+)\)", body))
    names.update(re.findall(r"(\w+)\s*=>", body))
    return names


def used_names(body):
    """Return every name this body reads. Property access and keys excluded."""

    names = set()
    for match in re.finditer(r"(?<![.\w$])([a-zA-Z_$]\w*)", body):
        if body[match.end() : match.end() + 1] == ":":
            continue
        names.add(match.group(1))
    return names


def free_identifiers(source):
    """Return ``{function_name: sorted_free_names}`` for one script.

    Args:
        source: The whole script.

    Returns:
        dict: Every top-level function that reads a name nothing declares, and
        the names it reads. Empty when the script is sound.
    """

    lines = source.split("\n")
    spans = list(top_level_functions(lines))
    module_level = {name for name, _, _ in spans}
    for index, line in enumerate(lines):
        if any(start <= index <= end for _, start, end in spans):
            continue
        clean = strip_noise(line)
        module_level.update(re.findall(r"\b(?:const|let|var|class)\s+(\w+)", clean))
        for match in re.finditer(r"\b(?:const|let|var)\s*\{([^}]*)\}", clean):
            module_level.update(re.findall(r"\w+", match.group(1)))

    known = module_level | BROWSER_GLOBALS | KEYWORDS
    findings = {}
    for name, start, end in spans:
        body = strip_noise("\n".join(lines[start : end + 1]))
        free = used_names(body) - declared_names(body) - known
        if free:
            findings[name] = sorted(free)
    return findings


class TestConsoleScriptScope(SimpleTestCase):
    """No function may use a name that nothing in scope declares."""

    def test_the_script_is_there_to_check(self):
        # A check that silently stops checking is worse than no check at all.
        self.assertTrue(SCRIPT.is_file(), f"{SCRIPT} is missing")

    def test_no_function_reads_a_name_from_another_function(self):
        findings = free_identifiers(SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(
            findings,
            {},
            "These names are used but never declared in scope. Each one raises "
            "ReferenceError the moment the function runs. If a name is a real "
            "browser global, add it to BROWSER_GLOBALS in this file.",
        )


class TestTheCheckItselfWorks(SimpleTestCase):
    """A checker that always reports success is worse than none at all.

    Every case below is written against the real defect this file exists for,
    so a refactor that quietly breaks the analysis fails here rather than
    passing everything forever.
    """

    def test_it_catches_a_name_declared_in_another_function(self):
        source = (
            "function editor() {\n"
            "  const duration = 1;\n"
            "  return 2;\n"
            "}\n"
            "\n"
            "function dossier() {\n"
            "  return duration;\n"
            "}\n"
        )
        self.assertEqual(free_identifiers(source), {"dossier": ["duration"]})

    def test_it_accepts_a_module_level_declaration(self):
        source = "const shared = 1;\n\nfunction reader() {\n  return shared;\n}\n"
        self.assertEqual(free_identifiers(source), {})

    def test_it_accepts_another_top_level_function(self):
        source = (
            "function helper() {\n  return 1;\n}\n\nfunction caller() {\n  return helper();\n}\n"
        )
        self.assertEqual(free_identifiers(source), {})

    def test_it_accepts_parameters_and_local_bindings(self):
        source = (
            "function reader(first, second = 2) {\n"
            "  const { third } = first;\n"
            "  for (const item of second) third(item);\n"
            "  return third;\n"
            "}\n"
        )
        self.assertEqual(free_identifiers(source), {})

    def test_a_regex_flag_is_not_reported_as_a_name(self):
        # The false positive that would have made this check unusable.
        source = 'function reader(text) {\n  return text.replace(/_/g, " ");\n}\n'
        self.assertEqual(free_identifiers(source), {})

    def test_a_name_inside_a_string_is_not_reported(self):
        source = 'function reader() {\n  return "duration is not read here";\n}\n'
        self.assertEqual(free_identifiers(source), {})

    def test_a_property_name_is_not_reported(self):
        source = "function reader(row) {\n  return row.duration;\n}\n"
        self.assertEqual(free_identifiers(source), {})

    def test_an_object_key_is_not_reported(self):
        source = "function reader() {\n  return { duration: 1 };\n}\n"
        self.assertEqual(free_identifiers(source), {})
