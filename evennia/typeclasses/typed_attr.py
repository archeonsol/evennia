"""
TypedAttr — declared attribute state for Evennia TypedObjects.

Workstream A of the A1 attribute storage revamp. Operates over the existing
AttributeHandler / AttributeDB storage; no schema changes required.

Three backends
--------------
``"blob"`` (default)
    Equivalent to AttributeProperty with optional type/range/choice
    validation. One Attribute row per key. Existing .db and
    AttributeProperty code is completely unchanged.

``"typed_col"``
    Like ``"blob"`` but asserts the value is a JSON-primitive type and
    exposes ``filter_kwargs()`` for building ORM queries against the
    already-shipped typed columns (db_int_val / db_float_val / db_str_val).

``"bag"``
    The entire subsystem lives in ONE Attribute row whose value is a dict.
    Sub-keys are accessed as attributes via AttributeBag. Mutations write
    back automatically through Evennia's _SaverDict mechanism; no explicit
    .save() is needed. Formalises the pattern already used by TraitHandler
    and BuffHandler.

Schema versioning
-----------------
Typeclasses may declare ``_attr_schema_version`` (int) and
``_attr_migrations`` (dict of version → [op, ...]).  Call
``apply_schema_migrations(obj)`` is called automatically from
``TypedObject.at_post_load()`` for every object on cache load. No manual
wiring is required; just declare ``_attr_schema_version`` and
``_attr_migrations`` on the typeclass.

Usage::

    from evennia.typeclasses.typed_attr import TypedAttr, AttrField

    class Character(DefaultCharacter):
        level = TypedAttr(int, default=1)
        xp    = TypedAttr(int, default=0, backend="typed_col", min=0)
        stats = TypedAttr(backend="bag", keys={
            "strength":  AttrField(int, default=10, min=1, max=20),
            "dexterity": AttrField(int, default=10, min=1, max=20),
        })

        _attr_schema_version = 2
        _attr_migrations = {
            2: [RenameAttr("hit_points", "hp")],
        }
"""

from copy import copy

from evennia.utils.dbserialize import from_pickle

__all__ = (
    "AttrField",
    "AttributeBag",
    "TypedAttr",
    "RenameAttr",
    "TransformAttr",
    "DropAttr",
    "apply_schema_migrations",
)

# Sentinel for "no default provided" — distinct from None.
_UNSET = object()

# Attribute key/category used to stamp schema versions on objects.
_SCHEMA_VERSION_KEY = "_schema_v"
_SCHEMA_VERSION_CATEGORY = "_meta"

# JSON-primitive types recognised by the typed columns.
_TYPED_COL_TYPES = (int, float, str, bool, type(None))


# ---------------------------------------------------------------------------
# AttrField
# ---------------------------------------------------------------------------


class AttrField:
    """
    Schema declaration for one sub-key inside a TypedAttr bag.

    Args:
        type_ (type, optional): Expected Python type. None skips type checking.
        default: Default value returned when the key is absent. Omit (or pass
            _UNSET) for a required key with no default.
        min: Minimum allowed value (uses ``<`` comparison).
        max: Maximum allowed value (uses ``>`` comparison).
        choices (iterable, optional): Exhaustive set of allowed values.
        nullable (bool): If False, None is rejected. Default True.
    """

    __slots__ = ("type_", "default", "min", "max", "choices", "nullable")

    def __init__(
        self,
        type_=None,
        default=_UNSET,
        *,
        min=None,
        max=None,
        choices=None,
        nullable=True,
    ):
        self.type_ = type_
        self.default = default
        self.min = min
        self.max = max
        self.choices = choices
        self.nullable = nullable

    def validate(self, name, value):
        """
        Validate *value* against this field's constraints.

        Raises:
            ValueError: nullable / range / choices violation.
            TypeError: type mismatch.
        """
        if value is None:
            if not self.nullable:
                raise ValueError(f"Bag field '{name}' is not nullable.")
            return
        if self.type_ is not None and not isinstance(value, self.type_):
            raise TypeError(
                f"Bag field '{name}' expected {self.type_.__name__}, got {type(value).__name__}."
            )
        if self.min is not None and value < self.min:
            raise ValueError(f"Bag field '{name}' value {value!r} is below minimum {self.min}.")
        if self.max is not None and value > self.max:
            raise ValueError(f"Bag field '{name}' value {value!r} exceeds maximum {self.max}.")
        if self.choices is not None and value not in self.choices:
            raise ValueError(
                f"Bag field '{name}' value {value!r} is not in choices {self.choices!r}."
            )


