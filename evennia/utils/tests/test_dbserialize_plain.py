"""
Tests for the walks of ``to_pickle`` and ``from_pickle`` over the plain values in a structure.

A row of a few dozen strings and numbers is most of what an Attribute holds, and each of them cost two calls on its way through
(one for the key, one for the value). A list or a dict now passes a plain value over with one check. The result has to be the
one the walks gave before, so these tests keep the old walks as they were and hold the new ones to them over made-up structures
of every kind the serializer knows, and they check the saver containers that ``from_pickle`` builds link up as they always did.
"""

import random
import time
from collections import OrderedDict, defaultdict, deque
from collections.abc import Mapping
from enum import IntFlag

from django.test import SimpleTestCase
from django.utils.safestring import SafeString

from evennia.utils import dbserialize
from evennia.utils.dbserialize import (
    _SaverDefaultDict,
    _SaverDeque,
    _SaverDict,
    _SaverList,
    _SaverMutable,
    _SaverOrderedDict,
    _SaverSet,
    from_pickle,
    to_pickle,
)
from evennia.utils.test_resources import BaseEvenniaTest

#: The test is built to run against the walks as they were; if a walk is changed again, these are the answers to keep.
_PLAIN = (str, int, float, bool, bytes, SafeString)


def old_to_pickle(data):
    """``to_pickle`` before a plain value was passed over with one check, for the kinds of value the tests make."""

    def process_item(item):
        dtype = type(item)
        if dtype in _PLAIN:
            return item
        elif isinstance(item, IntFlag):
            return item.value
        elif dtype == tuple:
            return tuple(process_item(val) for val in item)
        elif dtype in (list, _SaverList):
            return [process_item(val) for val in item]
        elif dtype in (dict, _SaverDict):
            return dict((process_item(key), process_item(val)) for key, val in item.items())
        elif dtype in (defaultdict, _SaverDefaultDict):
            return defaultdict(
                item.default_factory,
                ((process_item(key), process_item(val)) for key, val in item.items()),
            )
        elif dtype in (deque, _SaverDeque):
            return deque((process_item(val) for val in item), maxlen=item.maxlen)
        elif dtype in (set, _SaverSet):
            return set(process_item(val) for val in item)
        elif dtype in (OrderedDict, _SaverOrderedDict):
            return OrderedDict((process_item(key), process_item(val)) for key, val in item.items())
        if hasattr(item, "__iter__"):
            try:
                try:
                    return item.__class__(
                        (process_item(key), process_item(val)) for key, val in item.items()
                    )
                except (AttributeError, TypeError):
                    return {process_item(key): process_item(val) for key, val in item.items()}
            except Exception:
                try:
                    return item.__class__([process_item(val) for val in item])
                except (AttributeError, TypeError):
                    return [process_item(val) for val in item]
        return item

    return process_item(data)


