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
