"""Server-side routing facts for one delivery.

``msg`` treats every keyword as a command for the client, so a fact that only
the server needs travels here, in the ``route`` parameter, and never reaches a
session.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryRoute:
    """How one delivery reached its viewer.

    Args:
        perception_relay (bool): the delivery forwards a perception from
            another body. Relay hooks skip it, so it is not relayed again.
        rendered (bool): the node already ran its hooks and transforms; the
            receiving ``msg`` sends it on without running them again.
    """

    perception_relay: bool = False
    rendered: bool = False


NO_ROUTE = DeliveryRoute()
