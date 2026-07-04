"""Editor package: the protocol-agnostic edit core and (later) its frontends.

Today this exposes :class:`~evennia.utils.editor.core.EditCore`, the shared
document model. The legacy line editor
(:class:`evennia.utils.eveditor.EvEditor`) is the first frontend driving it; the
Mudlet/GMCP and webclient frontends are added in later phases per
``.agents/docs/engine-architecture/editor.md``.
"""

from evennia.utils.editor.core import EditCore

__all__ = ("EditCore",)
