"""
Unit tests for the prototypes and spawner

"""

import uuid
from random import randint, sample
from time import time

import mock
from anything import Something
from django.test.utils import override_settings

from evennia.commands.default import building
from evennia.objects.models import ObjectDB
from evennia.prototypes import protfuncs as protofuncs
from evennia.prototypes import prototypes as protlib
from evennia.prototypes import spawner
from evennia.prototypes.prototypes import _PROTOTYPE_TAG_META_CATEGORY
from evennia.utils import utils
from evennia.utils.create import create_object
from evennia.utils.test_resources import BaseEvenniaTest, EvenniaCommandTest

_PROTPARENTS = {
    "NOBODY": {},
    "GOBLIN": {
        "prototype_key": "GOBLIN",
        "typeclass": "evennia.objects.object.DefaultObject",
        "key": "goblin grunt",
        "health": lambda: randint(1, 1),
        "resists": ["cold", "poison"],
        "attacks": ["fists"],
        "weaknesses": ["fire", "light"],
    },
    "GOBLIN_WIZARD": {
        "prototype_parent": "GOBLIN",
        "key": "goblin wizard",
        "spells": ["fire ball", "lighting bolt"],
    },
    "GOBLIN_ARCHER": {
        "prototype_parent": "GOBLIN",
        "key": "goblin archer",
        "attacks": ["short bow"],
    },
    "ARCHWIZARD": {"prototype_parent": "GOBLIN", "attacks": ["archwizard staff"]},
    "GOBLIN_ARCHWIZARD": {
        "key": "goblin archwizard",
        "prototype_parent": ("GOBLIN_WIZARD", "ARCHWIZARD"),
    },
    "ISSUE2908": {
        "typeclass": "evennia.objects.object.DefaultObject",
        "key": "testobject_isse2909",
        "location": "$choice($objlist(",
    },
}


