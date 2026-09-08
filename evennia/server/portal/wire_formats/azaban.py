"""
Azaban wire format (azaban.v1).

Our own WebSocket protocol for the Svelte shell — not "v2 of Evennia's", our
protocol v1. Every frame is a TEXT frame carrying one typed JSON envelope:

    {"t": "<type>", "seq"?: <int>, "re"?: <int>, ...payload}

`t` is the discriminant. Unlike the legacy v1.evennia.com format (positional
`[cmd, args, kwargs]`, HTML-only text), Azaban carries **structured R1
RenderNodes** for narrative content so the shell renders nodes rather than
un-baking HTML. Plain system text still ships as HTML (there is no node behind
it); the `narrative` outputfunc carries the node tree.

Server -> client types: hello, text, prompt, render, patch, batch, oob, res.
Client -> server types: hello, cmd, req, oob, websocket_close.

Outgoing frames are stamped with a monotonic ``s`` by the transport and buffered
for replay across a reconnect (see ``supports_resume`` and
``WebSocketClient._stamp_and_buffer``).

Client capabilities arrive in the ``hello`` envelope and are stashed on the
session by the ``azaban_hello`` inputfunc:

    rendersNodes    the shell renders RenderNode trees (sets CLIENT_NARRATIVE)
    rendersMarkup   the shell parses Evennia markup itself, so the server omits
                    the parsed ``html`` alongside each node body
    batching        the shell understands ``{"t": "batch", "frames": [...]}``,
                    letting the transport coalesce a burst into one frame

See ``evennia/.agents/docs/engine-architecture/webclient-protocol.md``.
"""

import json

from django.conf import settings

from evennia.utils import logger
from evennia.utils.ansi import parse_ansi
from evennia.utils.text2html import parse_html

from .base import _RE_SCREENREADER_REGEX, WireFormat

# --- incoming safety limits (structural, always enforced) ------------------
#: Reject a client frame larger than this many bytes outright.
_MAX_FRAME_BYTES = 64 * 1024
#: Maximum nesting depth of a decoded payload.
_MAX_DEPTH = 8
#: Maximum length of any decoded string.
_MAX_STRING = 8 * 1024
#: Maximum number of items in any decoded list/dict.
_MAX_ITEMS = 512

#: Action names that must NEVER be reachable from a client frame regardless of
#: allowlist configuration — introspection/administration inputfuncs that would
#: leak or mutate server state. Deny-by-name closes the worst of the "any
#: global inputfunc is reachable" hole even when no allowlist is set.
_ALWAYS_DENIED = frozenset(
    {
        "get_inputfuncs",
        "get_client_options",
        "login",
        "default",
        "admin",
    }
)

# Warn-once flag so a permissive (unconfigured) deployment is visible in the log
# without flooding it on every frame.
_warned_no_allowlist = False


def _wants_server_html(protocol_flags):
    """
    Whether the server should attach parsed HTML alongside node text.

    A shell that renders Evennia markup itself declares ``caps.rendersMarkup``
    in its ``hello`` and gets the node body only, which halves the payload on the
    narrative hot path and skips a ``parse_html`` per message per session.
    Clients that do not declare it keep the previous behaviour.

    Args:
        protocol_flags (dict or None): session protocol flags, carrying
            ``AZABAN_CAPS`` as stashed by the ``azaban_hello`` inputfunc.

    Returns:
        bool: True if the ``html`` field should be attached.
    """
    caps = (protocol_flags or {}).get("AZABAN_CAPS") or {}
    if not isinstance(caps, dict):
        return True
    return not caps.get("rendersMarkup")


def _within_limits(obj, depth=0, *, max_string=_MAX_STRING):
    """True if a decoded payload respects the structural safety limits."""
    if depth > _MAX_DEPTH:
        return False
    if isinstance(obj, str):
        return len(obj) <= max_string
    if isinstance(obj, dict):
        if len(obj) > _MAX_ITEMS:
            return False
        return all(
            isinstance(k, str)
            and len(k) <= max_string
            and _within_limits(v, depth + 1, max_string=max_string)
            for k, v in obj.items()
        )
    if isinstance(obj, (list, tuple)):
        if len(obj) > _MAX_ITEMS:
            return False
        return all(_within_limits(v, depth + 1, max_string=max_string) for v in obj)
    return True


def _contains_raw_identity(obj):
    """Return whether a public payload exposes an internal database identity."""
    forbidden = {"char_id", "from_id", "referent_id", "object_id"}
    if isinstance(obj, dict):
        return any(key in forbidden or _contains_raw_identity(value) for key, value in obj.items())
    if isinstance(obj, (list, tuple)):
        return any(_contains_raw_identity(value) for value in obj)
    return False