# ---------------------------------------------------------------------------
# AttributeBag
# ---------------------------------------------------------------------------


class AttributeBag:
    """
    Attribute-style access to a single dict-valued Attribute (the "bag").

    The bag is backed by a regular Evennia Attribute whose value is a plain
    dict. Mutations to sub-keys write back automatically through Evennia's
    ``_SaverDict`` mechanism — no explicit ``.save()`` is needed.

    This class may be used standalone to replace hand-rolled batched-blob
    handlers::

        from evennia.utils.utils import lazy_property
        from evennia.typeclasses.typed_attr import AttributeBag, AttrField

        class Character(DefaultCharacter):
            @lazy_property
            def stats(self):
                return AttributeBag(self, "stats", schema={
                    "strength": AttrField(int, default=10),
                })

    Or declared via ``TypedAttr(backend="bag")`` for a more concise syntax.

    Sub-key access::

        char.stats.strength          # read
        char.stats.strength = 15     # write (auto-persists)
        "strength" in char.stats     # membership
        char.stats.get("foo", 0)     # safe read with default
    """

    def __init__(self, obj, key, category=None, schema=None):
        # Use object.__setattr__ to avoid triggering our own __setattr__.
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_key", key)
        object.__setattr__(self, "_cat", category)
        object.__setattr__(self, "_schema", schema or {})

    # ------------------------------------------------------------------
    # Internal: fetch (and lazily initialise) the backing _SaverDict.
    # ------------------------------------------------------------------

    def _data(self):
        """
        Return the _SaverDict backing this bag.

        If the Attribute does not exist yet, it is created now and
        populated with schema defaults so the first write goes to a real
        Attribute row rather than being silently lost.
        """
        obj = object.__getattribute__(self, "_obj")
        key = object.__getattribute__(self, "_key")
        cat = object.__getattribute__(self, "_cat")
        schema = object.__getattribute__(self, "_schema")

        val = obj.attributes.get(key, category=cat)
        if val is None:
            defaults = {
                fname: copy(field.default)
                for fname, field in schema.items()
                if field.default is not _UNSET
            }
            obj.attributes.add(key, defaults, category=cat)
            val = obj.attributes.get(key, category=cat)
        return val

    # ------------------------------------------------------------------
    # Attribute-style access — read
    # ------------------------------------------------------------------

    def __getattr__(self, name):
        # Only called when normal attribute lookup fails (i.e. not _obj etc.)
        schema = object.__getattribute__(self, "_schema")
        data = self._data()
        if data is not None and name in data:
            return data[name]
        if name in schema and schema[name].default is not _UNSET:
            return copy(schema[name].default)
        key = object.__getattribute__(self, "_key")
        raise AttributeError(f"Bag '{key}' has no sub-key '{name}'.")

    # ------------------------------------------------------------------
    # Attribute-style access — write
    # ------------------------------------------------------------------

    def __setattr__(self, name, value):
        schema = object.__getattribute__(self, "_schema")
        if name in schema:
            schema[name].validate(name, value)
        # _SaverDict.__setitem__ calls _save_tree() → Attribute.value = self
        # → _apply_classified_value + _mark_attr_dirty.  No explicit save needed.
        self._data()[name] = value

    def __delattr__(self, name):
        data = self._data()
        if data is not None and name in data:
            del data[name]

    # ------------------------------------------------------------------
    # Dict-like interface
    # ------------------------------------------------------------------

    def __contains__(self, name):
        data = self._data()
        return data is not None and name in data

    def __repr__(self):
        key = object.__getattribute__(self, "_key")
        return f"<AttributeBag '{key}': {self._data()!r}>"

    def get(self, name, default=None):
        """Return the sub-key value, or *default* if absent."""
        try:
            return self.__getattr__(name)
        except AttributeError:
            return default

    def keys(self):
        """Return bag sub-keys (dict_keys view)."""
        data = self._data()
        return data.keys() if data is not None else {}.keys()

    def values(self):
        """Return bag values (dict_values view)."""
        data = self._data()
        return data.values() if data is not None else {}.values()

    def items(self):
        """Return bag (key, value) pairs (dict_items view)."""
        data = self._data()
        return data.items() if data is not None else {}.items()

    def update(self, mapping):
        """Update multiple sub-keys at once, validating each against schema."""
        schema = object.__getattribute__(self, "_schema")
        for fname, fval in mapping.items():
            if fname in schema:
                schema[fname].validate(fname, fval)
        self._data().update(mapping)


