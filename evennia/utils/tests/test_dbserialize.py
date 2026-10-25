"""
Tests for dbserialize module
"""

import gc
import types
from collections import defaultdict, deque
from enum import IntFlag, auto
from unittest import mock

from django.test import TestCase
from parameterized import parameterized

from evennia.objects.objects import DefaultObject
from evennia.utils import dbserialize


class TestDbSerialize(TestCase):
    """
    Database serialization operations.
    """

    def setUp(self):
        self.obj = DefaultObject(db_key="Tester")
        self.obj.save()

    def test_intflag(self):
        class TestFlag(IntFlag):
            foo = auto()

        self.obj.db.test = TestFlag.foo
        self.assertEqual(self.obj.db.test, TestFlag.foo)
        self.obj.save()

    def test_constants(self):
        self.obj.db.test = 1
        self.obj.db.test += 1
        self.assertEqual(self.obj.db.test, 2)
        self.obj.db.test -= 3
        self.assertEqual(self.obj.db.test, -1)
        self.obj.db.test *= -2
        self.assertEqual(self.obj.db.test, 2)
        self.obj.db.test /= 2
        self.assertEqual(self.obj.db.test, 1)

    def test_saverlist(self):
        self.obj.db.test = [1, 2, 3]
        self.assertEqual(self.obj.db.test, [1, 2, 3])
        self.obj.db.test.append("4")
        self.assertEqual(self.obj.db.test, [1, 2, 3, "4"])
        self.obj.db.test.insert(1, 1.5)
        self.assertEqual(self.obj.db.test, [1, 1.5, 2, 3, "4"])
        self.obj.db.test.pop()
        self.assertEqual(self.obj.db.test, [1, 1.5, 2, 3])
        self.obj.db.test.pop(0)
        self.assertEqual(self.obj.db.test, [1.5, 2, 3])
        self.obj.db.test.reverse()
        self.assertEqual(self.obj.db.test, [3, 2, 1.5])

    def test_saverlist__sort(self):
        self.obj.db.test = [3, 2, 1.5]
        self.obj.db.test.sort()
        self.assertEqual(self.obj.db.test, [1.5, 2, 3])
        self.obj.db.test.extend([0, 4, 5])
        self.assertEqual(self.obj.db.test, [1.5, 2, 3, 0, 4, 5])
        self.obj.db.test.sort()
        self.assertEqual(self.obj.db.test, [0, 1.5, 2, 3, 4, 5])
        self.obj.db.test = [[4, 5, 6], [1, 2, 3]]
        self.assertEqual(self.obj.db.test, [[4, 5, 6], [1, 2, 3]])
        self.obj.db.test.sort()
        self.assertEqual(self.obj.db.test, [[1, 2, 3], [4, 5, 6]])
        self.obj.db.test = [{1: 0}, {0: 1}]
        self.assertEqual(self.obj.db.test, [{1: 0}, {0: 1}])
        self.obj.db.test.sort(key=lambda d: str(d))
        self.assertEqual(self.obj.db.test, [{0: 1}, {1: 0}])

    def test_saverdict(self):
        self.obj.db.test = {"a": True}
        self.obj.db.test.update({"b": False})
        self.assertEqual(self.obj.db.test, {"a": True, "b": False})
        self.obj.db.test |= {"c": 5}
        self.assertEqual(self.obj.db.test, {"a": True, "b": False, "c": 5})

    def test_saverdict__reverse_or_keeps_right_operand_precedence(self):
        """Reverse dict union gives the SaverDict operand precedence."""
        self.obj.db.test = {"shared": "attribute", "attribute": True}

        result = {"shared": "mapping", "mapping": True} | self.obj.db.test

        self.assertEqual(result, {"shared": "attribute", "mapping": True, "attribute": True})

    def test_saverdict__update_converts_nested_mutables(self):
        """Nested mutables added by update persist their later in-place changes."""
        self.obj.db.test = {}
        self.obj.db.test.update({"nested": []})
        self.obj.db.test["nested"].append("saved")

        self.obj.attributes.reset_cache()

        self.assertEqual(self.obj.db.test, {"nested": ["saved"]})

    @parameterized.expand(
        [
            ("list", list, dbserialize._SaverList, [1, 2, 3]),
            ("dict", dict, dbserialize._SaverDict, {"key": "value"}),
            ("set", set, dbserialize._SaverSet, {1, 2, 3}),
            ("deque", deque, dbserialize._SaverDeque, deque(("a", "b", "c"))),
            (
                "OrderedDict",
                dbserialize.OrderedDict,
                dbserialize._SaverOrderedDict,
                dbserialize.OrderedDict([("a", 1), ("b", 2), ("c", 3)]),
            ),
        ]
    )
    def test_deserialize(self, _, base_type, saver_type, default_value):
        self.assertIsInstance(default_value, base_type)
        self.obj.db.test = default_value
        for value in (dbserialize.deserialize(self.obj.db.test), self.obj.db.test.deserialize()):
            self.assertIsInstance(value, base_type)
            self.assertNotIsInstance(value, saver_type)
            self.assertEqual(value, default_value)
        self.obj.db.test = {"a": True}
        self.obj.db.test.update({"b": False})
        self.assertEqual(self.obj.db.test, {"a": True, "b": False})

    def test_defaultdict(self):
        # baseline behavior for a defaultdict
        _dd = defaultdict(list)
        _dd["a"]
        self.assertEqual(_dd, {"a": []})

        # behavior after defaultdict is set as attribute

        dd = defaultdict(list)
        self.obj.db.test = dd
        self.obj.db.test["a"]
        self.assertEqual(self.obj.db.test, {"a": []})

        self.obj.db.test["a"].append(1)
        self.assertEqual(self.obj.db.test, {"a": [1]})
        self.obj.db.test["a"].append(2)
        self.assertEqual(self.obj.db.test, {"a": [1, 2]})
        self.obj.db.test["a"].append(3)
        self.assertEqual(self.obj.db.test, {"a": [1, 2, 3]})
        self.obj.db.test |= {"b": [5, 6]}
        self.assertEqual(self.obj.db.test, {"a": [1, 2, 3], "b": [5, 6]})

    def test_deque_with_maxlen(self):
        _dd = deque((), maxlen=1)
        _dd.append(1)
        _dd.append(2)
        self.assertEqual(list(_dd), [2])

        dd = deque((), maxlen=1)
        self.obj.db.test = dd
        self.obj.db.test.append(1)
        self.obj.db.test.append(2)
        self.assertEqual(list(self.obj.db.test), [2])


