"""Engine-shipped outputfunc signatures (the well-known session frame commands).

Always loaded (imported by the protocol package). Games/contribs may register
extra outputfuncs the same way via ``register_outputfunc``.
"""

from evennia.server.protocol.outputfuncs import register_outputfunc

# text: args[0] is the message (str, or a (text, options) tuple serialized as a
# list); kwargs carry arbitrary display options -> permissive.
register_outputfunc("text", args=["any"], doc="args[0] = message text (or [text, options]).")
register_outputfunc("prompt", args=["any"], doc="args[0] = prompt text.")
register_outputfunc("default", args=["any"], doc="Catch-all outputfunc; args[0] = message.")
register_outputfunc("options", kwargs={"_map?": "dict"}, doc="Protocol/session options as kwargs.")

# media / session control mirror the OOB event catalog but ride the frame too.
register_outputfunc("logout", args=["any"], doc="args[0] = logout reason.")

# Wire primitives: the web wire formats encode these as their own envelopes.
register_outputfunc("narrative", args=["any"], doc="Structured render payloads.")
register_outputfunc("patch", args=["any"], doc="Scene or puppet model patch ops.")
register_outputfunc("res", args=["any"], doc="Reply to a client request.")

# Replies to inputfuncs (evennia.server.inputfuncs).
for _name in (
    "client_options",
    "get_inputfuncs",
    "get_value",
    "monitor",
    "monitored",
    "report",
    "webclient_options",
    "commands",
    "lists",
    "configurable_variables",
    "reportable_variables",
    "reported_variables",
    "sendable_variables",
    "send",
):
    register_outputfunc(_name, args=["any"], doc="Inputfunc reply.")

# Bot commands, sent to the IRC, Grapevine and Discord portal protocols.
for _name in (
    "request_nicklist",
    "ping",
    "reconnect",
    "channel",
    "privmsg",
    "nickname",
    "role",
    "remove_role",
    "interaction_reply",
    "register_commands",
    "presence",
    "create_thread",
    "thread_message",
    "thread_update",
    "thread_archive",
    "dm",
    "authenticate",
    "heartbeat",
    "subscribe",
    "unsubscribe",
):
    register_outputfunc(_name, args=["any"], doc="Bot protocol command.")
