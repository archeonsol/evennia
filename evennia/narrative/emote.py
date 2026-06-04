"""
First-person pose/emote linguistics (emote tiers B2).

Generic conjugation, segmentation, and per-viewer text assembly. Games extend
targeting and naming via :mod:`evennia.narrative.protocols` and quoted-speech via
:func:`parse_quoted_speech`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

try:
    import regex as re
except ImportError:
    import re

try:
    import yaml
except ImportError:
    yaml = None

try:
    import pyinflect as _pyinflect

    _PYINFLECT_AVAILABLE = True
except ImportError:
    _pyinflect = None
    _PYINFLECT_AVAILABLE = False

try:
    import inflect as _inflect_mod

    _INFLECT_ENGINE = _inflect_mod.engine()
    _INFLECT_AVAILABLE = True
except ImportError:
    _INFLECT_ENGINE = None
    _INFLECT_AVAILABLE = False

from .protocols import KeyNameResolver, NameResolver

__all__ = [
    "SegmentPlan",
    "EmotePlan",
    "EmoteResult",
    "load_linguistics",
    "parse_quoted_speech",
    "PRONOUN_MAP",
    "first_to_second",
    "first_to_third",
    "split_emote_segments",
    "build_emote_segment_plans",
    "find_targets_in_text",
    "build_emote_for_viewer",
    "format_emote_message",
    "replace_first_pronoun_with_name",
    "build_caller_echo",
    "build_camera_text",
]

_DEFAULT_LINGUISTICS_PATH = Path(__file__).with_name("linguistics.yaml")
_BUILTIN_DEFAULTS = {
    "pronoun_map": {
        "male": ("he", "his", "him"),
        "female": ("she", "her", "her"),
        "neutral": ("they", "their", "them"),
        "nonbinary": ("they", "their", "them"),
    },
    "first_to_second_map": [],
    "irregular_verbs": {},
    "auxiliaries": [],
    "pronoun_patterns": [],
}


def load_linguistics(path=None):
    """Load linguistics tables from YAML. Returns a dict (cached per path)."""
    path = Path(path) if path else _DEFAULT_LINGUISTICS_PATH
    if yaml is None or not path.is_file():
        return dict(_BUILTIN_DEFAULTS)
    with path.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return doc


def _tuple_map(raw_map):
    if not raw_map:
        return {}
    out = {}
    for key, vals in raw_map.items():
        if isinstance(vals, (list, tuple)):
            out[key] = tuple(vals)
        else:
            out[key] = vals
    return out


_lang = load_linguistics()

PRONOUN_MAP = _tuple_map(_lang.get("pronoun_map")) or _BUILTIN_DEFAULTS["pronoun_map"]

_BE_PRESENT = {"he": "is", "she": "is", "they": "are"}
_BE_PAST = {"he": "was", "she": "was", "they": "were"}

FIRST_TO_SECOND_MAP = [
    (pattern, replacement)
    for pattern, replacement in (_lang.get("first_to_second_map") or [])
]

IRREGULAR_VERBS = _lang.get("irregular_verbs") or {}
_AUXILIARIES = frozenset(_lang.get("auxiliaries") or [])
_PRONOUN_PATTERNS = _lang.get("pronoun_patterns") or []


@dataclass(frozen=True, slots=True)
class SegmentPlan:
    """Emitter-invariant data for one emote segment."""

    third_text: str
    lang_bits: list
    targets: list


@dataclass
class EmotePlan:
    """All emitter-invariant data for one pose, ready for delivery."""

    segment_plans: list
    caller_echo: str
    camera_text: str
    starts_comma: bool
    pronoun_key: str
    lang_key: str
    msg_type: str
    improvise: bool
    caller: object
    location: object
    viewers: list


@dataclass
class EmoteResult:
    """What actually happened when a pose was delivered."""

    targets: list
    delivered_to: list
    camera_text: str
    caller_echo: str


def parse_quoted_speech(text: str) -> tuple[str, list]:
    """Extension point: extract quoted speech placeholders from *text*.

    Default is a no-op (no language/obfuscation). Games override to return
    ``(text_with_placeholders, [(placeholder, quote_text), ...])``.
    """
    return text, []


def _protect_quotes(text):
    quotes = re.findall(r'"([^"]*)"', text)
    quote_map = {f"__Q{i}__": f'"{q}"' for i, q in enumerate(quotes)}
    for placeholder, original in quote_map.items():
        text = text.replace(original, placeholder)
    return text, quote_map


def _restore_quotes(text, quote_map):
    for placeholder, original in quote_map.items():
        text = text.replace(placeholder, original)
    return text


def first_to_second(text: str) -> str:
    """Convert first-person text to second person for the emitter's echo."""
    if not text:
        return text
    text, quote_map = _protect_quotes(text)
    for pattern, replacement in FIRST_TO_SECOND_MAP:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return _restore_quotes(text, quote_map)


