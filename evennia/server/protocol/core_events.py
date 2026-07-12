"""Engine-shipped OOB events: session lifecycle, media, and the UI primitive.

Games register their own events in a module listed in
settings.PROTOCOL_EVENT_MODULES. This is the engine's baseline, always loaded.
"""

from evennia.server.protocol import register_event

# -- session --------------------------------------------------------------
register_event("logout", carrier="args", fields={"_": "str"}, doc="args[0] = reason (e.g. 'quit').")

# -- media ----------------------------------------------------------------
register_event("image", carrier="args", fields={"_": "str"}, doc="args[0] = image URL.")
register_event("audio", carrier="args", fields={"_": "str"}, doc="args[0] = audio URL.")
register_event("video", carrier="args", fields={"_": "str"}, doc="args[0] = video URL.")
register_event("youtube", carrier="args", fields={"_": "str"}, doc="args[0] = YouTube URL.")

# -- server-driven UI primitive (evennia.server.ui) -----------------------
register_event(
    "ui_component",
    carrier="args",
    fields={"_": "dict"},
    doc="A UI component spec (see evennia.server.ui).",
)
register_event("ui_remove", carrier="args", fields={"id": "str"})

# -- embedded web page (folds magic-link pages into the shell as an iframe) -
# args[0] = {"url": str, "title"?: str, "id"?: str}. The shell opens the URL in
# a floating iframe panel; the page authenticates via the browser's shared
# Django session (SharedLoginMiddleware), so no magic-link token is needed for
# webclient sessions. Telnet/3rd-party clients get the link as text instead.
register_event(
    "web_panel",
    carrier="args",
    fields={"_": "dict"},
    doc="Open an embedded web page in the shell: args[0] = {url, title?, id?}.",
)
