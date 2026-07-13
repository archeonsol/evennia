"""One-way compiler from legacy lockstrings to authorization policies."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..policy import (
    AllOf,
    Always,
    AnyOf,
    Never,
    Not,
    PredicateRequirement,
    RequiresCapability,
)

_FUNC_RE = re.compile(r"^(\w+)\((.*)\)$", re.DOTALL)
_CUSTOM_COMPILERS: dict[str, object] = {}


class CompilationError(ValueError):
    """Raised when a legacy lock cannot be migrated without losing semantics."""


@dataclass(frozen=True, slots=True)
class SynthesizedGrant:
    """A positive resource grant derived from an identity lock."""

    principal_ref: str
    capability: str
    scope_kind: str
    scope_key: str
    provenance: str


@dataclass(frozen=True, slots=True)
class CompilationResult:
    """Policies and grants produced from one lock storage value."""

    policies: dict[str, object]
    grants: tuple[SynthesizedGrant, ...]
    source: str
    warnings: tuple[str, ...] = ()


def register_lock_compiler(name: str, compiler) -> None:
    """Register a deterministic compiler for one game/plugin lock function.

    Args:
        name: Legacy lock-function name.
        compiler: Callable accepting ``(args, access_type, resource_ref)`` and
            returning a structured policy node.
    """

    key = str(name or "").strip().lower()
    if not key or not callable(compiler):
        raise ValueError("lock compilers require a name and callable")
    existing = _CUSTOM_COMPILERS.get(key)
    if existing is not None and existing is not compiler:
        raise ValueError(f"lock compiler {key!r} is already registered")
    _CUSTOM_COMPILERS[key] = compiler


def _args(raw: str) -> list[str]:
    """Return positional lock-function arguments."""

    return [part.strip() for part in raw.split(",") if part.strip() and "=" not in part]


def _kwargs(raw: str) -> dict[str, str]:
    """Return bounded keyword lock-function arguments."""

    result = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, value = (item.strip() for item in part.split("=", 1))
        if key:
            result[key] = value
    return result


def _capability_for_access(resource_ref: str, access_type: str) -> str:
    """Map a legacy access type to a namespaced capability."""

    kind = (resource_ref.split(":", 1)[0] if ":" in resource_ref else "object").lower()
    aliases = {
        ("object", "traverse"): "engine.exit.traverse",
        ("object", "puppet"): "engine.character.puppet",
        ("command", "cmd"): "engine.command.execute",
        ("channel", "listen"): "engine.channel.listen",
        ("channel", "send"): "engine.channel.send",
        ("channel", "control"): "engine.channel.control",
        ("help", "read"): "engine.help.read",
        ("help", "view"): "engine.help.read",
        ("script", "control"): "engine.script.control",
    }
    return aliases.get((kind, access_type), f"engine.object.{access_type}")


def _tokenize(rhs: str):
    """Tokenize Boolean lock syntax while preserving grouping parentheses."""

    tokens = []
    position = 0
    length = len(rhs)
    while position < length:
        if rhs[position].isspace():
            position += 1
            continue
        if rhs[position] in "()":
            tokens.append(("lparen" if rhs[position] == "(" else "rparen",))
            position += 1
            continue
        word = re.match(r"[A-Za-z_]\w*", rhs[position:])
        if not word:
            raise CompilationError(f"invalid token near {rhs[position:]!r}")
        name = word.group(0)
        position += len(name)
        lowered = name.lower()
        if lowered in {"and", "or", "not"}:
            tokens.append((lowered,))
            continue
        while position < length and rhs[position].isspace():
            position += 1
        if position >= length or rhs[position] != "(":
            raise CompilationError(f"lock function {name!r} has no argument list")
        start = position
        depth = 0
        while position < length:
            char = rhs[position]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    position += 1
                    break
            position += 1
        if depth:
            raise CompilationError(f"lock function {name!r} has unmatched parentheses")
        tokens.append(("func", name + rhs[start:position]))
    return tokens


def _parse_clause(rhs, access_type, resource_ref, grants, warnings):
    """Parse one legacy Boolean clause into a structured policy."""

    tokens = _tokenize(rhs)
    if not tokens:
        raise CompilationError(f"empty or invalid lock clause {rhs!r}")
    position = 0

    def atom(token):
        match = _FUNC_RE.match(token)
        if not match:
            raise CompilationError(f"invalid lock atom {token!r}")
        name = match.group(1).lower()
        raw_args = match.group(2)
        args = _args(raw_args)
        kwargs = _kwargs(raw_args)
        if name in {"all", "true"}:
            return Always()
        if name in {"none", "false"}:
            return Never()
        if name in {"perm", "pperm", "perm_above", "pperm_above"}:
            if not args:
                raise CompilationError(f"{name} requires a permission")
            permission = args[0].rstrip("s").lower()
            if name in {"perm_above", "pperm_above"}:
                hierarchy = ("guest", "player", "helper", "builder", "admin", "developer")
                try:
                    permission = hierarchy[hierarchy.index(permission) + 1]
                except (ValueError, IndexError) as err:
                    raise CompilationError(
                        f"cannot migrate permission above {permission!r}"
                    ) from err
            scope = "account" if name.startswith("p") else "effective"
            return RequiresCapability(f"legacy.permission.{permission}", scope)
        if name in {"id", "dbref", "pid", "pdbref"}:
            if not args or not resource_ref:
                raise CompilationError("identity locks require a principal and resource")
            principal_kind = "account" if name.startswith("p") else "entity"
            try:
                principal_ref = f"{principal_kind}:{int(args[0].lstrip('#'))}"
            except ValueError:
                # Legacy dbref/id() also returned false for a nonnumeric value.
                # Preserve that result while surfacing the malformed authored
                # input in migration diagnostics.
                warnings.append(f"{access_type}: invalid identity {args[0]!r} became never")
                return Never()
            capability = _capability_for_access(resource_ref, access_type)
            grants.append(
                SynthesizedGrant(
                    principal_ref,
                    capability,
                    "resource",
                    resource_ref,
                    "legacy_id_lock",
                )
            )
            return RequiresCapability(capability)
        core_contextual = {
            "attr",
            "objattr",
            "locattr",
            "objlocattr",
            "attr_eq",
            "attr_gt",
            "attr_ge",
            "attr_lt",
            "attr_le",
            "attr_ne",
            "tag",
            "objtag",
            "objloctag",
            "holds",
            "inside",
            "inside_rec",
            "self",
            "has_account",
            "is_ooc",
            "serversetting",
            "superuser",
        }
        if name in core_contextual:
            params = {"name": name}
            params.update({f"arg{index}": value for index, value in enumerate(args)})
            params.update({f"kw_{key}": value for key, value in kwargs.items()})
            return PredicateRequirement("legacy.core_lockfunc", params)
        custom = _CUSTOM_COMPILERS.get(name)
        if custom is not None:
            policy = custom(tuple(args), access_type, resource_ref)
            if not hasattr(policy, "to_data"):
                raise CompilationError(f"custom compiler {name!r} returned no policy")
            return policy
        raise CompilationError(f"unsupported legacy lock function {name!r}")

    def parse_primary():
        nonlocal position
        if position < len(tokens) and tokens[position][0] == "lparen":
            position += 1
            node = parse_or()
            if position >= len(tokens) or tokens[position][0] != "rparen":
                raise CompilationError(f"unclosed Boolean group in {rhs!r}")
            position += 1
            return node
        if position >= len(tokens) or tokens[position][0] != "func":
            raise CompilationError(f"invalid lock expression {rhs!r}")
        node = atom(tokens[position][1])
        position += 1
        return node

    def parse_not():
        nonlocal position
        if position < len(tokens) and tokens[position][0] == "not":
            position += 1
            return Not(parse_not())
        return parse_primary()

    def parse_and():
        nonlocal position
        parts = [parse_not()]
        while position < len(tokens) and tokens[position][0] == "and":
            position += 1
            parts.append(parse_not())
        return parts[0] if len(parts) == 1 else AllOf(tuple(parts))

    def parse_or():
        nonlocal position
        parts = [parse_and()]
        while position < len(tokens) and tokens[position][0] == "or":
            position += 1
            parts.append(parse_and())
        return parts[0] if len(parts) == 1 else AnyOf(tuple(parts))

    policy = parse_or()
    if position != len(tokens):
        raise CompilationError(f"unconsumed lock expression in {rhs!r}")
    return policy


def compile_lockstring(lockstring: str, *, resource_ref: str = "object:unknown"):
    """Compile every access clause in a lock storage string.

    Args:
        lockstring: Semicolon-separated legacy lock definitions.
        resource_ref: Canonical resource receiving the migrated policies.

    Returns:
        A compilation result containing policies and synthesized positive grants.
    """

    policies = {}
    grants = []
    warnings = []
    for raw_clause in str(lockstring or "").split(";"):
        clause = raw_clause.strip()
        if not clause:
            continue
        try:
            access_type, rhs = (part.strip() for part in clause.split(":", 1))
        except ValueError as err:
            raise CompilationError(f"lock clause has no access type: {clause!r}") from err
        policies[access_type] = _parse_clause(rhs, access_type, resource_ref, grants, warnings)
    if not policies:
        raise CompilationError("lock storage contains no policies")
    return CompilationResult(policies, tuple(grants), str(lockstring), tuple(warnings))