def _action_allowed(action, ns):
    """
    Whether a client-named ``action`` may be routed into the inputfunc
    namespace.

    Deny-by-name for the always-forbidden set. Then, if
    ``settings.AZABAN_PUBLIC_ACTIONS`` is configured (a non-empty iterable of
    ``"action"`` or ``"ns:action"`` names), enforce it deny-by-default. If it is
    unset, run permissively but log once that no allowlist is in force — the
    exposure stays visible and the operator can lock it down without a code
    change.
    """
    global _warned_no_allowlist
    if not action or not isinstance(action, str) or action in _ALWAYS_DENIED:
        return False
    allow = getattr(settings, "AZABAN_PUBLIC_ACTIONS", None)
    if allow:
        allow = set(allow)
        return action in allow or (ns and f"{ns}:{action}" in allow)
    if not _warned_no_allowlist:
        _warned_no_allowlist = True
        logger.log_warn(
            "azaban: AZABAN_PUBLIC_ACTIONS is not configured; client 'req'/'oob' "
            "actions are routed permissively (only the always-denied set is "
            "blocked). Set AZABAN_PUBLIC_ACTIONS to a deny-by-default allowlist."
        )
    return True


def _frame(obj):
    """Hand an envelope dict to the transport as a (data, is_binary=False) TEXT frame.

    The dict travels unserialized: the transport stamps a resume sequence onto
    every Azaban frame, so serializing here would only force it to parse the
    result straight back. See ``WireFormat.supports_resume``.
    """
    return (obj, False)


