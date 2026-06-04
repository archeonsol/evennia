"""
Evennia action system (CM1).

A typed-action + rule-phase dispatch engine that replaces the cmdset model.
This package is built incrementally (see the CM1 roadmap). Phase 0f delivers
the permission/predicate layer that eliminates string locks; the remaining
modules (action, rule, engine, actor, ...) land in later phases.

Flat re-exports are added here as each module stabilizes. For now only the
permission lattice and predicate algebra are public.
"""

from .permission import (
    Capability,
    DefaultCapability,
    Scope,
    STAFF,
    get_capability_enum,
    rank_order,
    resolve_capabilities,
    capability_for_name,
)
from .predicate import (
    Predicate,
    HasCapability,
    Holds,
    HasTag,
    HasAttr,
    IsSelf,
    IsObject,
    And,
    Or,
    Not,
    Builder,
    Admin,
    Developer,
    Player,
    Helper,
    Guest,
    IsAlive,
    InSameRoom,
    ALWAYS,
    NEVER,
    coerce_predicate,
    from_lockstring,
    LegacyLock,
)
from .exceptions import (
    ActionError,
    RuleConflict,
    ParseError,
    AmbiguousTarget,
)
from .result import (
    RuleResult,
    PASS,
    SKIP,
    CLAIM,
    SILENT_FAIL,
    FAIL,
    REDIRECT,
    PhaseTrace,
    ActionTrace,
)
from .rule import rule, RuleSpec, PHASES
from .registry import (
    ActionRegistry,
    RuleRegistry,
    action_registry,
    rule_registry,
)
from .action import Action, action, GameObject
from .context import ActionContext, ActionContextBuilder, build_context
from .engine import RuleEngine, engine
from .events import Event, subscribe, EventSpec, EventRegistry, event_registry
from .process import (
    Activity,
    start_activity,
    cancel_activity,
    get_activities,
    active_activity,
    is_active,
)
from .parser import (
    ParseResult,
    ActionParser,
    parser,
    NoInputAction,
    NoMatchAction,
    LoginStartAction,
    DynamicVerbResolver,
)
from .state import (
    StateProvider,
    enter_state,
    exit_state,
    has_state,
    get_states,
)
from .actor import Actor
from .perception import (
    set_visibility_filter,
    get_visibility_filter,
    is_visible,
    filter_visible,
)
from .menus import (
    MenuInputAction,
    MenuPrompt,
    InputCaptureState,
    EvMenuState,
    DisambiguationState,
    format_menu_prompt,
    parse_menu_choice,
)
from .default import (
    Moved,
    Departed,
    Arrived,
    Move,
    Locomotion,
    ExitTraversalRules,
    CharacterMovementRules,
    exit_resolver,
    register_exit_resolver,
    Enterable,
    Get,
    Drop,
    Give,
    Put,
    Enter,
    CharacterObjectRules,
    ContainerPutRules,
    EnterableObjectRules,
)
from .dispatch import (
    try_action_dispatch,
    DispatchMiddleware,
    ProfilingMiddleware,
    register_middleware,
    clear_middlewares,
    get_middlewares,
)

__all__ = [
    "Capability",
    "DefaultCapability",
    "Scope",
    "STAFF",
    "get_capability_enum",
    "rank_order",
    "resolve_capabilities",
    "capability_for_name",
    "Predicate",
    "HasCapability",
    "Holds",
    "HasTag",
    "HasAttr",
    "IsSelf",
    "IsObject",
    "And",
    "Or",
    "Not",
    "Builder",
    "Admin",
    "Developer",
    "Player",
    "Helper",
    "Guest",
    "IsAlive",
    "InSameRoom",
    "ALWAYS",
    "NEVER",
    "coerce_predicate",
    "from_lockstring",
    "LegacyLock",
    # exceptions
    "ActionError",
    "RuleConflict",
    "ParseError",
    "AmbiguousTarget",
    # results + trace
    "RuleResult",
    "PASS",
    "SKIP",
    "CLAIM",
    "SILENT_FAIL",
    "FAIL",
    "REDIRECT",
    "PhaseTrace",
    "ActionTrace",
    # rules + registry
    "rule",
    "RuleSpec",
    "PHASES",
    "ActionRegistry",
    "RuleRegistry",
    "action_registry",
    "rule_registry",
    # actions
    "Action",
    "action",
    "GameObject",
    # engine + context
    "ActionContext",
    "ActionContextBuilder",
    "build_context",
    "RuleEngine",
    "engine",
    # events + activities
    "Event",
    "subscribe",
    "EventSpec",
    "EventRegistry",
    "event_registry",
    "Activity",
    "start_activity",
    "cancel_activity",
    "get_activities",
    "active_activity",
    "is_active",
    # parser + system actions
    "ParseResult",
    "ActionParser",
    "parser",
    "NoInputAction",
    "NoMatchAction",
    "LoginStartAction",
    "DynamicVerbResolver",
    # actor + state
    "Actor",
    "set_visibility_filter",
    "get_visibility_filter",
    "is_visible",
    "filter_visible",
    "StateProvider",
    "enter_state",
    "exit_state",
    "has_state",
    "get_states",
    # interaction states
    "MenuInputAction",
    "MenuPrompt",
    "InputCaptureState",
    "EvMenuState",
    "DisambiguationState",
    "format_menu_prompt",
    "parse_menu_choice",
    # default actions (shipped movement substrate)
    "Moved",
    "Departed",
    "Arrived",
    "Move",
    "Locomotion",
    "ExitTraversalRules",
    "CharacterMovementRules",
    "exit_resolver",
    "register_exit_resolver",
    # default actions (shipped object-manipulation substrate)
    "Enterable",
    "Get",
    "Drop",
    "Give",
    "Put",
    "Enter",
    "CharacterObjectRules",
    "ContainerPutRules",
    "EnterableObjectRules",
    # cmdhandler bridge (Phase 4)
    "try_action_dispatch",
    "DispatchMiddleware",
    "ProfilingMiddleware",
    "register_middleware",
    "clear_middlewares",
    "get_middlewares",
]