class _InvalidContainer:
    """Container not saveable in Attribute (if obj is dbobj, it 'hides' it)"""

    def __init__(self, obj):
        self.hidden_obj = obj


class _ValidContainer(_InvalidContainer):
    """Container possible to save in Attribute (handles hidden dbobj explicitly)"""

    def __serialize_dbobjs__(self):
        self.hidden_obj = dbserialize.dbserialize(self.hidden_obj)

    def __deserialize_dbobjs__(self):
        self.hidden_obj = dbserialize.dbunserialize(self.hidden_obj)


class DbObjWrappers(TestCase):
    """
    Test the `__serialize_dbobjs__` and `__deserialize_dbobjs__` methods.

    """

    def setUp(self):
        super().setUp()
        self.dbobj1 = DefaultObject(db_key="Tester1")
        self.dbobj1.save()
        self.dbobj2 = DefaultObject(db_key="Tester2")
        self.dbobj2.save()

    def test_dbobj_hidden_obj__fail(self):
        with self.assertRaises(TypeError):
            self.dbobj1.db.testarg = _InvalidContainer(self.dbobj1)

    def test_consecutive_fetch(self):
        con = _ValidContainer(self.dbobj2)
        self.dbobj1.db.testarg = con
        attrobj = self.dbobj1.attributes.get("testarg", return_obj=True)

        # Identity with the assigned object is not preserved across the
        # write-behind store (the value is round-tripped, not the live object
        # cached). What matters: repeated reads return a stable object and the
        # hidden dbobj round-trips correctly.
        self.assertIs(attrobj.value, attrobj.value)
        self.assertEqual(attrobj.value.hidden_obj, self.dbobj2)

    def test_dbobj_hidden_obj__success(self):
        con = _ValidContainer(self.dbobj2)
        self.dbobj1.db.testarg = con

        # accessing the same data multiple times
        res1 = self.dbobj1.db.testarg
        res2 = self.dbobj1.db.testarg
        res3 = self.dbobj1.db.testarg

        # Repeated reads return a stable object (identity with the assigned
        # object is not guaranteed across the write-behind store).
        self.assertIs(res1, res2)
        self.assertIs(res1, res3)
        self.assertEqual(res1.hidden_obj, self.dbobj2)
        self.assertEqual(res2.hidden_obj, self.dbobj2)
        self.assertEqual(res3.hidden_obj, self.dbobj2)

    def test_dbobj_hidden_dict(self):
        con1 = _ValidContainer(self.dbobj2)
        con2 = _ValidContainer(self.dbobj2)

        self.dbobj1.db.dict = {}

        self.dbobj1.db.dict["key1"] = con1
        self.dbobj1.db.dict["key2"] = con2

        self.assertEqual(self.dbobj1.db.dict["key1"].hidden_obj, self.dbobj2)
        self.assertEqual(self.dbobj1.db.dict["key1"].hidden_obj, self.dbobj2)
        self.assertEqual(self.dbobj1.db.dict["key2"].hidden_obj, self.dbobj2)
        self.assertEqual(self.dbobj1.db.dict["key2"].hidden_obj, self.dbobj2)

    def test_dbobj_hidden_defaultdict(self):
        con1 = _ValidContainer(self.dbobj2)
        con2 = _ValidContainer(self.dbobj2)

        self.dbobj1.db.dfdict = defaultdict(dict)

        self.dbobj1.db.dfdict["key"]["con1"] = con1
        self.dbobj1.db.dfdict["key"]["con2"] = con2

        self.assertEqual(self.dbobj1.db.dfdict["key"]["con1"].hidden_obj, self.dbobj2)

        self.assertEqual(self.dbobj1.db.dfdict["key"]["con1"].hidden_obj, self.dbobj2)
        self.assertEqual(self.dbobj1.db.dfdict["key"]["con2"].hidden_obj, self.dbobj2)
        self.assertEqual(self.dbobj1.db.dfdict["key"]["con2"].hidden_obj, self.dbobj2)


