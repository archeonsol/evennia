"""Doc generator for the hook registry.

Emits markdown tables sourced from ``_REGISTRY`` for embedding in the
published Typeclass-Hooks docs. Run as a script to update those docs
in place::

    python -m evennia.hooks.docs --check     # diff only, exit 1 if stale
    python -m evennia.hooks.docs --write     # rewrite docs in place

The generator only writes between marker pairs::

    <!-- hooks-gen:start <section> -->
    ...generated content...
    <!-- hooks-gen:end -->

Two sections are currently generated: ``return-contracts`` (§3.7 of
the reference doc) and ``misshapen`` (after §6 of the main doc).
Other sections stay hand-written.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .lint import lint
from .registry import _REGISTRY
from .specs import HookSpec

_MARKER_RE = re.compile(
    r"(<!--\s*hooks-gen:start\s+(?P<name>\S+)\s*-->)"
    r"(?P<body>.*?)"
    r"(<!--\s*hooks-gen:end\s*-->)",
    re.DOTALL,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCS = (
    _REPO_ROOT / "docs/source/Components/Typeclass-Hooks.md",
    _REPO_ROOT / "docs/source/Components/Typeclass-Hooks-Reference.md",
)


def _qualname_split(qualname: str) -> tuple[str, str]:
    """Split ``Cls.method`` (or longer) into ``(class, method)``."""
    cls, _, method = qualname.rpartition(".")
    return cls, method


def _by_returns(value: str) -> list[tuple[str, HookSpec]]:
    """Return ``(qualname, spec)`` pairs filtered to specs declaring ``returns``."""
    out = []
    for qualname, spec in _REGISTRY.items():
        if spec.returns == value:
            out.append((qualname, spec))
    out.sort()
    return out


def render_return_contracts() -> str:
    """Markdown for §3: one table per returns category, sourced from the registry."""
    sections = []
    for label, value, header_effect in [
        ("Veto", "veto", "Veto effect"),
        ("Transform", "transform", "Transform effect"),
        ("Content", "content", "Returns"),
        ("Ignored", "ignored", "Notes"),
        ("Conditional", "conditional", "Returns"),
    ]:
        rows = _by_returns(value)
        if not rows:
            continue
        sections.append(f'### {label} (`returns="{value}"`)')
        sections.append("")
        sections.append(f"| Hook | Class | Actor | {header_effect} |")
        sections.append("|---|---|---|---|")
        for qualname, spec in rows:
            cls, method = _qualname_split(qualname)
            note = (spec.notes or "").replace("|", "\\|")
            actor = spec.actor or ""
            sections.append(f"| `{method}` | `{cls}` | {actor} | {note} |")
        sections.append("")
    return "\n".join(sections).rstrip() + "\n"


def render_misshapen() -> str:
    """Markdown for §6: misshapen hooks, sourced from lint and notes."""
    findings = [f for f in lint() if f.code == "PHASE_MISMATCH"]
    # Specs whose notes call themselves out as misshapen.
    from_notes = [
        (qn, spec)
        for qn, spec in _REGISTRY.items()
        if spec.notes and "misshapen" in spec.notes.lower()
    ]
    from_notes.sort()
    sections = ["### Phase / name mismatches (lint)"]
    sections.append("")
    if findings:
        sections.append("| Hook | Issue |")
        sections.append("|---|---|")
        for f in findings:
            msg = f.message.replace("|", "\\|")
            sections.append(f"| `{f.location}` | {msg} |")
    else:
        sections.append(
            "_None: every `at_pre_*` / `at_post_*` / `at_failed_*` "
            "name matches its declared phase._"
        )
    sections.append("")
    sections.append("### Self-flagged in spec notes")
    sections.append("")
    if from_notes:
        sections.append("| Hook | Note |")
        sections.append("|---|---|")
        for qualname, spec in from_notes:
            note = (spec.notes or "").replace("|", "\\|")
            sections.append(f"| `{qualname}` | {note} |")
    else:
        sections.append("_None._")
    sections.append("")
    return "\n".join(sections).rstrip() + "\n"


_RENDERERS = {
    "return-contracts": render_return_contracts,
    "misshapen": render_misshapen,
}


def render_section(name: str) -> str:
    """Render a single named section by key."""
    if name not in _RENDERERS:
        raise KeyError(f"unknown section {name!r}; known: {sorted(_RENDERERS)}")
    return _RENDERERS[name]()


def _rewrite_doc(text: str) -> str:
    """Return ``text`` with every hooks-gen block replaced by current output."""

    def _sub(match):
        name = match.group("name")
        if name not in _RENDERERS:
            # Unknown section name: leave untouched.
            return match.group(0)
        new_body = render_section(name)
        return f"{match.group(1)}\n{new_body}{match.group(4)}"

    return _MARKER_RE.sub(_sub, text)


def regenerate_doc_files(write: bool = False) -> list[Path]:
    """Regenerate marker blocks in the two published docs.

    Args:
        write: When True, rewrite files in place. When False, only
            report which files would change.

    Returns:
        Paths whose generated content differs from disk.
    """
    stale = []
    for path in _DOCS:
        if not path.exists():
            continue
        current = path.read_text()
        updated = _rewrite_doc(current)
        if updated != current:
            stale.append(path)
            if write:
                path.write_text(updated)
    return stale


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Exit 1 if docs are stale.")
    group.add_argument("--write", action="store_true", help="Rewrite docs in place.")
    args = parser.parse_args(argv)

    # Engine needs to be importable; Django settings must be configured.
    import django

    django.setup()
    import evennia

    evennia._init()

    stale = regenerate_doc_files(write=args.write)
    if args.check:
        if stale:
            for path in stale:
                print(f"STALE: {path}", file=sys.stderr)
            return 1
        print("Hook docs in sync.")
        return 0
    for path in stale:
        print(f"WROTE: {path}")
    if not stale:
        print("Hook docs already in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
