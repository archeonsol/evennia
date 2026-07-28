"""Span-tree rendering: resolve narrative content per-viewer at *view* time.

This is the depth of R1 that makes recog/sdesc a real engine feature rather than
emit-time string baking. Narrative content (a pose, a room line, a recorded
scene) is a **viewer-invariant span tree**: literal text interleaved with
*references* -- to characters, pronouns, and speech -- that are **not yet
resolved to names**. A :func:`render_spans` call resolves those references for a
specific viewer, through a :class:`SpanResolver`, as the last step.

Because the tree holds references (ids + roles), not resolved strings, it can be:

- resolved differently per viewer (sdesc vs recognized name vs "someone"),
- **stored** and resolved *later* for whoever reads it (recordings, photos,
  channel logs, mail) so recognition applies retroactively, and
- passed through composable resolver logic (perception gating, language garbling,
  disguise) instead of tangled per-subsystem regex.

The resolver is the extension seam: the engine ships a trivial key-name resolver;
games supply one that knows sdesc, recognition, skin tones, perception, senses,
and disguise. Nothing here resolves names itself -- it only walks the tree and
delegates, so every surface that renders through it names people identically.

Parity note: a resolver that mirrors the game's current per-viewer string builder
makes ``render_spans`` reproduce today's output byte-for-byte; that equivalence
is what gates switching a live surface over (see the game-side parity harness).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Protocol, runtime_checkable

__all__ = [
    "ViewerContext",
    "TextSpan",
    "CharRef",
    "PronounRef",
    "SpeechSpan",
    "ObjectRef",
    "ExitRef",
    "ItemRef",
    "SelfRef",
    "SenderRef",
    "SpanResolver",
    "KeyResolver",
    "set_default_lookup",
    "render_spans",
    "span_to_dict",
    "span_from_dict",
    "Section",
    "SectionedView",
    "RenderPass",
    "PipelineResolver",
]


@dataclass
class ViewerContext:
    """Everything a resolver needs about *who* is viewing and under what conditions.

    ``viewer`` is the perceiving object (or ``None`` for a neutral/camera render).
    ``extras`` is an open bag so resolver passes can carry lighting, distance,
    known languages, perception results, etc. without churning this signature.
    """

    viewer: object = None
    extras: dict = field(default_factory=dict)

    def get(self, key, default=None):
        return self.extras.get(key, default)


# -- spans ------------------------------------------------------------------
# Viewer-invariant. A CharRef/PronounRef/SpeechSpan is a *reference*, resolved to
# a string only at render time by the resolver.


@dataclass(frozen=True, slots=True)
class TextSpan:
    """Literal text, rendered verbatim."""

    text: str


@dataclass(frozen=True, slots=True)
class CharRef:
    """A reference to a character mentioned in the content.

    Args:
        char_id: dbref id of the referenced character.
        role (str): why it is referenced (``"target"``, ``"emitter"``, ...);
            resolvers may treat roles differently.
        possessive (bool): whether the mention was possessive (``"Kade's"``), so
            the resolver picks the possessive name form.
    """

    char_id: int
    role: str = "target"
    possessive: bool = False
    # Whether the resolver applies skin-tone/name formatting. Emotes and say use
    # formatted names; whisper uses the raw display name (no colour).
    formatted: bool = True


@dataclass(frozen=True, slots=True)
class PronounRef:
    """A pronoun whose rendering depends on whether its referent is the viewer.

    Args:
        referent_id: dbref id of the character the pronoun refers to (or None).
        form (str): grammatical form key (subject/object/poss_det/...).
        original (str): the emitter-side pronoun text, used when the referent is
            not the viewer.
    """

    referent_id: int | None
    form: str
    original: str


@dataclass(frozen=True, slots=True)
class SpeechSpan:
    """Quoted speech, garbled per the viewer's languages by the resolver.

    Args:
        text (str): the spoken text (as the speaker said it).
        lang (str | None): language key the speech was in.
        quoted (bool): whether the resolver wraps the result in ``"..."``. Emotes
            embed speech as a quoted fragment (True); ``say``/``whisper`` supply
            their own quotes in the template, so garble only (False).
    """

    text: str
    lang: str | None = None
    quoted: bool = True


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """A reference to a non-character entity (exit, item, vehicle, door, ...).

    The generic inline reference: everything that is *not* a character, a
    pronoun, or speech. ``kind`` lets one resolver method dispatch by category
    without a class per category; :func:`ExitRef` and :func:`ItemRef` are the
    conventional constructors.

    Args:
        object_id: dbref id of the referenced entity.
        kind (str): category (``"object"``, ``"exit"``, ``"item"``, ...).
            Resolvers gate perception and formatting on it (a false exit, an
            unidentified weapon).
        role (str): why it is referenced (``"target"``, ``"instrument"``, ...).
        possessive (bool): whether the mention was possessive.
        formatted (bool): whether the resolver applies presentation formatting.
        fallback (str): what to render when the viewer cannot perceive the
            entity at all and the resolver has no better answer.
    """

    object_id: int | None
    kind: str = "object"
    role: str = "target"
    possessive: bool = False
    formatted: bool = True
    fallback: str = "something"


def ExitRef(object_id, *, role="exit", possessive=False, formatted=True, fallback="somewhere"):
    """An :class:`ObjectRef` for an exit (``kind="exit"``).

    Exits are the reference kind that makes *stable false exits* possible: the
    plan says "the exit id 42", and the viewer's resolver decides whether that
    renders as its real name, a hallucinated one, or nothing.
    """
    return ObjectRef(
        object_id=object_id,
        kind="exit",
        role=role,
        possessive=possessive,
        formatted=formatted,
        fallback=fallback,
    )


def ItemRef(object_id, *, role="target", possessive=False, formatted=True, fallback="something"):
    """An :class:`ObjectRef` for a carried/wielded item (``kind="item"``)."""
    return ObjectRef(
        object_id=object_id,
        kind="item",
        role=role,
        possessive=possessive,
        formatted=formatted,
        fallback=fallback,
    )


@dataclass(frozen=True, slots=True)
class SelfRef:
    """The viewer themself, in a given grammatical form.

    Distinct from a :class:`CharRef` that happens to point at the viewer: a
    ``SelfRef`` is authored when the text *means* "you" regardless of who reads
    it (a self-echo, a second-person system line), so no identity is consulted.

    Args:
        form (str): ``"subject"`` (you), ``"object"`` (you), ``"poss_det"``
            (your), ``"poss"`` (yours), ``"reflexive"`` (yourself).
        capitalize (bool): render capitalized (sentence-initial).
    """

    form: str = "subject"
    capitalize: bool = False


@dataclass(frozen=True, slots=True)
class SenderRef:
    """A network/comms sender identity, resolved at delivery.

    Network attribution is a *reference*, not a name: the alias a viewer sees
    for a sender depends on their handle book, signal quality, and psychosis
    state. Keeping it structured is what makes misattribution a resolver
    decision instead of a regex over a finished line.

    Args:
        sender_id: dbref id of the sending entity (server-side only).
        alias (str): the alias the sender transmitted under.
        channel (str): transport the message arrived on (``"matrix"``, ...).
    """

    sender_id: int | None
    alias: str = ""
    channel: str = ""


_SELF_FORMS = {
    "subject": "you",
    "object": "you",
    "poss_det": "your",
    "poss": "yours",
    "reflexive": "yourself",
}


@runtime_checkable
class SpanResolver(Protocol):
    """Turns references into per-viewer strings. The extension seam.

    A resolver may consult :class:`ViewerContext` for perception, recognition,
    language, disguise and return whatever the game's rules dictate (including a
    perception fallback like ``"someone"`` or an empty string).
    """

    def char(self, ref: CharRef, ctx: ViewerContext) -> str:
        """Render a character reference for the viewer."""
        ...

    def pron(self, ref: PronounRef, ctx: ViewerContext) -> str:
        """Render a pronoun reference for the viewer."""
        ...

    def speech(self, ref: SpeechSpan, ctx: ViewerContext) -> str:
        """Render quoted speech for the viewer."""
        ...

    def obj(self, ref: ObjectRef, ctx: ViewerContext) -> str:
        """Render a non-character entity reference for the viewer."""
        ...

    def selfref(self, ref: SelfRef, ctx: ViewerContext) -> str:
        """Render a second-person self reference for the viewer."""
        ...

    def sender(self, ref: SenderRef, ctx: ViewerContext) -> str:
        """Render a network sender identity for the viewer."""
        ...


def _self_text(ref: SelfRef) -> str:
    """Default second-person rendering for a :class:`SelfRef`."""
    value = _SELF_FORMS.get(ref.form, "you")
    return value.capitalize() if ref.capitalize else value


_DEFAULT_LOOKUP = None


def set_default_lookup(fn):
    """Install the process-wide ``fn(entity_id) -> object`` used when naming refs.

    A resolver that does not inject its own lookup (including the fallback used
    for reference kinds a game resolver predates) still names entities, rather
    than silently degrading every one of them to its perception fallback.
    """
    global _DEFAULT_LOOKUP
    _DEFAULT_LOOKUP = fn
    return fn


class KeyResolver:
    """Engine-default resolver: name characters by ``key``, no perception rules.

    Games replace this with an sdesc/recognition/perception-aware resolver.
    """

    def __init__(self, lookup=None):
        # lookup(char_id) -> object; falls back to the process-wide default.
        self._lookup = lookup

    def _obj(self, char_id):
        lookup = self._lookup or _DEFAULT_LOOKUP
        return lookup(char_id) if lookup else None

    def char(self, ref: CharRef, ctx: ViewerContext) -> str:
        obj = self._obj(ref.char_id)
        name = getattr(obj, "key", str(ref.char_id)) if obj is not None else str(ref.char_id)
        return name + ("'s" if ref.possessive else "")

    def pron(self, ref: PronounRef, ctx: ViewerContext) -> str:
        return ref.original

    def speech(self, ref: SpeechSpan, ctx: ViewerContext) -> str:
        return f'"{ref.text}"' if getattr(ref, "quoted", True) else ref.text

    def obj(self, ref: ObjectRef, ctx: ViewerContext) -> str:
        """Name an entity as this viewer sees it.

        Defers to ``get_display_name(looker=viewer)`` -- the identity base namer
        (R1 keeps it as such: identity is not display). A game layers
        perception, disguise and psychosis on top via passes, not by changing
        what this returns.
        """
        entity = self._obj(ref.object_id)
        if entity is None:
            return ref.fallback + ("'s" if ref.possessive else "")
        getter = getattr(entity, "get_display_name", None)
        if callable(getter):
            try:
                name = getter(looker=ctx.viewer)
            except Exception:
                name = getattr(entity, "key", str(ref.object_id))
        else:
            name = getattr(entity, "key", str(ref.object_id))
        return str(name) + ("'s" if ref.possessive else "")

    def selfref(self, ref: SelfRef, ctx: ViewerContext) -> str:
        return _self_text(ref)

    def sender(self, ref: SenderRef, ctx: ViewerContext) -> str:
        return ref.alias


@dataclass
class Section:
    """One named region of a block-structured view (a room-look section, a doc block).

    ``text`` is the flattened, parity-anchor string a legacy/text client receives;
    ``spans`` is the optional viewer-invariant tree behind it, present once the
    section is decomposed for per-viewer resolution or a structured client.

    Args:
        kind (str): section identity (``"desc"``, ``"exits"``, ...).
        text (str): flattened section text.
        spans (list | None): optional span tree behind ``text``.
    """

    kind: str
    text: str = ""
    spans: list | None = None

    def __bool__(self):
        return bool(self.text) or bool(self.spans)


class SectionedView:
    """An ordered set of named :class:`Section`s with a group-driven flatten.

    The engine primitive behind block-structured output: a view declares its
    section ``order``; :meth:`flatten` joins section texts into the delivered
    string per a ``groups`` spec, so a game surface reproduces its legacy
    composition byte-for-byte while exposing the sections to structured consumers
    (rich clients, stored scenes, density/perception passes).

    Games subclass this (e.g. room look) to fix ``order`` and the flatten groups;
    nothing here is game-specific.
    """

    #: default section order; subclasses/callers override.
    order: tuple = ()

    def __init__(self, order=None, **sections):
        if order is not None:
            self.order = tuple(order)
        self.sections = {k: Section(k) for k in self.order}
        for kind, value in sections.items():
            self.set(kind, value)

    def set(self, kind, text="", spans=None):
        """Set a section by text (+ optional spans), or from a :class:`Section`."""
        if isinstance(text, Section):
            self.sections[kind] = text
        else:
            self.sections[kind] = Section(kind, text or "", spans)
        if kind not in self.order:
            self.order = self.order + (kind,)
        return self.sections[kind]

    def section(self, kind) -> Section:
        """Return the :class:`Section` for ``kind`` (empty one if absent)."""
        return self.sections.get(kind, Section(kind))

    def text_of(self, kind) -> str:
        return self.section(kind).text

    def blocks(self):
        """Non-empty ``(kind, text)`` sections in declared order."""
        return [(k, self.sections[k].text) for k in self.order if self.sections.get(k)]

    def flatten(self, groups=None, between="\n\n", within="\n") -> str:
        """Join section texts into one string.

        Within a group, non-empty section texts join with ``within``; groups join
        with ``between``; wholly empty groups vanish. ``groups`` defaults to one
        section per group (each its own block, ``between``-separated).
        """
        if groups is None:
            groups = [[k] for k in self.order]
        out = []
        for group in groups:
            texts = [
                self.sections[k].text for k in group if k in self.sections and self.sections[k].text
            ]
            joined = within.join(texts)
            if joined:
                out.append(joined)
        return between.join(out)


def render_spans(spans, ctx: ViewerContext, resolver: SpanResolver) -> str:
    """Flatten a span tree to a string for one viewer via ``resolver``.

    Walks the spans in order, delegating each reference to the resolver and
    passing literal text through. The resolver owns all naming/perception/
    language policy, so every surface that renders through here is consistent.
    """
    out = []
    text_fn = getattr(resolver, "text", None)
    for span in spans:
        if isinstance(span, TextSpan):
            # Optional resolver hook: transform literal narration (leaves
            # CharRef/SpeechSpan structure intact) - e.g. psychosis semantic rot.
            out.append(text_fn(span, ctx) if callable(text_fn) else span.text)
        elif isinstance(span, CharRef):
            out.append(resolver.char(span, ctx))
        elif isinstance(span, PronounRef):
            out.append(resolver.pron(span, ctx))
        elif isinstance(span, SpeechSpan):
            out.append(resolver.speech(span, ctx))
        elif isinstance(span, ObjectRef):
            out.append(_delegate(resolver, "obj", span, ctx, KeyResolver.obj))
        elif isinstance(span, SelfRef):
            out.append(_delegate(resolver, "selfref", span, ctx, KeyResolver.selfref))
        elif isinstance(span, SenderRef):
            out.append(_delegate(resolver, "sender", span, ctx, KeyResolver.sender))
        else:  # pragma: no cover - forward-compat for new span kinds
            out.append(str(getattr(span, "text", "")))
    return "".join(out)


def _delegate(resolver, method, span, ctx, default):
    """Call ``resolver.method``, falling back to the engine default.

    The newer reference kinds land in games that already ship a resolver. A
    resolver written before they existed still renders them (by ``key``) rather
    than raising, so adding a reference kind to a plan is never a hard break.
    """
    fn = getattr(resolver, method, None)
    if callable(fn):
        return fn(span, ctx)
    return default(KeyResolver(), span, ctx)


# -- pass pipeline (R1 core: register passes, don't wire them per surface) ----


@runtime_checkable
class RenderPass(Protocol):
    """A composable, deliver-time transform folded over resolved output.

    Each method is optional -- a pass implements only the span kinds it touches
    -- and receives the *value so far* plus the reference and context, returning
    the transformed value. Passes run in registration order. This is how games
    layer perception, psychosis, senses, and language onto the base rendering
    without each producer (or the resolver) hard-wiring them: register once,
    apply everywhere that renders through the pipeline.
    """

    def char(self, value: str, ref: CharRef, ctx: ViewerContext) -> str: ...

    def pron(self, value: str, ref: PronounRef, ctx: ViewerContext) -> str: ...

    def speech(self, value: str, ref: SpeechSpan, ctx: ViewerContext) -> str: ...

    def text(self, value: str, span: TextSpan, ctx: ViewerContext) -> str: ...


class PipelineResolver:
    """A :class:`SpanResolver` assembled from base *namers* + ordered *passes*.

    - A **namer** (game policy: sdesc/recognition/skin-tone; self->"you";
      perception gate) produces the base rendering for a reference. It may return
      a plain string, or a ``(value, final)`` pair; ``final=True`` short-circuits
      the passes (e.g. a concealed ``"someone"`` or a possessive form that must
      not be further transformed) -- preserving the exact ordering a hand-written
      resolver had.
    - A **pass** (:class:`RenderPass`: psychosis, senses restyle, language) folds
      over the base value in registration order.

    Games build one of these, set the namers, and register passes -- instead of
    editing a monolithic resolver or wiring effects into every producer. New
    deliver-time effects (e.g. phase-3 senses gating) become one ``add_pass``.
    """

    def __init__(self):
        self._char_namer = None
        self._pron_namer = None
        self._speech_namer = None
        self._obj_namer = None
        self._self_namer = None
        self._sender_namer = None
        self._passes = []

    def set_char_namer(self, fn):
        self._char_namer = fn
        return self

    def set_obj_namer(self, fn):
        """Set the namer for :class:`ObjectRef` (exits, items, vehicles, doors)."""
        self._obj_namer = fn
        return self

    def set_self_namer(self, fn):
        """Set the namer for :class:`SelfRef` (second-person forms)."""
        self._self_namer = fn
        return self

    def set_sender_namer(self, fn):
        """Set the namer for :class:`SenderRef` (network attribution)."""
        self._sender_namer = fn
        return self

    def set_pron_namer(self, fn):
        self._pron_namer = fn
        return self

    def set_speech_namer(self, fn):
        self._speech_namer = fn
        return self

    def add_pass(self, render_pass):
        self._passes.append(render_pass)
        return self

    @staticmethod
    def _split(result):
        if isinstance(result, tuple):
            return result
        return result, False

    def _fold(self, kind, value, ref, ctx):
        for render_pass in self._passes:
            fn = getattr(render_pass, kind, None)
            if callable(fn):
                value = fn(value, ref, ctx)
        return value

    def char(self, ref: CharRef, ctx: ViewerContext) -> str:
        if self._char_namer is None:
            value, final = str(ref.char_id), False
        else:
            value, final = self._split(self._char_namer(ref, ctx))
        if final:
            return value
        return self._fold("char", value, ref, ctx)

    def pron(self, ref: PronounRef, ctx: ViewerContext) -> str:
        if self._pron_namer is None:
            value, final = ref.original, False
        else:
            value, final = self._split(self._pron_namer(ref, ctx))
        if final:
            return value
        return self._fold("pron", value, ref, ctx)

    def speech(self, ref: SpeechSpan, ctx: ViewerContext) -> str:
        if self._speech_namer is None:
            base = f'"{ref.text}"' if getattr(ref, "quoted", True) else ref.text
            value, final = base, False
        else:
            value, final = self._split(self._speech_namer(ref, ctx))
        if final:
            return value
        return self._fold("speech", value, ref, ctx)

    def obj(self, ref: ObjectRef, ctx: ViewerContext) -> str:
        if self._obj_namer is None:
            value, final = KeyResolver().obj(ref, ctx), False
        else:
            value, final = self._split(self._obj_namer(ref, ctx))
        if final:
            return value
        return self._fold("obj", value, ref, ctx)

    def selfref(self, ref: SelfRef, ctx: ViewerContext) -> str:
        if self._self_namer is None:
            value, final = _self_text(ref), False
        else:
            value, final = self._split(self._self_namer(ref, ctx))
        if final:
            return value
        return self._fold("selfref", value, ref, ctx)

    def sender(self, ref: SenderRef, ctx: ViewerContext) -> str:
        if self._sender_namer is None:
            value, final = ref.alias, False
        else:
            value, final = self._split(self._sender_namer(ref, ctx))
        if final:
            return value
        return self._fold("sender", value, ref, ctx)

    def text(self, span: TextSpan, ctx: ViewerContext) -> str:
        return self._fold("text", span.text, span, ctx)


# -- serialization (slice 2: stored, replayable content) --------------------

_SPAN_KINDS = {
    "text": TextSpan,
    "char": CharRef,
    "pron": PronounRef,
    "speech": SpeechSpan,
    "obj": ObjectRef,
    "self": SelfRef,
    "sender": SenderRef,
}
_KIND_BY_TYPE = {cls: kind for kind, cls in _SPAN_KINDS.items()}


def span_to_dict(span) -> dict:
    """Serialize a span to a JSON-friendly dict (for stored/replayable trees)."""
    kind = _KIND_BY_TYPE.get(type(span))
    if kind is None:  # pragma: no cover
        raise TypeError(f"Unserializable span: {span!r}")
    data = {item.name: getattr(span, item.name) for item in fields(span)}
    data["_"] = kind
    return data


def span_from_dict(data: dict):
    """Rebuild a span from :func:`span_to_dict` output."""
    payload = dict(data)
    kind = payload.pop("_", None)
    cls = _SPAN_KINDS.get(kind)
    if cls is None:  # pragma: no cover
        raise TypeError(f"Unknown span kind: {kind!r}")
    return cls(**payload)