def _closure_garbage(work, rounds=25):
    """
    Run ``work`` several times and return the functions and cells the cycle collector had to free.

    A helper defined inside a function that calls itself holds a reference to itself,
    so each call leaves a function and a cell for the collector. Only those are counted:
    a nested list and the list that holds it also refer to each other, and that is meant.
    """
    work()
    gc.collect()
    gc.set_debug(gc.DEBUG_SAVEALL)
    try:
        gc.garbage.clear()
        for _ in range(rounds):
            work()
        gc.collect()
        found = list(gc.garbage)
    finally:
        gc.set_debug(0)
        gc.garbage.clear()
    return [obj for obj in found if isinstance(obj, (types.FunctionType, types.CellType))]


_NESTED = {"a": [1, 2, {"b": (3, 4)}], "c": {"d": {"e": 5}}, "f": {6, 7}}


class TestNoLeftoverClosures(TestCase):
    """Converting a value to or from its stored form leaves nothing for the collector to free."""

    def test_to_pickle(self):
        self.assertEqual(_closure_garbage(lambda: dbserialize.to_pickle(_NESTED)), [])

    def test_from_pickle_without_a_saver(self):
        self.assertEqual(_closure_garbage(lambda: dbserialize.from_pickle(_NESTED)), [])

    def test_from_pickle_into_savers(self):
        db_obj = mock.Mock()
        self.assertEqual(_closure_garbage(lambda: dbserialize.from_pickle(_NESTED, db_obj)), [])

    def test_deserialize(self):
        saver = dbserialize.from_pickle(_NESTED, mock.Mock())
        self.assertEqual(_closure_garbage(lambda: dbserialize.deserialize(saver)), [])

    def test_assigning_a_nested_value_into_a_saver(self):
        saver = dbserialize.from_pickle({"x": 1}, mock.Mock())

        def assign():
            saver["y"] = {"p": [1, 2, {"q": 3}]}

        self.assertEqual(_closure_garbage(assign), [])

    def test_results_are_unchanged(self):
        packed = dbserialize.to_pickle(_NESTED)
        self.assertEqual(packed, _NESTED)
        saver = dbserialize.from_pickle(packed, mock.Mock())
        self.assertEqual(dbserialize.deserialize(saver), _NESTED)
        self.assertIsInstance(saver["a"], dbserialize._SaverList)
        self.assertIsInstance(saver["a"][2], dbserialize._SaverDict)
        self.assertIsInstance(saver["c"]["d"], dbserialize._SaverDict)


class TestAssignedDeque(TestCase):
    """A deque put into a stored dict becomes a saver, so changing it is saved."""

    def test_assigned_deque_is_converted_and_keeps_its_limit(self):
        db_obj = mock.Mock()
        saver = dbserialize.from_pickle({"x": 1}, db_obj)
        saver["d"] = deque([1, 2], maxlen=3)
        self.assertIsInstance(saver["d"], dbserialize._SaverDeque)
        self.assertEqual(list(saver["d"]), [1, 2])
        self.assertEqual(saver["d"].maxlen, 3)

    def test_changing_the_assigned_deque_saves(self):
        db_obj = mock.Mock()
        saver = dbserialize.from_pickle({"x": 1}, db_obj)
        saver["d"] = deque([1], maxlen=3)
        db_obj.reset_mock()
        saver["d"].append(2)
        self.assertEqual(list(saver["d"]), [1, 2])
        self.assertIs(db_obj.value, saver)