def old_from_pickle(data, db_obj=None):
    """``from_pickle`` before a plain value was passed over with one check, for the kinds of value the tests make."""

    def process_item(item):
        dtype = type(item)
        if dtype in _PLAIN:
            return item
        elif dbserialize._IS_PACKED_DBOBJ(item):
            return dbserialize.unpack_dbobj(item)
        elif dtype == tuple:
            return tuple(process_item(val) for val in item)
        elif dtype == dict:
            return dict((process_item(key), process_item(val)) for key, val in item.items())
        elif dtype == defaultdict:
            return defaultdict(
                item.default_factory,
                ((process_item(key), process_item(val)) for key, val in item.items()),
            )
        elif dtype == set:
            return set(process_item(val) for val in item)
        elif dtype == OrderedDict:
            return OrderedDict((process_item(key), process_item(val)) for key, val in item.items())
        elif dtype == deque:
            return deque((process_item(val) for val in item), maxlen=item.maxlen)
        elif hasattr(item, "__iter__"):
            try:
                try:
                    return item.__class__(
                        (process_item(key), process_item(val)) for key, val in item.items()
                    )
                except (AttributeError, TypeError):
                    return {process_item(key): process_item(val) for key, val in item.items()}
            except Exception:
                try:
                    return item.__class__(process_item(val) for val in item)
                except (AttributeError, TypeError):
                    return [process_item(val) for val in item]
        return item

    def process_tree(item, parent):
        dtype = type(item)
        if dtype in _PLAIN:
            return item
        elif dbserialize._IS_PACKED_DBOBJ(item):
            return dbserialize.unpack_dbobj(item)
        elif dtype == tuple:
            return tuple(process_tree(val, item) for val in item)
        elif dtype == list:
            dat = _SaverList(_parent=parent)
            dat._data.extend(process_tree(val, dat) for val in item)
            return dat
        elif dtype == dict:
            dat = _SaverDict(_parent=parent)
            dat._data.update(
                (process_item(key), process_tree(val, dat)) for key, val in item.items()
            )
            return dat
        elif dtype == defaultdict:
            dat = _SaverDefaultDict(item.default_factory, _parent=parent)
            dat._data.update(
                (process_item(key), process_tree(val, dat)) for key, val in item.items()
            )
            return dat
        elif dtype == set:
            dat = _SaverSet(_parent=parent)
            dat._data.update(set(process_tree(val, dat) for val in item))
            return dat
        elif dtype == OrderedDict:
            dat = _SaverOrderedDict(_parent=parent)
            dat._data.update(
                (process_item(key), process_tree(val, dat)) for key, val in item.items()
            )
            return dat
        elif dtype == deque:
            dat = _SaverDeque(_parent=parent, maxlen=item.maxlen)
            dat._data.extend(process_item(val) for val in item)
            return dat
        elif hasattr(item, "__iter__"):
            try:
                try:
                    dat = _SaverDict(_parent=parent, _class=item.__class__)
                    dat._data.update(
                        (process_item(key), process_tree(val, dat)) for key, val in item.items()
                    )
                    return dat
                except (AttributeError, TypeError):
                    dat = _SaverDict(_parent=parent)
                    dat._data.update(
                        (process_item(key), process_tree(val, dat)) for key, val in item.items()
                    )
                    return dat
            except Exception:
                try:
                    dat = _SaverList(_parent=parent, _class=item.__class__)
                    dat._data.extend(process_tree(val, dat) for val in item)
                    return dat
                except (AttributeError, TypeError):
                    dat = _SaverList(_parent=parent)
                    dat._data.extend(process_tree(val, dat) for val in item)
                    return dat
        return item

    if not db_obj:
        return process_item(data)
    dtype = type(data)
    if dtype in _PLAIN + (tuple,):
        return process_item(data)
    elif dtype == list:
        dat = _SaverList(_db_obj=db_obj)
        dat._data.extend(process_tree(val, dat) for val in data)
        return dat
    elif dtype == dict:
        dat = _SaverDict(_db_obj=db_obj)
        dat._data.update((process_item(key), process_tree(val, dat)) for key, val in data.items())
        return dat
    elif dtype == defaultdict:
        dat = _SaverDefaultDict(data.default_factory, _db_obj=db_obj)
        dat._data.update((process_item(key), process_tree(val, dat)) for key, val in data.items())
        return dat
    elif dtype == set:
        dat = _SaverSet(_db_obj=db_obj)
        dat._data.update(process_tree(val, dat) for val in data)
        return dat
    elif dtype == OrderedDict:
        dat = _SaverOrderedDict(_db_obj=db_obj)
        dat._data.update((process_item(key), process_tree(val, dat)) for key, val in data.items())
        return dat
    elif dtype == deque:
        dat = _SaverDeque(_db_obj=db_obj, maxlen=data.maxlen)
        dat._data.extend(process_item(val) for val in data)
        return dat
    return process_item(data)