# ---------------------------------------------------------------------------
# TypedAttr
# ---------------------------------------------------------------------------


class TypedAttr:
    """
    Descriptor for declared attribute state on a TypedObject.

    See module docstring for full usage. Mirrors AttributeProperty's public
    API (``at_get`` / ``at_set`` hooks, ``autocreate``, ``lockstring``,
    ``strattr``) so it can replace it without changing call sites.

    Args:
        type_ (type, optional): Expected Python type for validation. For
            ``backend="bag"`` this must be omitted; use ``keys=`` instead.
        default: Default value.  If callable, called with no arguments each
            time a fresh default is needed.  Omit for no default (None returned
            on miss).
        backend (str): ``"blob"`` | ``"typed_col"`` | ``"bag"``.
        category (str, optional): Attribute category (None = default category).
        keys (dict, optional): For ``backend="bag"``: maps sub-key names to
            AttrField instances (or plain default values).
        nullable (bool): Allow None values. Default True.
        min: Minimum value (``blob`` / ``typed_col`` only).
        max: Maximum value (``blob`` / ``typed_col`` only).
        choices (iterable, optional): Exhaustive set of valid values.
        autocreate (bool): Create the Attribute row on first access if it does
            not exist. Default True. Set False for lazy / optional attrs.
        lockstring (str): Lock string passed through to AttributeHandler.add.
        strattr (bool): Store as strvalue (string-only fast path, same as
            AttributeProperty.strattr).
    """

    _attrhandler_name = "attributes"
    _cached_default_template = "_typedattr_default_{key}"

    def __init__(
        self,
        type_=None,
        *,
        default=_UNSET,
        backend="blob",
        category=None,
        keys=None,
        nullable=True,
        min=None,
        max=None,
        choices=None,
        autocreate=True,
        lockstring="",
        strattr=False,
    ):
        if backend not in ("blob", "typed_col", "bag"):
            raise ValueError(
                f"TypedAttr backend must be 'blob', 'typed_col', or 'bag'; got {backend!r}."
            )
        if backend == "typed_col" and type_ not in _TYPED_COL_TYPES:
            raise TypeError(
                "TypedAttr backend='typed_col' requires a JSON-primitive type_ "
                f"({', '.join(t.__name__ for t in _TYPED_COL_TYPES if t is not type(None))},"
                " or None)."
            )
        if backend == "bag" and type_ is not None:
            raise TypeError(
                "TypedAttr backend='bag' does not accept type_; use keys= for sub-key schemas."
            )

        self.type_ = type_
        self.default = default
        self.backend = backend
        self.category = category
        # Normalise keys: bare values become AttrField(default=value)
        self.keys = {
            k: (v if isinstance(v, AttrField) else AttrField(default=v))
            for k, v in (keys or {}).items()
        }
        self.nullable = nullable
        self.min = min
        self.max = max
        self.choices = choices
        self.autocreate = autocreate
        if lockstring:
            raise ValueError("typed attributes no longer accept lockstrings")
        self.lockstring = ""
        self.strattr = strattr
        self.attr_key = ""  # populated by __set_name__

    # ------------------------------------------------------------------
    # Descriptor registration
    # ------------------------------------------------------------------

    def __set_name__(self, owner, name):
        self.attr_key = name
        # Each class in the hierarchy gets its own _typed_attrs dict so
        # subclasses don't pollute the parent's registry.
        if "_typed_attrs" not in owner.__dict__:
            owner._typed_attrs = {}
        owner._typed_attrs[name] = self

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, value):
        """
        Raise TypeError / ValueError if *value* violates declared constraints.
        Not called for ``backend="bag"`` (per-key validation is on AttrField).
        """
        if value is None:
            if not self.nullable:
                raise ValueError(f"TypedAttr '{self.attr_key}' is not nullable.")
            return
        if self.type_ is not None and not isinstance(value, self.type_):
            raise TypeError(
                f"TypedAttr '{self.attr_key}' expected {self.type_.__name__}, "
                f"got {type(value).__name__}."
            )
        if self.min is not None and value < self.min:
            raise ValueError(
                f"TypedAttr '{self.attr_key}' value {value!r} is below minimum {self.min}."
            )
        if self.max is not None and value > self.max:
            raise ValueError(
                f"TypedAttr '{self.attr_key}' value {value!r} exceeds maximum {self.max}."
            )
        if self.choices is not None and value not in self.choices:
            raise ValueError(
                f"TypedAttr '{self.attr_key}' value {value!r} is not in choices {self.choices!r}."
            )

    # ------------------------------------------------------------------
    # Default handling (mirrors AttributeProperty._get_and_cache_default)
    # ------------------------------------------------------------------

    def _get_and_cache_default(self, instance):
        """
        Return the default value for *instance*, wrapped through from_pickle
        so mutable containers (_SaverDict etc.) track writes correctly.

        The wrapped default is cached on the AttributeHandler instance (same
        namespace as AttributeProperty uses) so repeated accesses don't
        create new wrappers.
        """
        handler = getattr(instance, self._attrhandler_name)
        cache_key = self._cached_default_template.format(key=self.attr_key)
        cached = getattr(handler, cache_key, None)
        if cached is None and self.default is not _UNSET:
            val = self.default() if callable(self.default) else copy(self.default)
            val = from_pickle(val, db_obj=None)
            setattr(handler, cache_key, val)
            cached = val
        return cached

    # ------------------------------------------------------------------
    # Extension hooks (matches AttributeProperty API)
    # ------------------------------------------------------------------

    def at_get(self, value, obj):
        """Called with the retrieved value before returning it. Override to transform."""
        return value

    def at_set(self, value, obj):
        """Called with the value before storing it. Override to transform or validate."""
        return value

    # ------------------------------------------------------------------
    # Descriptor protocol
    # ------------------------------------------------------------------

    def __get__(self, instance, owner):
        if instance is None:
            # Class-level access: return the descriptor itself so callers
            # can use TypedAttr.filter_kwargs() etc.
            return self

        if self.backend == "bag":
            return AttributeBag(
                instance,
                self.attr_key,
                category=self.category,
                schema=self.keys,
            )

        # blob / typed_col: delegate to AttributeHandler
        default = self._get_and_cache_default(instance)
        handler = getattr(instance, self._attrhandler_name)

        try:
            val = handler.get(
                key=self.attr_key,
                default=default,
                category=self.category,
                strattr=self.strattr,
                raise_exception=self.autocreate,
            )
        except AttributeError:
            # Attribute didn't exist and autocreate=True: create it now.
            if self.autocreate:
                self.__set__(instance, default)
                val = default
            else:
                raise

        return self.at_get(val, instance)

    def __set__(self, instance, value):
        if self.backend == "bag":
            if not isinstance(value, dict):
                raise TypeError(
                    f"TypedAttr '{self.attr_key}' (bag) requires a dict; "
                    f"got {type(value).__name__}."
                )
            for fname, fval in value.items():
                if fname in self.keys:
                    self.keys[fname].validate(fname, fval)
            getattr(instance, self._attrhandler_name).add(
                self.attr_key, value, category=self.category
            )
            return

        value = self.at_set(value, instance)
        self._validate(value)
        getattr(instance, self._attrhandler_name).add(
            self.attr_key,
            value,
            category=self.category,
            lockstring=self.lockstring,
            strattr=self.strattr,
        )

    def __delete__(self, instance):
        getattr(instance, self._attrhandler_name).remove(key=self.attr_key, category=self.category)

    # ------------------------------------------------------------------
    # Query helper
    # ------------------------------------------------------------------

    def filter_kwargs(self, value, *, prefix="db_attributes__"):
        """Not usable with the JSONB attribute backend.

        The legacy ``db_attributes__`` ORM lookup targets the M2M attribute
        table, which is no longer written when ``JsonbAttributeBackend`` is
        active.  Use ``world.db_utils.attrs_match()`` / ``attrs_exists()`` /
        ``db_attrs__contains={...}`` for JSONB-backed queries instead.
        """
        raise NotImplementedError(
            "TypedAttr.filter_kwargs targets the legacy db_attributes M2M table, "
            "which is orphaned when the JSONB backend is active. "
            "Use world.db_utils.attrs_match() or db_attrs__contains for queries."
        )