class TestSpawner(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        self.prot1 = {
            "prototype_key": "testprototype",
            "typeclass": "evennia.objects.object.DefaultObject",
        }

    def test_spawn_from_prot(self):
        obj1 = spawner.spawn(self.prot1)
        # check spawned objects have the right tag
        self.assertEqual(list(protlib.search_objects_with_prototype("testprototype")), obj1)
        self.assertEqual(
            [
                o.key
                for o in spawner.spawn(
                    _PROTPARENTS["GOBLIN"],
                    _PROTPARENTS["GOBLIN_ARCHWIZARD"],
                    prototype_parents=_PROTPARENTS,
                )
            ],
            ["goblin grunt", "goblin archwizard"],
        )

    def test_spawn_from_str(self):
        protlib.save_prototype(self.prot1)
        obj1 = spawner.spawn(self.prot1["prototype_key"])
        self.assertEqual(list(protlib.search_objects_with_prototype("testprototype")), obj1)
        self.assertEqual(
            [
                o.key
                for o in spawner.spawn(
                    _PROTPARENTS["GOBLIN"],
                    _PROTPARENTS["GOBLIN_ARCHWIZARD"],
                    prototype_parents=_PROTPARENTS,
                )
            ],
            ["goblin grunt", "goblin archwizard"],
        )


class TestUtils(BaseEvenniaTest):
    def test_prototype_from_object(self):
        self.maxDiff = None
        self.obj1.attributes.add("test", "testval")
        self.obj1.tags.add("foo")
        new_prot = spawner.prototype_from_object(self.obj1)
        self.assertEqual(
            {
                "attrs": [("test", "testval", None, "")],
                "home": Something,
                "key": "Obj",
                "location": Something,
                "locks": ";".join(
                    [
                        "call:true()",
                        "control:perm(Developer)",
                        "delete:perm(Admin)",
                        "drop:holds()",
                        "edit:perm(Admin)",
                        "examine:perm(Builder)",
                        "get:all()",
                        "puppet:pperm(Developer)",
                        "teleport:true()",
                        "teleport_here:true()",
                        "tell:perm(Admin)",
                        "view:all()",
                    ]
                ),
                "prototype_desc": "Built from Obj",
                "prototype_key": Something,
                "prototype_locks": "spawn:all();edit:all()",
                "prototype_tags": [],
                "tags": [("foo", None, None)],
                "typeclass": "evennia.objects.object.DefaultObject",
            },
            new_prot,
        )

    def test_update_objects_from_prototypes(self):
        self.maxDiff = None
        self.obj1.attributes.add("oldtest", "to_keep")

        old_prot = spawner.prototype_from_object(self.obj1)

        # modify object away from prototype
        self.obj1.attributes.add("test", "testval")
        self.obj1.attributes.add("desc", "changed desc")
        self.obj1.aliases.add("foo")
        self.obj1.tags.add("footag", "foocategory")

        # modify prototype
        old_prot["new"] = "new_val"
        old_prot["test"] = "testval_changed"
        old_prot["permissions"] = ["Builder"]
        # this will not update, since we don't update the prototype on-disk
        old_prot["prototype_desc"] = "New version of prototype"
        old_prot["attrs"] += (("fooattr", "fooattrval", None, ""),)

        # diff obj/prototype
        old_prot_copy = old_prot.copy()

        pdiff, obj_prototype = spawner.prototype_diff_from_object(old_prot, self.obj1)

        self.assertEqual(old_prot_copy, old_prot)

        self.assertEqual(
            obj_prototype,
            {
                "aliases": ["foo"],
                "attrs": [
                    ("desc", "changed desc", None, ""),
                    ("oldtest", "to_keep", None, ""),
                    ("test", "testval", None, ""),
                ],
                "key": "Obj",
                "home": Something,
                "location": Something,
                "locks": ";".join(
                    [
                        "call:true()",
                        "control:perm(Developer)",
                        "delete:perm(Admin)",
                        "drop:holds()",
                        "edit:perm(Admin)",
                        "examine:perm(Builder)",
                        "get:all()",
                        "puppet:pperm(Developer)",
                        "teleport:true()",
                        "teleport_here:true()",
                        "tell:perm(Admin)",
                        "view:all()",
                    ]
                ),
                "prototype_desc": "Built from Obj",
                "prototype_key": Something,
                "prototype_locks": "spawn:all();edit:all()",
                "prototype_tags": [],
                "tags": [("footag", "foocategory", None)],
                "typeclass": "evennia.objects.object.DefaultObject",
            },
        )

        self.assertEqual(
            old_prot,
            {
                "attrs": [("oldtest", "to_keep", None, ""), ("fooattr", "fooattrval", None, "")],
                "home": Something,
                "key": "Obj",
                "location": Something,
                "locks": ";".join(
                    [
                        "call:true()",
                        "control:perm(Developer)",
                        "delete:perm(Admin)",
                        "drop:holds()",
                        "edit:perm(Admin)",
                        "examine:perm(Builder)",
                        "get:all()",
                        "puppet:pperm(Developer)",
                        "teleport:true()",
                        "teleport_here:true()",
                        "tell:perm(Admin)",
                        "view:all()",
                    ]
                ),
                "new": "new_val",
                "permissions": ["Builder"],
                "prototype_desc": "New version of prototype",
                "prototype_key": Something,
                "prototype_locks": "spawn:all();edit:all()",
                "prototype_tags": [],
                "test": "testval_changed",
                "typeclass": "evennia.objects.object.DefaultObject",
            },
        )

        self.assertEqual(
            pdiff,
            {
                "home": (Something, Something, "KEEP"),
                "prototype_locks": ("spawn:all();edit:all()", "spawn:all();edit:all()", "KEEP"),
                "prototype_key": (Something, Something, "UPDATE"),
                "location": (Something, Something, "KEEP"),
                "locks": (
                    ";".join(
                        [
                            "call:true()",
                            "control:perm(Developer)",
                            "delete:perm(Admin)",
                            "drop:holds()",
                            "edit:perm(Admin)",
                            "examine:perm(Builder)",
                            "get:all()",
                            "puppet:pperm(Developer)",
                            "teleport:true()",
                            "teleport_here:true()",
                            "tell:perm(Admin)",
                            "view:all()",
                        ]
                    ),
                    ";".join(
                        [
                            "call:true()",
                            "control:perm(Developer)",
                            "delete:perm(Admin)",
                            "drop:holds()",
                            "edit:perm(Admin)",
                            "examine:perm(Builder)",
                            "get:all()",
                            "puppet:pperm(Developer)",
                            "teleport:true()",
                            "teleport_here:true()",
                            "tell:perm(Admin)",
                            "view:all()",
                        ]
                    ),
                    "KEEP",
                ),
                "prototype_tags": (None, None, "KEEP"),
                "attrs": {
                    "oldtest": (
                        ("oldtest", "to_keep", None, ""),
                        ("oldtest", "to_keep", None, ""),
                        "KEEP",
                    ),
                    "desc": (("desc", "changed desc", None, ""), None, "KEEP"),
                    "fooattr": (Something, ("fooattr", "fooattrval", None, ""), "ADD"),
                    "test": (
                        ("test", "testval", None, ""),
                        ("test", "testval_changed", None, ""),
                        "UPDATE",
                    ),
                    "new": (Something, ("new", "new_val", None, ""), "ADD"),
                },
                "key": ("Obj", "Obj", "KEEP"),
                "typeclass": (
                    "evennia.objects.object.DefaultObject",
                    "evennia.objects.object.DefaultObject",
                    "KEEP",
                ),
                "aliases": {"foo": ("foo", None, "REMOVE")},
                "tags": {"footag": (("footag", "foocategory", None), None, "REMOVE")},
                "prototype_desc": ("Built from Obj", "New version of prototype", "UPDATE"),
                "permissions": {"Builder": (None, "Builder", "ADD")},
            },
        )

        self.assertEqual(
            spawner.flatten_diff(pdiff),
            {
                "aliases": "REMOVE",
                "attrs": "UPDATE",
                "home": "KEEP",
                "key": "KEEP",
                "location": "KEEP",
                "locks": "KEEP",
                "permissions": "UPDATE",
                "prototype_desc": "UPDATE",
                "prototype_key": "UPDATE",
                "prototype_locks": "KEEP",
                "prototype_tags": "KEEP",
                "tags": "REMOVE",
                "typeclass": "KEEP",
            },
        )

        # apply diff
        count = spawner.batch_update_objects_with_prototype(
            old_prot, diff=pdiff, objects=[self.obj1]
        )
        self.assertEqual(count, 1)

        new_prot = spawner.prototype_from_object(self.obj1)
        self.assertEqual(
            {
                "aliases": ["foo"],
                "attrs": [
                    ("desc", "changed desc", None, ""),
                    ("fooattr", "fooattrval", None, ""),
                    ("new", "new_val", None, ""),
                    ("oldtest", "to_keep", None, ""),
                    ("test", "testval_changed", None, ""),
                ],
                "home": Something,
                "key": "Obj",
                "location": Something,
                "locks": ";".join(
                    [
                        "call:true()",
                        "control:perm(Developer)",
                        "delete:perm(Admin)",
                        "drop:holds()",
                        "edit:perm(Admin)",
                        "examine:perm(Builder)",
                        "get:all()",
                        "puppet:pperm(Developer)",
                        "teleport:true()",
                        "teleport_here:true()",
                        "tell:perm(Admin)",
                        "view:all()",
                    ]
                ),
                "tags": [("footag", "foocategory", None), (Something, "from_prototype", None)],
                "permissions": ["builder"],
                "prototype_desc": "Built from Obj",
                "prototype_key": Something,
                "prototype_locks": "spawn:all();edit:all()",
                "prototype_tags": [],
                "typeclass": "evennia.objects.object.DefaultObject",
            },
            new_prot,
        )


class TestProtLib(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        self.obj1.attributes.add("testattr", "testval")
        self.prot = spawner.prototype_from_object(self.obj1)

    def test_prototype_to_str(self):
        prstr = protlib.prototype_to_str(self.prot)
        self.assertTrue(prstr.startswith("|cprototype-key:|n"))

    def test_check_permission(self):
        pass

    def test_save_prototype(self):
        result = protlib.save_prototype(self.prot)
        self.assertEqual(result, self.prot)
        # faulty
        self.prot["prototype_key"] = None
        self.assertRaises(protlib.ValidationError, protlib.save_prototype, self.prot)

    def test_search_prototype(self):
        protlib.save_prototype(self.prot)
        match = protlib.search_prototype("NotFound")
        self.assertFalse(match)
        match = protlib.search_prototype()
        self.assertTrue(match)
        match = protlib.search_prototype(self.prot["prototype_key"])
        self.assertEqual(match, [self.prot])
        match = protlib.search_prototype(self.prot["prototype_key"].upper())
        self.assertEqual(match, [self.prot])

    def test_homogenize_prototype_locks_preserved(self):
        """Test that homogenize_prototype preserves custom prototype_locks. (Bug 3828)"""
        from evennia.prototypes.prototypes import _PROTOTYPE_FALLBACK_LOCK

        prot_with_locks = {
            "prototype_key": "test_prot_with_locks",
            "typeclass": "evennia.objects.object.DefaultObject",
            "prototype_locks": "spawn:perm(Builder);edit:perm(Admin)",
        }
        homogenized = protlib.homogenize_prototype(prot_with_locks)
        self.assertEqual(
            homogenized["prototype_locks"],
            "spawn:perm(Builder);edit:perm(Admin)",
        )
        self.assertNotEqual(
            homogenized["prototype_locks"],
            _PROTOTYPE_FALLBACK_LOCK,
        )

    def test_homogenize_prototype_locks_default_fallback(self):
        """Test that homogenize_prototype uses default when prototype_locks not provided."""
        from evennia.prototypes.prototypes import _PROTOTYPE_FALLBACK_LOCK

        prot_without_locks = {
            "prototype_key": "test_prot_without_locks",
            "typeclass": "evennia.objects.object.DefaultObject",
        }
        homogenized = protlib.homogenize_prototype(prot_without_locks)
        self.assertEqual(
            homogenized["prototype_locks"],
            _PROTOTYPE_FALLBACK_LOCK,
        )


class TestProtFuncs(BaseEvenniaTest):
    @override_settings(PROT_FUNC_MODULES=["evennia.prototypes.protfuncs"])
    def test_protkey_protfunc(self):
        test_prot = {"key1": "value1", "key2": 2}

        self.assertEqual(
            protlib.protfunc_parser("$protkey(key1)", testing=True, prototype=test_prot),
            "value1",
        )
        self.assertEqual(
            protlib.protfunc_parser("$protkey(key2)", testing=True, prototype=test_prot), 2
        )


class TestPrototypeStorage(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        self.maxDiff = None

        self.prot1 = spawner.prototype_from_object(self.obj1)
        self.prot1["prototype_key"] = "testprototype1"
        self.prot1["prototype_desc"] = "testdesc1"
        self.prot1["prototype_tags"] = [("foo1", _PROTOTYPE_TAG_META_CATEGORY)]

        self.prot2 = self.prot1.copy()
        self.prot2["prototype_key"] = "testprototype2"
        self.prot2["prototype_desc"] = "testdesc2"
        self.prot2["prototype_tags"] = [("foo1", _PROTOTYPE_TAG_META_CATEGORY)]

        self.prot3 = self.prot2.copy()
        self.prot3["prototype_key"] = "testprototype3"
        self.prot3["prototype_desc"] = "testdesc3"
        self.prot3["prototype_tags"] = [("foo1", _PROTOTYPE_TAG_META_CATEGORY)]

    def test_prototype_storage(self):
        # from evennia import set_trace;set_trace(term_size=(180, 50))
        prot1 = protlib.create_prototype(self.prot1)

        self.assertTrue(bool(prot1))
        self.assertEqual(prot1, self.prot1)

        self.assertEqual(prot1["prototype_desc"], "testdesc1")

        self.assertEqual(prot1["prototype_tags"], [("foo1", _PROTOTYPE_TAG_META_CATEGORY)])
        self.assertEqual(
            protlib.DbPrototype.objects.get_by_tag("foo1", _PROTOTYPE_TAG_META_CATEGORY)[
                0
            ].db.prototype,
            prot1,
        )

        prot2 = protlib.create_prototype(self.prot2)
        self.assertEqual(
            [
                pobj.db.prototype
                for pobj in protlib.DbPrototype.objects.get_by_tag(
                    "foo1", _PROTOTYPE_TAG_META_CATEGORY
                )
            ],
            [prot1, prot2],
        )

        # add to existing prototype
        prot1b = protlib.create_prototype(
            {"prototype_key": "testprototype1", "foo": "bar", "prototype_tags": ["foo2"]}
        )

        self.assertEqual(
            [
                pobj.db.prototype
                for pobj in protlib.DbPrototype.objects.get_by_tag(
                    "foo2", _PROTOTYPE_TAG_META_CATEGORY
                )
            ],
            [prot1b],
        )

        self.assertEqual(list(protlib.search_prototype("testprototype2")), [prot2])
        self.assertNotEqual(list(protlib.search_prototype("testprototype1")), [prot1])
        self.assertEqual(list(protlib.search_prototype("testprototype1")), [prot1b])

        prot3 = protlib.create_prototype(self.prot3)

        # partial match
        with mock.patch.object(protlib, "_MODULE_PROTOTYPES", {}):
            self.assertCountEqual(protlib.search_prototype("prot"), [prot1b, prot2, prot3])
            self.assertCountEqual(protlib.search_prototype(tags="foo1"), [prot1b, prot2, prot3])

        self.assertTrue(str(str(protlib.list_prototypes(self.char1))))


class PrototypeCrashTest(BaseEvenniaTest):
    # increase this to 1000 for optimization testing
    num_prototypes = 10

    def create(self, num=None):
        if not num:
            num = self.num_prototypes
        # print(f"Creating {num} additional prototypes...")
        for x in range(num):
            prot = {
                "prototype_key": str(uuid.uuid4()),
                "some_attributes": [str(uuid.uuid4()) for x in range(10)],
                "prototype_tags": list(sample(["demo", "test", "stuff"], 2)),
            }
            protlib.save_prototype(prot)

    def test_prototype_dos(self, *args, **kwargs):
        num_prototypes = self.num_prototypes
        for x in range(2):
            self.create(num_prototypes)
            # print("Attempting to list prototypes...")
            # start_time = time()
            self.char1.execute_cmd("@spawn/list")
            # print(f"Prototypes listed in {time()-start_time} seconds.")


class Test2474(BaseEvenniaTest):
    """
    Test bug #2474 (https://github.com/evennia/evennia/issues/2474),
    where the prototype's attribute fails to take precedence over
    that of its prototype_parent.

    """

    prototypes = {
        "WEAPON": {
            "typeclass": "evennia.objects.object.DefaultObject",
            "key": "Weapon",
            "desc": "A generic blade.",
            "magic": False,
        },
        "STING": {
            "prototype_parent": "WEAPON",
            "key": "Sting",
            "desc": "A dagger that shines with a cold light if Orcs are near.",
            "magic": True,
        },
    }

    def test_magic_spawn(self):
        """
        Test magic is inherited.

        """
        sting = spawner.spawn(self.prototypes["STING"], prototype_parents=self.prototypes)[0]
        self.assertEqual(sting.db.magic, True)

    def test_non_magic_spawn(self):
        """
        Test inverse - no magic.

        """
        sting = spawner.spawn(self.prototypes["WEAPON"], prototype_parents=self.prototypes)[0]
        self.assertEqual(sting.db.magic, False)


class TestPartialTagAttributes(BaseEvenniaTest):
    """
    Make sure tags and attributes are homogenized if given as incomplete tuples.

    See https://github.com/evennia/evennia/issues/2524.

    """

    def setUp(self):
        super().setUp()
        self.prot = {
            "prototype_key": "rock",
            "typeclass": "evennia.objects.object.DefaultObject",
            "key": "a rock",
            "tags": [("quantity", "groupable")],  # missing data field
            "attrs": [("quantity", 1)],  # missing category and lock fields
            "desc": "A good way to get stoned.",
        }

    def test_partial_spawn(self):
        obj = spawner.spawn(self.prot)
        self.assertEqual(obj[0].key, self.prot["key"])


class TestIssue2908(BaseEvenniaTest):
    """
    Test spawning a prototype with a nested protfunc, as per issue #2908.

    """

    def test_spawn_with_protfunc(self):
        self.room1.tags.add("beach", category="zone")

        prot = {
            "prototype_key": "rock",
            "typeclass": "evennia.objects.object.DefaultObject",
            "key": "a rock",
            "location": "$choice($objlist(beach,category=zone,type=tag))",
        }

        obj = spawner.spawn(prot, caller=self.char1)
        self.assertEqual(obj[0].location, self.room1)


class TestIssue3824(BaseEvenniaTest):
    """
    Test that $obj, $objlist, $search, and $dbref callables work correctly when spawning prototypes.

    Regression test for bug where 'prototype' kwarg was passed to search functions causing TypeError.
    """

    def test_spawn_with_search_callables(self):
        """Test spawning prototype with $obj, $objlist, $search, and $dbref callables."""
        # Setup: tag some objects for searching
        self.room1.tags.add("test_location", category="zone")
        self.room2.tags.add("test_location", category="zone")
        self.obj1.tags.add("test_item", category="item_type")

        # Create prototype using all search callables
        prot = {
            "prototype_key": "test_search_callables",
            "typeclass": "evennia.objects.object.DefaultObject",
            "key": "test object",
            "attr_obj": f"$obj({self.obj1.dbref})",
            "attr_search": "$search(Char)",
            "attr_objlist": "$objlist(test_location, category=zone, type=tag)",
            "attr_dbref": f"$dbref({self.obj1.dbref})",
        }

        # This should not raise TypeError about 'prototype' kwarg
        objs = spawner.spawn(prot, caller=self.char1)

        self.assertEqual(len(objs), 1)
        obj = objs[0]

        # Verify all search callables worked correctly
        self.assertEqual(obj.db.attr_obj, self.obj1)
        self.assertEqual(obj.db.attr_search, self.char1)
        self.assertEqual(obj.db.attr_dbref, self.obj1)
        # attr_objlist should be a list or list-like object with 2 rooms
        objlist = obj.db.attr_objlist
        self.assertEqual(len(objlist), 2)
        self.assertIn(self.room1, objlist)
        self.assertIn(self.room2, objlist)


class TestIssue3101(EvenniaCommandTest):
    """
    Spawning and using create_object should store the same `typeclass_path` if using
    the same actual typeclass.

    """

    def test_spawn_vs_create_paths(self):
        self.call(
            building.CmdSpawn(),
            '{"key": "first thing", "typeclass": "evennia.DefaultObject"}',
            "Spawned first thing",
        )
        self.call(
            building.CmdCreate(),
            "second thing:evennia.DefaultObject",
            "You create a new DefaultObject: second thing",
        )

        obj1 = ObjectDB.objects.get(db_key="first thing")
        obj2 = ObjectDB.objects.get(db_key="second thing")

        self.assertEqual(obj1.typeclass_path, obj2.typeclass_path)