def old_convert_mutables(owner, data):
    """``_SaverMutable._convert_mutables`` before a value that comes back as it went in was passed over with one check."""

    def process_tree(item, parent):
        dtype = type(item)
        if dtype in (str, int, float, bool, tuple):
            return item
        elif dtype == list:
            dat = _SaverList(_parent=parent)
            dat._data.extend(process_tree(val, dat) for val in item)
            return dat
        elif dtype == dict:
            dat = _SaverDict(_parent=parent)
            dat._data.update((key, process_tree(val, dat)) for key, val in item.items())
            return dat
        elif dtype == defaultdict:
            dat = _SaverDefaultDict(item.default_factory, _parent=parent)
            dat._data.update((key, process_tree(val, dat)) for key, val in item.items())
            return dat
        elif dtype == deque:
            # The walk before the cycle fix built this saver and then fell through to return the plain deque, so a change to it
            # was not saved. It is filled and returned now, as ``_convert_tree`` does.
            dat = _SaverDeque(_parent=parent, maxlen=item.maxlen)
            dat._data.extend(process_tree(val, dat) for val in item)
            return dat
        elif dtype == set:
            dat = _SaverSet(_parent=parent)
            dat._data.update(process_tree(val, dat) for val in item)
            return dat
        return item

    return process_tree(data, owner)


class Flag(IntFlag):
    ONE = 1
    TWO = 2


class Pairs(Mapping):
    """A mapping that is not a dict, so the walks reach for the way they treat any other iterable."""

    def __init__(self, items=()):
        self._items = dict(items)

    def __getitem__(self, key):
        return self._items[key]

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


def made_up(rng, depth=0, hashable=False):
    """A random nested value of the kinds the serializer knows, with the plain ones most common, as in a row."""
    scalars = [
        lambda: None,
        lambda: rng.random() < 0.5,
        lambda: rng.randint(-5, 10**12),
        lambda: rng.random() * 100,
        lambda: "s%d" % rng.randint(0, 99),
        lambda: b"b%d" % rng.randint(0, 9),
        lambda: SafeString("safe%d" % rng.randint(0, 9)),
        lambda: Flag.ONE | Flag.TWO,
    ]
    if hashable or depth >= 4:
        if hashable and depth < 2 and rng.random() < 0.2:
            return tuple(made_up(rng, depth + 1, hashable=True) for _ in range(rng.randint(0, 3)))
        return rng.choice(scalars)()
    kind = rng.choices(
        [
            "scalar",
            "tuple",
            "list",
            "dict",
            "set",
            "ordered",
            "default",
            "deque",
            "pairs",
            "frozen",
        ],
        [8, 1, 4, 4, 1, 1, 1, 1, 1, 1],
    )[0]
    if kind == "scalar":
        return rng.choice(scalars)()
    if kind == "tuple":
        return tuple(made_up(rng, depth + 1) for _ in range(rng.randint(0, 3)))
    if kind == "list":
        return [made_up(rng, depth + 1) for _ in range(rng.randint(0, 6))]
    keys = lambda: rng.choice(  # noqa: E731
        ["k%d" % rng.randint(0, 20)] * 8 + [rng.randint(0, 5), None, ("a", rng.randint(0, 3))]
    )
    if kind == "dict":
        return {keys(): made_up(rng, depth + 1) for _ in range(rng.randint(0, 6))}
    if kind == "set":
        return {made_up(rng, depth + 1, hashable=True) for _ in range(rng.randint(0, 4))}
    if kind == "ordered":
        return OrderedDict((keys(), made_up(rng, depth + 1)) for _ in range(rng.randint(0, 4)))
    if kind == "default":
        out = defaultdict(list)
        for _ in range(rng.randint(0, 4)):
            out[keys()] = made_up(rng, depth + 1)
        return out
    if kind == "deque":
        return deque(
            (made_up(rng, depth + 1, hashable=True) for _ in range(rng.randint(0, 4))),
            maxlen=rng.choice([None, 10]),
        )
    if kind == "pairs":
        return Pairs((keys(), made_up(rng, depth + 1)) for _ in range(rng.randint(0, 4)))
    return frozenset(made_up(rng, depth + 1, hashable=True) for _ in range(rng.randint(0, 3)))