# ---------------------------------------------------------------------------
# Schema migration primitives
# ---------------------------------------------------------------------------


class RenameAttr:
    """
    Schema migration op: rename an attribute key.

    Usage::

        class Character(DefaultCharacter):
            _attr_schema_version = 2
            _attr_migrations = {
                2: [RenameAttr("hit_points", "hp")],
            }
    """

    def __init__(self, old_key, new_key, category=None):
        self.old_key = old_key
        self.new_key = new_key
        self.category = category

    def apply(self, obj):
        val = obj.attributes.get(self.old_key, category=self.category)
        if val is not None:
            obj.attributes.add(self.new_key, val, category=self.category)
            obj.attributes.remove(self.old_key, category=self.category)

    def __repr__(self):
        return f"RenameAttr({self.old_key!r} → {self.new_key!r}, category={self.category!r})"


class TransformAttr:
    """
    Schema migration op: transform an attribute's value in-place.

    Usage::

        class Character(DefaultCharacter):
            _attr_schema_version = 3
            _attr_migrations = {
                3: [TransformAttr("score", lambda v: v * 10)],
            }
    """

    def __init__(self, key, fn, category=None):
        self.key = key
        self.fn = fn
        self.category = category

    def apply(self, obj):
        val = obj.attributes.get(self.key, category=self.category)
        if val is not None:
            obj.attributes.add(self.key, self.fn(val), category=self.category)

    def __repr__(self):
        return f"TransformAttr({self.key!r}, category={self.category!r})"


