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

Server -> client types: hello, text, prompt, render, oob, res.
Client -> server types: hello, cmd, req, oob, websocket_close.

See ``evennia/.agents/docs/engine-architecture/webclient-protocol.md``.
"""

import json

from evennia.utils.ansi import parse_ansi
from evennia.utils.text2html import parse_html

from .base import _RE_SCREENREADER_REGEX, WireFormat


def _frame(obj):
    """Encode an envelope dict as a (bytes, is_binary=False) TEXT frame."""
    return (json.dumps(obj).encode("utf-8"), False)


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
        env = {"t": "text", "html": html}
        kind = kwargs.get("type") or options.get("type")
        if kind:
            env["kind"] = kind
        return _frame(env)

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
            for node in nodes:
                if isinstance(node, dict) and node.get("body") and "html" not in node:
                    try:
                        node["html"] = parse_html(node["body"])
                    except Exception:
                        pass
            return _frame({"t": "render", "nodes": nodes})
        if cmdname == "patch":
            # Scene-model delta: {target, ops} carried in kwargs.
            return _frame(
                {
                    "t": "patch",
                    "target": kwargs.get("target", "scene"),
                    "ops": kwargs.get("ops", []),
                }
            )
        # Generic typed OOB event.
        return _frame({"t": "oob", "event": cmdname, "args": list(args), "kwargs": kwargs})

    # -- incoming (client -> server) ---------------------------------------

    def decode_incoming(self, payload, is_binary, protocol_flags=None):
        try:
            env = json.loads(str(payload, "utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(env, dict):
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
            action = env.get("action")
            if not action:
                return None
            meta = {"seq": env.get("seq"), "ns": env.get("ns")}
            return {action: [[env.get("data")], meta]}
        if t == "oob":
            action = env.get("action") or env.get("event")
            if not action:
                return None
            return {action: [env.get("args", []), env.get("kwargs", {})]}
        return None