#: The kinds of value ``from_pickle`` turns into a saver container when it is the whole of what it is given.
ROOTS = (
    list,
    dict,
    defaultdict,
    set,
    OrderedDict,
    deque,
    tuple,
    str,
    int,
    float,
    bool,
    bytes,
    SafeString,
)


def rooted(value):
    """A value the reference builds a root for: one of the kinds above, or the value in a list."""
    return value if type(value) in ROOTS else [value]


class Owner:
    """What a saver writes back to; it has a ``pk`` and a ``value``."""

    pk = 1
    value = None


def describe(obj, parent=None, owner=None):
    """A value as a nested tuple that tells its type and content, and for a saver container whether it is linked as it should be."""
    if isinstance(obj, _SaverMutable):
        data = obj._data
        linked = (obj._parent is parent) and (obj._db_obj is owner)
        if isinstance(data, Mapping):
            body = tuple((describe(key), describe(value, obj, None)) for key, value in data.items())
        elif isinstance(data, (set, frozenset)):
            body = tuple(sorted(repr(describe(value, obj, None)) for value in data))
        else:
            body = tuple(describe(value, obj, None) for value in data)
        extra = getattr(data, "maxlen", None), getattr(data, "default_factory", None)
        return (type(obj).__name__, type(data).__name__, linked, extra, body)
    if isinstance(obj, (dict, OrderedDict, defaultdict)):
        return (
            type(obj).__name__,
            getattr(obj, "default_factory", None),
            tuple((describe(key), describe(value)) for key, value in obj.items()),
        )
    if isinstance(obj, (list, tuple, deque)):
        return (type(obj).__name__, getattr(obj, "maxlen", None), tuple(describe(v) for v in obj))
    if isinstance(obj, (set, frozenset)):
        return (type(obj).__name__, tuple(sorted(repr(describe(v)) for v in obj)))
    if isinstance(obj, Mapping):
        return (type(obj).__name__, tuple((describe(k), describe(v)) for k, v in obj.items()))
    return (type(obj).__name__, repr(obj))


