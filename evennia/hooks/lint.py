"""Lint the engine hook surface against the registry.

Checks every ``at_*``/``get_*``/``return_*`` method on engine-defined
typeclasses (or their mixins) is registered via ``@hook``, that each
spec's ``fires_from`` paths resolve to real callables, and that
``at_pre_*``/``at_post_*``/``at_failed_*`` names match their declared
``phase``. Game-side overrides inherit registration silently and are
out of scope.
"""

import importlib

from .specs import LintFinding

_HOOK_NAME_PREFIXES = ("at_", "get_", "return_")

# Phase implied by name prefix; entries omitted (``at_<event>``) are
# unconstrained because the prefix is overloaded by the contract.
_NAME_PHASE = {
    "at_pre_": "pre",
    "at_post_": "post",
    "at_failed_": "failed",
}

# Base classes whose engine-side subclasses (and the bases themselves)
# are in lint scope. Loaded lazily so importing the lint module does
# not eagerly drag in Django.
_BASE_CLASS_PATHS = (
    "evennia.objects.objects:DefaultObject",
    "evennia.objects.objects:DefaultCharacter",
    "evennia.objects.objects:DefaultRoom",
    "evennia.objects.objects:DefaultExit",
    "evennia.accounts.accounts:DefaultAccount",
    "evennia.accounts.accounts:DefaultGuest",
    "evennia.comms.comms:DefaultChannel",
    "evennia.scripts.scripts:DefaultScript",
    "evennia.server.serversession:ServerSession",
    "evennia.objects.mixins.lifecycle:LifecycleMixin",
    "evennia.objects.mixins.movement:MovementMixin",
    "evennia.objects.mixins.appearance:AppearanceMixin",
    "evennia.objects.mixins.messaging:MessagingMixin",
    "evennia.objects.mixins.search:SearchMixin",
    "evennia.typeclasses.models:TypedObject",
)


def _resolve(path):
    """Resolve ``"module:attr"`` to the attribute, or ``None`` if missing."""
    mod_name, _, attr = path.partition(":")
    try:
        module = importlib.import_module(mod_name)
    except ImportError:
        return None
    return getattr(module, attr, None)


def _load_engine_bases():
    """Return the resolved engine base classes (skips any that fail to import)."""
    classes = []
    for path in _BASE_CLASS_PATHS:
        cls = _resolve(path)
        if cls is not None:
            classes.append(cls)
    return classes


def _engine_class_set(bases):
    """Walk base subclasses recursively, keeping only classes from ``evennia.*``."""
    seen = set()

    def _walk(cls):
        if cls in seen:
            return
        seen.add(cls)
        for sub in cls.__subclasses__():
            _walk(sub)

    for base in bases:
        _walk(base)

    # Contrib and game-template classes are game-shaped, not engine.
    # They opt in to registration but lint does not require it.
    excluded_prefixes = ("evennia.contrib.", "evennia.game_template.")
    return [
        c
        for c in seen
        if (c.__module__ or "").startswith("evennia.")
        and not any((c.__module__ or "").startswith(p) for p in excluded_prefixes)
    ]


def _is_hook_name(name):
    if name.startswith("__"):
        return False
    return any(name.startswith(p) for p in _HOOK_NAME_PREFIXES)


def _unwrap(attr):
    if isinstance(attr, (classmethod, staticmethod)):
        return attr.__func__
    return attr


def _has_inherited_spec(cls, name):
    """True if any ancestor's same-name method carries ``__evennia_hook__``."""
    for ancestor in cls.__mro__[1:]:
        attr = ancestor.__dict__.get(name)
        if attr is not None and hasattr(_unwrap(attr), "__evennia_hook__"):
            return True
    return False


# Handler classes that fire hooks but are neither typeclasses nor
# typeclass ancestors. Listed here so fires_from strings like
# "CmdSetHandler.get" resolve cleanly without forcing a full
# module-walk during lint.
_AUX_HANDLER_PATHS = (
    "evennia.commands.cmdsethandler:CmdSetHandler",
    "evennia.server.serversession:ServerSession",
    "evennia.server.service:EvenniaServerService",
)