class DropAttr:
    """
    Schema migration op: remove a stale attribute key.

    Usage::

        class Character(DefaultCharacter):
            _attr_schema_version = 4
            _attr_migrations = {
                4: [DropAttr("legacy_field")],
            }
    """

    def __init__(self, key, category=None):
        self.key = key
        self.category = category

    def apply(self, obj):
        obj.attributes.remove(self.key, category=self.category)

    def __repr__(self):
        return f"DropAttr({self.key!r}, category={self.category!r})"


# ---------------------------------------------------------------------------
# Schema version runner
# ---------------------------------------------------------------------------


def apply_schema_migrations(obj):
    """
    Apply any pending attribute schema migrations to *obj*.

    Reads ``_attr_schema_version`` (int) and ``_attr_migrations`` (dict
    mapping version int → list of migration ops) from the typeclass.

    If the stored version stamp on the object is behind the declared
    version, each outstanding migration is applied in version order. The
    version stamp is updated after all ops for that version run (even if
    some ops failed — failures are logged and skipped so a broken migration
    does not loop on every load).

    Called automatically from ``TypedObject.at_post_load()``. Typeclasses
    only need to declare ``_attr_schema_version`` and ``_attr_migrations``::

        class Character(DefaultCharacter):
            _attr_schema_version = 2
            _attr_migrations = {
                2: [RenameAttr("hit_points", "hp")],
            }

    Args:
        obj (TypedObject): The object to migrate.
    """
    declared = getattr(obj.__class__, "_attr_schema_version", None)
    if declared is None:
        return

    migrations = getattr(obj.__class__, "_attr_migrations", {})
    stored = obj.attributes.get(_SCHEMA_VERSION_KEY, category=_SCHEMA_VERSION_CATEGORY) or 0

    if stored >= declared:
        return

    from evennia.utils import logger

    for version in range(stored + 1, declared + 1):
        for op in migrations.get(version, []):
            try:
                op.apply(obj)
            except Exception:
                logger.log_trace(
                    f"Schema migration v{version} {op!r} failed on {obj!r} — skipping."
                )

    obj.attributes.add(_SCHEMA_VERSION_KEY, declared, category=_SCHEMA_VERSION_CATEGORY)