def _conjugate(word: str) -> str:
    if not word:
        return word
    lower = word.lower()
    if lower in _AUXILIARIES:
        return word
    if lower in IRREGULAR_VERBS:
        result = IRREGULAR_VERBS[lower]
        return result.capitalize() if word[0].isupper() else result
    if _PYINFLECT_AVAILABLE:
        try:
            inflected = _pyinflect.getInflection(lower, tag="VBZ")
            if inflected:
                result = inflected[0]
                return result.capitalize() if word[0].isupper() else result
        except Exception:
            pass
    if lower.endswith(("s", "sh", "ch", "x", "z")):
        return word + "es"
    if lower.endswith("y") and len(lower) > 1 and lower[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def _possessive(name: str) -> str:
    if not name:
        return name
    if _INFLECT_AVAILABLE:
        try:
            return _INFLECT_ENGINE.possessive(name)
        except Exception:
            pass
    return name + "'s"


def first_to_third(text: str, character) -> str:
    """Convert first-person emote text to third-person for other viewers."""
    text, quote_map = _protect_quotes(text)

    key = (getattr(getattr(character, "db", None), "pronoun", None) or "neutral").lower()
    sub, poss, obj = PRONOUN_MAP.get(key, PRONOUN_MAP["neutral"])
    reflexive = {"he": "himself", "she": "herself", "they": "themself"}.get(sub, "themself")

    be_present = _BE_PRESENT.get(sub, "is")
    be_past = _BE_PAST.get(sub, "was")
    have_present = "have" if sub == "they" else "has"

    im_contraction = f"{sub}'re" if sub == "they" else f"{sub}'s"
    text = re.sub(r"\bI'm\b", im_contraction, text, flags=re.IGNORECASE)
    ive_contraction = f"{sub}'ve" if sub == "they" else f"{sub}'s"
    text = re.sub(r"\bI've\b", ive_contraction, text, flags=re.IGNORECASE)
    text = re.sub(r"\bI'll\b", f"{sub}'ll", text, flags=re.IGNORECASE)
    text = re.sub(r"\bI'd\b", f"{sub}'d", text, flags=re.IGNORECASE)

    text = re.sub(r"\bI am\b", f"{sub} {be_present}", text, flags=re.IGNORECASE)
    text = re.sub(r"\bI was\b", f"{sub} {be_past}", text, flags=re.IGNORECASE)
    text = re.sub(r"\bI were\b", f"{sub} {be_past}", text, flags=re.IGNORECASE)
    text = re.sub(r"\bI will\b", f"{sub} will", text, flags=re.IGNORECASE)
    text = re.sub(r"\bI have\b", f"{sub} {have_present}", text, flags=re.IGNORECASE)
    text = re.sub(r"\bI had\b", f"{sub} had", text, flags=re.IGNORECASE)
    text = re.sub(r"\bI would\b", f"{sub} would", text, flags=re.IGNORECASE)
    text = re.sub(r"\bI could\b", f"{sub} could", text, flags=re.IGNORECASE)
    text = re.sub(r"\bI should\b", f"{sub} should", text, flags=re.IGNORECASE)
    text = re.sub(r"\bI can\b", f"{sub} can", text, flags=re.IGNORECASE)
    text = re.sub(r"\bI did\b", f"{sub} did", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\bI do\b",
        f"{sub} does" if sub != "they" else f"{sub} do",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(r"\bI\b", sub, text, flags=re.IGNORECASE)
    text = re.sub(r"\bmy\b", poss, text, flags=re.IGNORECASE)
    text = re.sub(r"\bme\b", obj, text, flags=re.IGNORECASE)
    text = re.sub(
        r"\bmine\b",
        f"{poss}s" if poss not in ("their",) else "theirs",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bmyself\b",
        lambda m: reflexive.capitalize() if m.group(0)[0].isupper() else reflexive,
        text,
        flags=re.IGNORECASE,
    )

    skip_first_conjugate = False
    if text.lstrip().startswith(","):
        text = text.lstrip()[1:].lstrip()
        skip_first_conjugate = True
    if not skip_first_conjugate and text.lstrip().startswith("."):
        text = text.lstrip().lstrip(".").lstrip()

    def conjugate_dot_verb(match):
        return " " + _conjugate(match.group(1))

    text = re.sub(r" \.\s*(\w+)", conjugate_dot_verb, text)

    words = text.split()
    pronouns = {
        sub,
        poss,
        obj,
        "they",
        "their",
        "them",
        reflexive,
        "himself",
        "herself",
        "themself",
        "theirs",
    }
    if not skip_first_conjugate and words:
        first = words[0]
        alpha_part = first.rstrip(".,;:!?") or first
        if (
            alpha_part
            and alpha_part.isalpha()
            and "__Q" not in alpha_part
            and alpha_part.lower() not in pronouns
        ):
            words[0] = _conjugate(alpha_part) + first[len(alpha_part) :]
    text = " ".join(words)

    return _restore_quotes(text, quote_map)


def split_emote_segments(text: str) -> list[str]:
    """Split on ``' . '`` (space dot space) for multi-segment poses."""
    return [s.strip() for s in re.split(r" \.\s+", text) if s.strip()]


def find_targets_in_text(
    text: str,
    character_list: Sequence,
    emitter,
    resolver: NameResolver | None = None,
) -> list:
    """Find character targets in *text* using *resolver* (default: key names)."""
    resolver = resolver or KeyNameResolver()
    return resolver.find_targets_in_text(text, character_list, emitter)


def build_emote_segment_plans(
    segments,
    caller,
    character_list,
    resolver: NameResolver | None = None,
) -> list[SegmentPlan]:
    """Emitter-invariant work per segment: third-person text and targets."""
    resolver = resolver or KeyNameResolver()
    plans = []
    for seg in segments:
        third = first_to_third(seg.strip(), caller)
        try:
            third, lang_bits = parse_quoted_speech(third)
        except Exception:
            from evennia.utils import logger

            logger.log_trace("narrative.emote.build_emote_segment_plans parse_quoted_speech")
            lang_bits = []
        targets = find_targets_in_text(third, character_list, caller, resolver=resolver)
        plans.append(SegmentPlan(third, lang_bits, targets))
    return plans


def _pronoun_set(char):
    return (getattr(getattr(char, "db", None), "pronoun", None) or "neutral").lower()


def _get_name_mention_timeline(text, targets):
    events = []
    for name, char in targets:
        for m in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE):
            events.append((m.end(), char))
    events.sort(key=lambda x: x[0])
    return events


def _get_pronoun_referents(text, targets):
    timeline = _get_name_mention_timeline(text, targets)
    if not timeline:
        return []
    char_pronoun_set = {char: _pronoun_set(char) for _, char in targets}
    covered = []
    pronoun_occurrences = []

    for pattern, pset, form_type, has_capture in _PRONOUN_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            s, e = m.start(), m.end()
            if any(s < end and e > start for start, end in covered):
                continue
            covered.append((s, e))
            referent = None
            for end_pos, char in timeline:
                if end_pos <= s and char_pronoun_set.get(char) == pset:
                    referent = char
            suffix = m.group(1) if has_capture and m.lastindex else None
            pronoun_occurrences.append((s, e, referent, form_type, m.group(0), suffix))
    pronoun_occurrences.sort(key=lambda x: x[0])
    return pronoun_occurrences


def _second_person_form(form_type):
    if form_type in ("sub", "obj"):
        return "you"
    if form_type == "poss_det":
        return "your"
    return "yours"


def build_emote_for_viewer(
    text: str,
    viewer,
    targets,
    resolver: NameResolver | None = None,
) -> str:
    """Per-viewer body: names and pronouns resolved for *viewer*."""
    resolver = resolver or KeyNameResolver()
    pronoun_occurrences = _get_pronoun_referents(text, targets)
    placeholders = []
    for i, (start, end, referent, form_type, original, suffix) in enumerate(
        reversed(pronoun_occurrences)
    ):
        idx = len(pronoun_occurrences) - 1 - i
        ph = f"__PRON_{idx}__"
        placeholders.append((ph, referent, form_type, original, suffix))
        tail = (" " + suffix if suffix else "")
        text = text[:start] + ph + tail + text[end:]

    for matched_name, char in sorted(targets, key=lambda t: -len(t[0])):
        if char == viewer:
            replacement = "you"
            text = re.sub(
                r"(?<!\w)" + re.escape(matched_name) + r"'s(?!\w)",
                "your",
                text,
                flags=re.IGNORECASE,
            )
            text = re.sub(
                r"(?<!\w)" + re.escape(matched_name) + r"(?!\w)",
                replacement,
                text,
                flags=re.IGNORECASE,
            )
        else:
            plain = resolver.display_name(char, viewer)
            repl_poss = _possessive(plain)
            text = re.sub(
                r"(?<!\w)" + re.escape(matched_name) + r"'s(?!\w)",
                repl_poss,
                text,
                flags=re.IGNORECASE,
            )
            text = re.sub(
                r"(?<!\w)" + re.escape(matched_name) + r"(?!\w)",
                plain,
                text,
                flags=re.IGNORECASE,
            )

    for ph, referent, form_type, original, suffix in placeholders:
        if referent is not None and referent == viewer:
            replacement = _second_person_form(form_type)
        else:
            replacement = original.split()[0] if suffix and form_type == "poss_det" else original
        text = text.replace(ph, replacement)
    return text


def format_emote_message(emitter, viewer, body, resolver: NameResolver | None = None) -> str:
    """Third-person emote line: emitter name + body."""
    resolver = resolver or KeyNameResolver()
    name = resolver.display_name(emitter, viewer)
    return f"|c{name}|n {body}"


def replace_first_pronoun_with_name(
    body,
    pronoun_key,
    emitter,
    viewer,
    resolver: NameResolver | None = None,
) -> str:
    """Comma-start poses: swap the first third-person pronoun for the emitter's name."""
    resolver = resolver or KeyNameResolver()
    key = (pronoun_key or "neutral").lower()
    sub, poss, obj = PRONOUN_MAP.get(key, PRONOUN_MAP["neutral"])
    name = resolver.display_name(emitter, viewer) if emitter else ""
    name_poss = _possessive(name)
    candidates = []
    for pattern, repl in [
        (r"\b" + re.escape(sub) + r"\b", name),
        (r"\b" + re.escape(obj) + r"\b", name),
        (r"\b" + re.escape(poss) + r"\b", name_poss),
    ]:
        m = re.search(pattern, body, re.IGNORECASE)
        if m:
            candidates.append((m.start(), m.end(), repl))
    if poss == "her" and obj == "her":
        m = re.search(r"\bher\s+(\w)", body, re.IGNORECASE)
        if m:
            end = m.start() + len(m.group(0)) - len(m.group(1))
            candidates.append((m.start(), end, name_poss + " "))
    if not candidates:
        return body
    candidates.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    start, end, repl = candidates[0]
    return body[:start] + repl + body[end:]


def build_caller_echo(
    segments,
    starts_comma,
    segment_plans,
    caller,
    resolver: NameResolver | None = None,
) -> str:
    """Second-person echo the emitter sees for their own pose."""
    resolver = resolver or KeyNameResolver()
    echo_parts = []
    caller_targets = []
    for i, seg in enumerate(segments):
        seg = seg.lstrip().lstrip(",").lstrip() if seg.strip().startswith(",") else seg
        seg = re.sub(r"^\.\s*(\w+)", r"\1", seg)
        seg = re.sub(r" \.\s*(\w+)", r" \1", seg)
        converted = first_to_second(seg)
        if i == 0:
            converted = converted[0].upper() + converted[1:] if converted else converted
        else:
            converted = converted[0].lower() + converted[1:] if converted else converted
        echo_parts.append(converted)
        if i < len(segment_plans):
            caller_targets.extend(segment_plans[i].targets)

    full_echo = ". ".join(echo_parts).strip()
    if not full_echo.endswith((".", "!", "?", '"')):
        full_echo += "."
    full_echo = re.sub(r"\.\s+(\w)", lambda m: ". " + m.group(1).upper(), full_echo)

    for matched_name, char in sorted(caller_targets, key=lambda x: -len(x[0])):
        if char == caller:
            full_echo = re.sub(
                r"(?<!\w)" + re.escape(matched_name) + r"'s(?!\w)",
                "your",
                full_echo,
                flags=re.IGNORECASE,
            )
            full_echo = re.sub(
                r"(?<!\w)" + re.escape(matched_name) + r"(?!\w)",
                "you",
                full_echo,
                flags=re.IGNORECASE,
            )
        else:
            pln = resolver.display_name(char, caller)
            repl_poss = _possessive(pln)
            full_echo = re.sub(
                r"(?<!\w)" + re.escape(matched_name) + r"'s(?!\w)",
                repl_poss,
                full_echo,
                flags=re.IGNORECASE,
            )
            full_echo = re.sub(
                r"(?<!\w)" + re.escape(matched_name) + r"(?!\w)",
                pln,
                full_echo,
                flags=re.IGNORECASE,
            )

    if starts_comma:
        body = full_echo
        return (body[0].upper() + body[1:]) if body and body[0].islower() else (body or "")
    if full_echo.lower().startswith("you "):
        body = full_echo[4:].strip()
    else:
        body = (
            (full_echo[0].lower() + full_echo[1:])
            if full_echo and full_echo[0].isupper()
            else full_echo
        )
    return f"|cYou|n {body}" if body else "|cYou|n"


def build_camera_text(
    segment_plans,
    starts_comma,
    pronoun_key,
    caller,
    resolver: NameResolver | None = None,
) -> str:
    """Neutral third-person line for logs (viewer=None)."""
    resolver = resolver or KeyNameResolver()
    if not segment_plans:
        return ""
    body_parts = []
    for sp in segment_plans:
        body_part = build_emote_for_viewer(sp.third_text, None, sp.targets, resolver=resolver)
        for ph, quote_text in sp.lang_bits:
            body_part = body_part.replace(ph, f'"{quote_text}"')
        body_parts.append(body_part)

    full_body = ". ".join(p.strip() for p in body_parts if p.strip()).strip()
    if full_body and not full_body.endswith((".", "!", "?", '"')):
        full_body += "."
    full_body = re.sub(r"\.\s+(\w)", lambda m: ". " + m.group(1).upper(), full_body)

    if starts_comma:
        full_body = replace_first_pronoun_with_name(
            full_body, pronoun_key, caller, None, resolver=resolver
        )
        return (
            (full_body[0].upper() + full_body[1:])
            if full_body and full_body[0].islower()
            else (full_body or "")
        )
    return format_emote_message(caller, None, full_body, resolver=resolver)
