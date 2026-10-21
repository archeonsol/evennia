"""
This module implements the main Evennia server process, the core of the game
engine.

This module should be started with the 'twistd' executable since it sets up all
the networking features.  (this is done automatically by
evennia/server/server_runner.py).

"""

import os
import sys

# Same bootstrap path setup as portal.py (see portal/portal.py).
if __name__ == "__main__":
    _bootstrap_dir = os.path.dirname(os.path.abspath(__file__))
    if sys.path and os.path.abspath(sys.path[0]) == _bootstrap_dir:
        sys.path.pop(0)
    _game_dir = os.getcwd()
    if _game_dir and _game_dir not in sys.path:
        sys.path.insert(0, _game_dir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.conf.settings")

import django

django.setup()

import evennia

evennia._init()


# twistd requires us to define the variable 'application' so it knows
# what to execute from.
# The guts of the application are in the service.py file,
# which is instantiated and attached to application in evennia._init()
application = evennia.TWISTED_APPLICATION

if __name__ == "__main__":
    from evennia.server.asyncio_bootstrap import run_server

    run_server()