class AzabanFormat(WireFormat):
    """
    Azaban shell protocol: typed JSON envelopes over TEXT frames.

    Text handling:
        Plain text (`text`/`prompt` outputfuncs) is HTML-converted (parse_html),
        same palette as the legacy client, wrapped in a typed envelope.

    Structured narrative:
        The `narrative` outputfunc (R1 deliver_node) carries RenderNode payload
        dicts; encoded as a ``render`` envelope the shell renders as nodes.

    OOB:
        Any other outputfunc becomes a typed ``oob`` envelope.
    """

    name = "azaban.v1"
    supports_oob = True
    #: Frames are JSON envelopes, so the transport may stamp ``s`` on them and
    #: buffer them for replay after a reconnect.
    supports_resume = True

    # -- outgoing (server -> client) ---------------------------------------

    def _html(self, text, protocol_flags, options):
        flags = protocol_flags or {}
        raw = options.get("raw", flags.get("RAW", False))
        client_raw = options.get("client_raw", False)
        nocolor = options.get("nocolor", flags.get("NOCOLOR", False))
        screenreader = options.get("screenreader", flags.get("SCREENREADER", False))
        if screenreader:
            text = parse_ansi(text, strip_ansi=True, xterm256=False, mxp=False)
            text = _RE_SCREENREADER_REGEX.sub("", text)
        if raw:
            if client_raw:
                return text
            import html as _html_lib

            return _html_lib.escape(text)
        return parse_html(text, strip_ansi=nocolor)

    def encode_text(self, *args, protocol_flags=None, **kwargs):
        if not args or args[0] is None:
            return None
        options = kwargs.pop("options", {}) or {}
        html = self._html(args[0], protocol_flags, options)
        kind = kwargs.get("type") or options.get("type")
        # Azaban is the structured client protocol: even a legacy string becomes
        # a render.v1 text node here. Other wire formats continue receiving the
        # original text outputfunc unchanged.
        from evennia.narrative.rendernode import text_node

        node = text_node(str(args[0]), msg_type=str(kind or "text")).payload()
        if _wants_server_html(protocol_flags):
            node["html"] = html
        return _frame({"t": "render", "nodes": [node]})

    def encode_prompt(self, *args, protocol_flags=None, **kwargs):
        if not args or args[0] is None:
            return None
        options = kwargs.pop("options", {}) or {}
        html = self._html(args[0], protocol_flags, options)
        return _frame({"t": "prompt", "html": html})

    def encode_res(self, *args, protocol_flags=None, **kwargs):
        """RPC reply to a client ``req``. session.msg(res=(data, seq[, ok])).

        Correlates back to the request via ``re`` (the request's ``seq``). On
        failure pass ok=False and the error payload in place of data.
        """
        data = args[0] if len(args) > 0 else kwargs.get("data")
        seq = args[1] if len(args) > 1 else kwargs.get("seq")
        ok = args[2] if len(args) > 2 else kwargs.get("ok", True)
        frame = {"t": "res", "re": seq, "ok": bool(ok)}
        if ok:
            frame["data"] = data
        else:
            frame["error"] = data
        return _frame(frame)

    def encode_default(self, cmdname, *args, protocol_flags=None, **kwargs):
        if cmdname == "options":
            return None
        if cmdname == "res":
            # RPC reply. The webclient protocol has no send_res, so res arrives
            # here via send_default; hand it to the dedicated encoder so the shell
            # sees a {t:res, re} frame (not a generic oob event).
            return self.encode_res(*args, protocol_flags=protocol_flags, **kwargs)
        if cmdname == "narrative":
            # R1 structured narrative. deliver_node sends a list of RenderNode
            # payload dicts, but the outbound path may spread it, so args can be
            # (payload,) OR ([payload],). Normalise to a list of dicts. Add a
            # server-parsed `html` per node (its `body` carries Evennia markup) so
            # the shell renders colour immediately; refs/spans drive interactivity.
            from evennia.utils.text2html import parse_html

            raw = list(args)
            if len(raw) == 1 and isinstance(raw[0], list):
                nodes = raw[0]
            else:
                nodes = raw
            if not all(isinstance(node, dict) for node in nodes):
                return None
            from evennia.narrative.rendernode import MAX_BODY_CHARS

            if (
                len(nodes) > _MAX_ITEMS
                or not all(_within_limits(node, max_string=MAX_BODY_CHARS) for node in nodes)
                or _contains_raw_identity(nodes)
            ):
                logger.log_warn("azaban: rejected unsafe narrative payload")
                return None
            attach_html = _wants_server_html(protocol_flags)
            safe_nodes = []
            for original in nodes:
                node = dict(original)
                if attach_html and node.get("body") and "html" not in node:
                    try:
                        node["html"] = parse_html(node["body"])
                    except Exception:
                        pass
                safe_nodes.append(node)
            return _frame({"t": "render", "nodes": safe_nodes})
        if cmdname == "patch":
            # Scene-model delta: {target, ops, meta} carried in kwargs.
            #
            # ``meta`` is an open dict, not a fixed schema: the multipuppet relay
            # ships routing/provenance there (``npc_id``, ``slot``, ``revision``,
            # ``action_id``, ``kind``, ``relay_mode``, plus the event's own meta)
            # and the shell routes on it. It is deliberately not allowlisted —
            # doing so would break that relay — so the guards below are what keep
            # it honest: no raw database identity, and bounded structure. Anything
            # new added here must be viewer-scoped and safe to put on the wire.
            ops = kwargs.get("ops", [])
            meta = kwargs.get("meta", {})
            if (
                not isinstance(meta, dict)
                or _contains_raw_identity((ops, meta))
                or not _within_limits((ops, meta))
            ):
                logger.log_warn("azaban: rejected unsafe scene patch")
                return None
            return _frame(
                {
                    "t": "patch",
                    "target": kwargs.get("target", "scene"),
                    "ops": ops,
                    "meta": meta,
                }
            )
        # Generic typed OOB event.
        return _frame({"t": "oob", "event": cmdname, "args": list(args), "kwargs": kwargs})

    # -- incoming (client -> server) ---------------------------------------

    def decode_incoming(self, payload, is_binary, protocol_flags=None):
        # Structural guard: cap the raw frame before parsing so a hostile client
        # cannot force a huge allocation, then bound the decoded shape.
        raw = bytes(payload) if not isinstance(payload, (bytes, bytearray)) else payload
        if len(raw) > _MAX_FRAME_BYTES:
            logger.log_warn("azaban: dropping oversized incoming frame (%d bytes)" % len(raw))
            return None
        try:
            env = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(env, dict):
            return None
        if not _within_limits(env):
            logger.log_warn("azaban: dropping incoming frame exceeding structural limits")
            return None

        t = env.get("t")
        if t == "cmd":
            # A command line.
            return {"text": [[str(env.get("line", ""))], {}]}
        if t == "websocket_close":
            return {"websocket_close": [[], {}]}
        if t == "hello":
            # Client capability announcement; routed to an inputfunc that can
            # stash caps on the session (no-op if unhandled).
            return {"azaban_hello": [[], {"caps": env.get("caps", {})}]}
        if t == "req":
            # RPC: {seq, ns, action, data} -> inputfunc <action>, with correlation
            # metadata in kwargs so the handler can reply via a `res` envelope.
            # The action must be an allowed public endpoint — unknown/internal
            # inputfunc names are not reachable from a client frame.
            action = env.get("action")
            ns = env.get("ns")
            if not _action_allowed(action, ns):
                logger.log_warn("azaban: rejected 'req' for disallowed action %r" % (action,))
                return None
            meta = {"seq": env.get("seq"), "ns": ns}
            return {action: [[env.get("data")], meta]}
        if t == "oob":
            action = env.get("action") or env.get("event")
            ns = env.get("ns")
            if not _action_allowed(action, ns):
                logger.log_warn("azaban: rejected 'oob' for disallowed action %r" % (action,))
                return None
            args = env.get("args", [])
            kwargs = env.get("kwargs", {})
            if not isinstance(args, list) or not isinstance(kwargs, dict):
                return None
            return {action: [args, kwargs]}
        return None
