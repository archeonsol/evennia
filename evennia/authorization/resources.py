"""Extensible resource adapters for authorization references and scope labels."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResourceAdapter:
    """Describe how one engine/game resource family participates in authorization."""

    kind: str
    matches: object
    reference: object
    labels: object = lambda resource: ()
    priority: int = 0

    def __post_init__(self):
        """Validate callables and normalize the resource kind."""

        kind = str(self.kind or "").strip().lower()
        if not kind or not all(
            callable(value) for value in (self.matches, self.reference, self.labels)
        ):
            raise ValueError("resource adapters require a kind and callable hooks")
        object.__setattr__(self, "kind", kind)


class ResourceAdapterRegistry:
    """Priority-ordered game-extensible resource classification."""

    def __init__(self):
        """Initialize an empty adapter set."""

        self._adapters: dict[str, ResourceAdapter] = {}

    def register(self, adapter: ResourceAdapter) -> ResourceAdapter:
        """Register an adapter idempotently."""

        existing = self._adapters.get(adapter.kind)
        if existing is not None and existing != adapter:
            raise ValueError(f"resource adapter {adapter.kind!r} is already registered")
        self._adapters[adapter.kind] = adapter
        return adapter

    def for_resource(self, resource) -> ResourceAdapter | None:
        """Return the highest-priority matching adapter."""

        for adapter in sorted(
            self._adapters.values(), key=lambda item: (-item.priority, item.kind)
        ):
            if adapter.matches(resource):
                return adapter
        return None


resource_adapters = ResourceAdapterRegistry()