class TestThePlainValuesAreWalkedAsTheyWere(SimpleTestCase):
    def test_to_pickle_gives_what_it_gave(self):
        rng = random.Random(20261025)
        for index in range(1500):
            value = made_up(rng)
            self.assertEqual(describe(to_pickle(value)), describe(old_to_pickle(value)), index)

    def test_to_pickle_of_a_saver_tree_gives_what_it_gave(self):
        rng = random.Random(20261026)
        for index in range(800):
            value = made_up(rng)
            saved = from_pickle(rooted(to_pickle(value)), db_obj=Owner())
            self.assertEqual(describe(to_pickle(saved)), describe(old_to_pickle(saved)), index)

    def test_from_pickle_gives_what_it_gave_with_no_owner(self):
        rng = random.Random(20261027)
        for index in range(1500):
            value = rooted(to_pickle(made_up(rng)))
            self.assertEqual(describe(from_pickle(value)), describe(old_from_pickle(value)), index)

    def test_from_pickle_builds_the_saver_tree_it_built(self):
        rng = random.Random(20261028)
        for index in range(1500):
            value = rooted(to_pickle(made_up(rng)))
            owner = Owner()
            new = from_pickle(value, db_obj=owner)
            old = old_from_pickle(value, db_obj=owner)
            self.assertEqual(describe(new, None, owner), describe(old, None, owner), index)

    def test_a_value_assigned_into_a_saver_is_converted_as_it_was(self):
        rng = random.Random(20261029)
        for index in range(1500):
            value = made_up(rng)
            holder = _SaverList(_db_obj=Owner())
            self.assertEqual(
                describe(holder._convert_mutables(value), holder),
                describe(old_convert_mutables(holder, value), holder),
                index,
            )

    def test_a_row_appended_to_a_saver_list_is_stored_and_writes_back_from_inside(self):
        class Writes:
            pk = 1
            seen = None

            @property
            def value(self):
                return self.seen

            @value.setter
            def value(self, new):
                self.seen = new

        owner = Writes()
        tree = from_pickle([], db_obj=owner)
        tree.append({"a": 1, "b": [1, {"c": 2}], "d": None})
        self.assertEqual(to_pickle(owner.seen), [{"a": 1, "b": [1, {"c": 2}], "d": None}])
        tree[0]["b"][1]["c"] = 3
        self.assertEqual(to_pickle(owner.seen), [{"a": 1, "b": [1, {"c": 3}], "d": None}])
        self.assertIs(tree[0]["b"][1]._parent, tree[0]["b"])

    def test_from_pickle_of_a_list_of_rows_links_every_container_to_its_parent(self):
        rows = [{"a": 1, "b": [1, 2, {"c": "d"}], "e": {"f": None}} for _ in range(3)]
        owner = Owner()
        tree = from_pickle(rows, db_obj=owner)
        self.assertIs(tree._db_obj, owner)
        self.assertIsNone(tree._parent)
        for row in tree:
            self.assertIs(row._parent, tree)
            self.assertIs(row["b"]._parent, row)
            self.assertIs(row["b"][2]._parent, row["b"])
            self.assertIs(row["e"]._parent, row)

    def test_an_edit_deep_in_the_tree_writes_back_to_the_owner(self):
        class Writes:
            pk = 1
            seen = None

            @property
            def value(self):
                return self.seen

            @value.setter
            def value(self, new):
                self.seen = new

        owner = Writes()
        tree = from_pickle([{"a": [1, {"b": 2}]}], db_obj=owner)
        tree[0]["a"][1]["b"] = 3
        self.assertIs(owner.seen, tree)
        self.assertEqual(to_pickle(owner.seen), [{"a": [1, {"b": 3}]}])

    def test_a_list_read_with_no_owner_is_a_plain_list(self):
        value = [1, "two", [3], {"four": (5, 6)}]
        read = from_pickle(value)
        self.assertEqual(read, value)
        self.assertIs(type(read), list)
        self.assertIs(type(read[2]), list)
        self.assertIs(type(read[3]), dict)
        self.assertIsNot(read, value)

    def test_a_flag_is_stored_as_its_value(self):
        self.assertEqual(to_pickle({"flags": [Flag.ONE | Flag.TWO]}), {"flags": [3]})
        self.assertIs(type(to_pickle([Flag.ONE])[0]), int)

    def test_a_row_is_walked_faster_than_it_was(self):
        rows = [
            {"field_%d" % n: (n if n % 3 else "text") for n in range(70)} | {"tags": ["a"], "x": {}}
            for _ in range(25)
        ]
        owner = Owner()

        def clock(call, *args):
            best = None
            for _ in range(5):
                started = time.perf_counter()
                for _ in range(10):
                    call(*args)
                elapsed = time.perf_counter() - started
                best = elapsed if best is None else min(best, elapsed)
            return best

        # Measured at about a fifth of the time; the test asks for less than three quarters so a busy machine cannot fail it.
        self.assertLess(clock(from_pickle, rows, owner), clock(old_from_pickle, rows, owner) * 0.75)
        self.assertLess(clock(to_pickle, rows), clock(old_to_pickle, rows) * 0.75)
        saved = from_pickle(rows, db_obj=owner)
        self.assertLess(clock(to_pickle, saved), clock(old_to_pickle, saved) * 0.75)


class TestADatabaseObjectInsideAStructure(BaseEvenniaTest):
    evennia_fixtures = {"obj1"}

    def test_it_is_packed_and_comes_back_wherever_it_sits(self):
        packed = to_pickle({"a": [self.obj1, {"b": self.obj1}], "c": (self.obj1,)})
        self.assertEqual(packed["a"][0][0], "__packed_dbobj__")
        read = from_pickle(packed, db_obj=Owner())
        self.assertEqual(read["a"][0], self.obj1)
        self.assertEqual(read["a"][1]["b"], self.obj1)
        self.assertEqual(read["c"][0], self.obj1)
        self.assertEqual(describe(from_pickle(packed)), describe(old_from_pickle(packed)))