def _resolve_fires_from(path):
    """Resolve a ``"Class.method"`` string to a callable.

    Searches the engine class set first, then ancestors of those classes
    (to reach parents like ``SharedMemoryModel`` that fire hooks but
    are not themselves registered bases), then a small set of known
    handler classes. Returns the callable or ``None`` if unresolved.
    """
    class_name, _, method_name = path.partition(".")
    if not method_name:
        return None
    candidates = set()
    for cls in _engine_class_set(_load_engine_bases()):
        candidates.add(cls)
        candidates.update(cls.__mro__)
    for aux_path in _AUX_HANDLER_PATHS:
        cls = _resolve(aux_path)
        if cls is not None:
            candidates.add(cls)
    for cls in candidates:
        if cls.__name__ == class_name:
            method = getattr(cls, method_name, None)
            if callable(method):
                return method
    return None


def _check_class(cls):
    """Return findings for methods on ``cls`` that lack a spec."""
    findings = []
    for name, attr in cls.__dict__.items():
        if not _is_hook_name(name):
            continue
        unwrapped = _unwrap(attr)
        if not callable(unwrapped):
            continue
        # Skip class-level aliases: `foo = some_other_function` rebinds an
        # existing function whose qualname ends with a different method
        # name. Not a new method definition; lint the original definition
        # instead.
        qualname = getattr(unwrapped, "__qualname__", "")
        if qualname and not qualname.endswith(f".{name}"):
            continue
        if hasattr(unwrapped, "__evennia_hook__"):
            continue
        if _has_inherited_spec(cls, name):
            continue
        findings.append(
            LintFinding(
                code="MISSING_DECORATOR",
                message=f"{cls.__name__}.{name} has no @hook registration",
                location=f"{cls.__module__}.{cls.__name__}.{name}",
            )
        )
    return findings


def _check_spec_fires_from(qualname, spec):
    findings = []
    for path in spec.fires_from:
        if _resolve_fires_from(path) is None:
            findings.append(
                LintFinding(
                    code="UNRESOLVED_FIRES_FROM",
                    message=f"{qualname}: fires_from {path!r} does not resolve",
                    location=qualname,
                )
            )
    return findings


def _check_spec_phase_name(qualname, spec):
    method_name = qualname.rsplit(".", 1)[-1]
    for prefix, expected_phase in _NAME_PHASE.items():
        if method_name.startswith(prefix):
            if spec.phase and spec.phase != expected_phase:
                return [
                    LintFinding(
                        code="PHASE_MISMATCH",
                        message=(
                            f"{qualname}: name prefix {prefix!r} implies phase="
                            f"{expected_phase!r}, declared {spec.phase!r}"
                        ),
                        location=qualname,
                    )
                ]
            break
    return []


def lint(classes=None):
    """Return all lint findings.

    Args:
        classes: Optional iterable of classes to lint instead of the
            default engine class set. Used by tests.

    Returns:
        list[LintFinding]: All findings; empty list means clean.
    """
    from .registry import _REGISTRY

    if classes is None:
        classes = _engine_class_set(_load_engine_bases())

    findings = []
    for cls in classes:
        findings.extend(_check_class(cls))
    for qualname, spec in _REGISTRY.items():
        if spec.extends is not None:
            continue
        findings.extend(_check_spec_fires_from(qualname, spec))
        findings.extend(_check_spec_phase_name(qualname, spec))
    return findings


def warn_at_startup():
    """Run ``lint()`` and write findings to the twisted logger.

    Warn-only by design: an engine documentation issue should never
    block a game from booting. Strict enforcement lives in
    ``evennia.hooks.tests.test_lint.PilotIntegrationTest.test_engine_lint_is_clean``,
    which fires during ``evennia test`` and fails CI if any engine hook
    is undecorated, has unresolved fires_from, or mismatches its phase.

    Called from ``ServerService.run_init_hooks``. Safe to call before
    all engine classes are imported; classes that load later are
    linted on the next boot.
    """
    from evennia.utils import logger

    findings = lint()
    if not findings:
        logger.log_msg("Hook registry lint: clean.")
        return
    logger.log_msg(f"Hook registry lint: {len(findings)} finding(s).")
    for finding in findings:
        logger.log_msg(f"  [{finding.code}] {finding.message}")
