from mock import MagicMock, patch

from evennia.objects.models import ObjectDB
from evennia.objects.objects import (DefaultCharacter, DefaultExit,
                                     DefaultObject, DefaultRoom)
from evennia.objects.search_result import Ambiguous, Found, NotFound
from evennia.typeclasses.attributes import AttributeProperty
from evennia.typeclasses.tags import (AliasProperty, PermissionProperty,
                                      TagCategoryProperty, TagProperty)
from evennia.utils import create, search
from evennia.utils.ansi import strip_ansi
from evennia.utils.test_resources import BaseEvenniaTest, EvenniaTestCase


class DefaultObjectTest(BaseEvenniaTest):
    ip = "212.216.139.14"

    def test_object_create(self):
        description = "A home for a grouch."
        home = self.room1.dbref

        obj, errors = DefaultObject.create(
            "trashcan", self.account, description=description, ip=self.ip, home=home
        )
        self.assertTrue(obj, errors)
        self.assertFalse(errors, errors)
        self.assertEqual(description, obj.db.desc)
        self.assertEqual(obj.db.creator_ip, self.ip)
        self.assertEqual(obj.db_home, self.room1)

    def test_object_default_description(self):
        obj, errors = DefaultObject.create("void")
        self.assertTrue(obj, errors)
        self.assertFalse(errors, errors)
        self.assertIsNone(obj.db.desc)
        self.assertEqual(obj.default_description, obj.get_display_desc(obj))

    def test_character_create(self):
        description = "A furry green monster, reeking of garbage."
        home = self.room1.dbref

        obj, errors = DefaultCharacter.create(
            "oscar", self.account, description=description, ip=self.ip, home=home
        )
        self.assertTrue(obj, errors)
        self.assertFalse(errors, errors)
        self.assertEqual(description, obj.db.desc)
        self.assertEqual(obj.db.creator_ip, self.ip)
        self.assertEqual(obj.db_home, self.room1)

    def test_character_create_noaccount(self):
        obj, errors = DefaultCharacter.create("oscar", None, home=self.room1.dbref)
        self.assertTrue(obj, errors)
        self.assertFalse(errors, errors)
        self.assertEqual(obj.db_home, self.room1)

    def test_character_create_weirdname(self):
        obj, errors = DefaultCharacter.create(
            "SigurðurÞórarinsson", self.account, home=self.room1.dbref
        )
        self.assertTrue(obj, errors)
        self.assertFalse(errors, errors)
        self.assertEqual(obj.name, "SigurXurXorarinsson")

    def test_character_default_description(self):
        obj, errors = DefaultCharacter.create("dementor")
        self.assertTrue(obj, errors)
        self.assertFalse(errors, errors)
        self.assertIsNone(obj.db.desc)
        self.assertEqual(obj.default_description, obj.get_display_desc(obj))

    def test_room_create(self):
        description = "A dimly-lit alley behind the local Chinese restaurant."
        obj, errors = DefaultRoom.create("alley", self.account, description=description, ip=self.ip)
        self.assertTrue(obj, errors)
        self.assertFalse(errors, errors)
        self.assertEqual(description, obj.db.desc)
        self.assertEqual(obj.db.creator_ip, self.ip)

    def test_room_default_description(self):
        obj, errors = DefaultRoom.create("black hole")
        self.assertTrue(obj, errors)
        self.assertFalse(errors, errors)
        self.assertIsNone(obj.db.desc)
        self.assertEqual(obj.default_description, obj.get_display_desc(obj))

    def test_exit_create(self):
        description = (
            "The steaming depths of the dumpster, ripe with refuse in various states of"
            " decomposition."
        )
        obj, errors = DefaultExit.create(
            "in", self.room1, self.room2, account=self.account, description=description, ip=self.ip
        )
        self.assertTrue(obj, errors)
        self.assertFalse(errors, errors)
        self.assertEqual(description, obj.db.desc)
        self.assertEqual(obj.db.creator_ip, self.ip)

    def test_exit_default_description(self):
        obj, errors = DefaultExit.create("the nothing")
        self.assertTrue(obj, errors)
        self.assertFalse(errors, errors)
        self.assertIsNone(obj.db.desc)
        self.assertEqual(obj.default_description, obj.get_display_desc(obj))

    def test_exit_get_return_exit(self):
        ex1, _ = DefaultExit.create("north", self.room1, self.room2, account=self.account)
        single_return_exit = ex1.get_return_exit()
        all_return_exit = ex1.get_return_exit(return_all=True)
        self.assertEqual(single_return_exit, None)
        self.assertEqual(len(all_return_exit), 0)

        ex2, _ = DefaultExit.create("south", self.room2, self.room1, account=self.account)
        single_return_exit = ex1.get_return_exit()
        all_return_exit = ex1.get_return_exit(return_all=True)
        self.assertEqual(single_return_exit, ex2)
        self.assertEqual(len(all_return_exit), 1)

        ex3, _ = DefaultExit.create("also_south", self.room2, self.room1, account=self.account)
        all_return_exit = ex1.get_return_exit(return_all=True)
        self.assertEqual(len(all_return_exit), 2)

    def test_exit_order(self):
        DefaultExit.create("south", self.room1, self.room2, account=self.account)
        DefaultExit.create("portal", self.room1, self.room2, account=self.account)
        DefaultExit.create("north", self.room1, self.room2, account=self.account)
        DefaultExit.create("aperture", self.room1, self.room2, account=self.account)

        # in creation order; stock get_content_group_label("exits") returns ""
        # so no "Exits:" prefix ships engine-side
        exits = strip_ansi(self.room1.get_display_exits(self.char1))
        self.assertEqual(exits, "out, south, portal, north, and aperture")

        # in specified order with unspecified exits alpbabetically on the end
        exit_order = ("north", "south", "out")
        exits = strip_ansi(self.room1.get_display_exits(self.char1, exit_order=exit_order))
        self.assertEqual(exits, "north, south, out, aperture, and portal")

    def test_urls(self):
        "Make sure objects are returning URLs"
        self.assertTrue(self.char1.get_absolute_url())
        self.assertTrue("admin" in self.char1.web_get_admin_url())

        self.assertTrue(self.room1.get_absolute_url())
        self.assertTrue("admin" in self.room1.web_get_admin_url())

    def test_search_stacked(self):
        "Test searching stacks"
        coin1 = DefaultObject.create("coin", location=self.room1)[0]
        coin2 = DefaultObject.create("coin", location=self.room1)[0]
        colon = DefaultObject.create("colon", location=self.room1)[0]

        # stack
        self.assertEqual(self.char1.search("coin", stacked=2), [coin1, coin2])
        self.assertEqual(self.char1.search("coin", stacked=5), [coin1, coin2])
        # partial match to 'colon' - multimatch error since stack is not homogenous
        self.assertEqual(self.char1.search("co", stacked=2), None)

    def test_search_ordinal_last(self):
        """first/last/other multimatch input resolves to one object via search_for."""
        a = DefaultObject.create("gem", location=self.room1)[0]
        b = DefaultObject.create("gem", location=self.room1)[0]
        c = DefaultObject.create("gem", location=self.room1)[0]
        self.assertEqual(self.char1.search_for("first gem"), Found(obj=a))
        self.assertEqual(self.char1.search_for("last gem"), Found(obj=c))
        d = DefaultObject.create("orb", location=self.room1)[0]
        e = DefaultObject.create("orb", location=self.room1)[0]
        self.assertEqual(self.char1.search_for("other orb"), Found(obj=e))
        # "other gem" is invalid against 3 gems → Ambiguous with invalid_other
        result = self.char1.search_for("other gem")
        self.assertIsInstance(result, Ambiguous)
        self.assertTrue(result.invalid_other)
        # sugar .search() emits prompt and returns None
        self.assertIsNone(self.char1.search("other gem"))

    def test_search_location_scope(self):
        """my/here narrow candidates before matching."""
        room_gem = DefaultObject.create("gem", location=self.room1)[0]
        inv_gem = DefaultObject.create("gem", location=self.char1)[0]
        self.assertEqual(self.char1.search_for("here gem"), Found(obj=room_gem))
        self.assertEqual(self.char1.search_for("my gem"), Found(obj=inv_gem))

    def test_search_autopick(self):
        """Auto-pick collapses single-bucket results."""
        only_room = DefaultObject.create("pebble", location=self.room1)[0]
        # single match: Found
        self.assertEqual(self.char1.search_for("pebble"), Found(obj=only_room))
        # sugar returns the object directly
        self.assertEqual(self.char1.search("pebble"), only_room)

        DefaultObject.create("pebble", location=self.room1)
        # multi-match without qualifier → Ambiguous (autopick can't help across same bucket)
        result = self.char1.search_for("pebble")
        self.assertIsInstance(result, Ambiguous)
        self.assertEqual(len(result.candidates), 2)

        inv_pebble = DefaultObject.create("pebble", location=self.char1)[0]
        DefaultObject.create("pebble", location=self.char1)
        # "my pebble" narrows to inventory (2 pebbles) → Ambiguous
        result = self.char1.search_for("my pebble")
        self.assertIsInstance(result, Ambiguous)
        self.assertEqual(len(result.candidates), 2)

    def test_search_plural_form(self):
        """Plural-form searches return all matches as Ambiguous candidates."""
        coin1 = DefaultObject.create("coin", location=self.room1)[0]
        coin2 = DefaultObject.create("coin", location=self.room1)[0]
        coin3 = DefaultObject.create("coin", location=self.room1)[0]
        # build the numbered aliases
        coin1.get_numbered_name(2, self.char1)
        coin2.get_numbered_name(3, self.char1)
        coin3.get_numbered_name(4, self.char1)

        result = self.char1.search_for("coin")
        self.assertIsInstance(result, Ambiguous)
        self.assertEqual(result.candidates, [coin1, coin2, coin3])
        result = self.char1.search_for("coins")
        self.assertIsInstance(result, Ambiguous)
        self.assertEqual(result.candidates, [coin1, coin2, coin3])

    def test_get_default_lockstring_base(self):
        pattern = (
            f"control:pid({self.account.id}) or id({self.char1.id}) or"
            f" perm(Admin);delete:pid({self.account.id}) or id({self.char1.id}) or"
            f" perm(Admin);edit:pid({self.account.id}) or id({self.char1.id}) or perm(Admin)"
        )
        self.assertEqual(
            DefaultObject.get_default_lockstring(account=self.account, caller=self.char1), pattern
        )

    def test_search_by_tag_kwarg(self):
        "Test the by_tag method"

        self.obj1.tags.add("plugh", category="adventure")

        self.assertEqual(self.char1.search_for("Obj"), Found(obj=self.obj1))
        # should not find a match
        self.assertIsInstance(self.char1.search_for("Dummy"), NotFound)
        # should still not find a match
        self.assertIsInstance(
            self.char1.search_for("Dummy", tags=[("plugh", "adventure")]), NotFound
        )

        self.assertEqual(list(search.search_object("Dummy", tags=[("plugh", "adventure")])), [])
        self.assertEqual(
            list(search.search_object("Obj", tags=[("plugh", "adventure")])), [self.obj1]
        )
        self.assertEqual(list(search.search_object("Obj", tags=[("dummy", "adventure")])), [])

    def test_get_default_lockstring_room(self):
        pattern = (
            f"control:pid({self.account.id}) or id({self.char1.id}) or"
            f" perm(Admin);delete:pid({self.account.id}) or id({self.char1.id}) or"
            f" perm(Admin);edit:pid({self.account.id}) or id({self.char1.id}) or perm(Admin)"
        )
        self.assertEqual(
            DefaultRoom.get_default_lockstring(account=self.account, caller=self.char1), pattern
        )

    def test_get_default_lockstring_exit(self):
        pattern = (
            f"control:pid({self.account.id}) or id({self.char1.id}) or"
            f" perm(Admin);delete:pid({self.account.id}) or id({self.char1.id}) or"
            f" perm(Admin);edit:pid({self.account.id}) or id({self.char1.id}) or perm(Admin)"
        )
        self.assertEqual(
            DefaultExit.get_default_lockstring(account=self.account, caller=self.char1), pattern
        )

    def test_get_default_lockstring_character(self):
        pattern = (
            f"puppet:pid({self.account.id}) or perm(Developer) or"
            f" pperm(Developer);delete:pid({self.account.id}) or"
            f" perm(Admin);edit:pid({self.account.id}) or perm(Admin)"
        )
        self.assertEqual(
            DefaultCharacter.get_default_lockstring(account=self.account, caller=self.char1),
            pattern,
        )

    def test_get_name_without_article(self):
        self.assertEqual(self.obj1.get_numbered_name(1, self.char1, return_string=True), "an Obj")
        self.assertEqual(
            self.obj1.get_numbered_name(1, self.char1, return_string=True, no_article=True), "Obj"
        )


class TestObjectManager(BaseEvenniaTest):
    "Test object manager methods"

    def test_create_object_with_none_key(self):
        """Test that create_object() handles key=None and key="" correctly."""
        # Test with key=None - should convert to "" and then to #dbref
        obj_none = ObjectDB.objects.create_object(key=None, location=self.room1)
        self.assertIsNotNone(obj_none)
        self.assertEqual(obj_none.key, f"#{obj_none.id}")
        obj_none.delete()

        # Test with key="" - should convert to #dbref
        obj_empty = ObjectDB.objects.create_object(key="", location=self.room1)
        self.assertIsNotNone(obj_empty)
        self.assertEqual(obj_empty.key, f"#{obj_empty.id}")
        obj_empty.delete()

    def test_get_object_with_account(self):
        query = ObjectDB.objects.get_object_with_account("TestAccount").first()
        self.assertEqual(query, self.char1)
        query = ObjectDB.objects.get_object_with_account(self.account.dbref)
        self.assertEqual(query, self.char1)
        query = ObjectDB.objects.get_object_with_account("#123456")
        self.assertFalse(query)
        query = ObjectDB.objects.get_object_with_account("TestAccou").first()
        self.assertFalse(query)

        query = ObjectDB.objects.get_object_with_account("TestAccou", exact=False)
        self.assertEqual(tuple(query), (self.char1, self.char2))

        query = ObjectDB.objects.get_object_with_account(
            "TestAccou", candidates=[self.char1, self.obj1], exact=False
        )
        self.assertEqual(list(query), [self.char1])

    def test_get_objs_with_key_and_typeclass(self):
        query = ObjectDB.objects.get_objs_with_key_and_typeclass(
            "Char", "evennia.objects.character.DefaultCharacter"
        )
        self.assertEqual(list(query), [self.char1])
        query = ObjectDB.objects.get_objs_with_key_and_typeclass(
            "Char", "evennia.objects.object.DefaultObject"
        )
        self.assertFalse(query)
        query = ObjectDB.objects.get_objs_with_key_and_typeclass(
            "NotFound", "evennia.objects.character.DefaultCharacter"
        )
        self.assertFalse(query)
        query = ObjectDB.objects.get_objs_with_key_and_typeclass(
            "Char",
            "evennia.objects.character.DefaultCharacter",
            candidates=[self.char1, self.char2],
        )
        self.assertEqual(list(query), [self.char1])

    def test_get_objs_with_key_or_alias(self):
        query = ObjectDB.objects.get_objs_with_key_or_alias("Char")
        self.assertEqual(list(query), [self.char1])
        query = ObjectDB.objects.get_objs_with_key_or_alias(
            "Char", typeclasses="evennia.objects.object.DefaultObject"
        )
        self.assertEqual(list(query), [])
        query = ObjectDB.objects.get_objs_with_key_or_alias(
            "Char", candidates=[self.char1, self.char2]
        )
        self.assertEqual(list(query), [self.char1])

        self.char1.aliases.add("test alias")
        query = ObjectDB.objects.get_objs_with_key_or_alias("test alias")
        self.assertEqual(list(query), [self.char1])

        query = ObjectDB.objects.get_objs_with_key_or_alias("")
        self.assertFalse(query)
        query = ObjectDB.objects.get_objs_with_key_or_alias("", exact=False)
        self.assertEqual(list(query), list(ObjectDB.objects.all().order_by("id")))

        query = ObjectDB.objects.get_objs_with_key_or_alias(
            "", exact=False, typeclasses="evennia.objects.character.DefaultCharacter"
        )
        self.assertEqual(list(query), [self.char1, self.char2])

    def test_key_alias_search_partial_match(self):
        """
        verify that get_objs_with_key_or_alias will partial match the first part of
        any words in the name, when given in the correct order
        """
        self.obj1.key = "big sword"
        self.obj2.key = "shiny sword"

        # beginning of "sword", should match both
        query = ObjectDB.objects.get_objs_with_key_or_alias("sw", exact=False)
        self.assertEqual(list(query), [self.obj1, self.obj2])

        # middle of "sword", should NOT match
        query = ObjectDB.objects.get_objs_with_key_or_alias("wor", exact=False)
        self.assertEqual(list(query), [])

        # beginning of "big" then "sword", should match obj1
        query = ObjectDB.objects.get_objs_with_key_or_alias("b sw", exact=False)
        self.assertEqual(list(query), [self.obj1])

        # beginning of "sword" then "big", should NOT match
        query = ObjectDB.objects.get_objs_with_key_or_alias("sw b", exact=False)
        self.assertEqual(list(query), [])

    def test_search_object(self):
        self.char1.tags.add("test tag")
        self.obj1.tags.add("test tag")

        query = ObjectDB.objects.search_object("", exact=False, tags=[("test tag", None)])
        self.assertEqual(list(query), [self.obj1, self.char1])

        query = ObjectDB.objects.search_object("Char", tags=[("invalid tag", None)])
        self.assertFalse(query)

        query = ObjectDB.objects.search_object(
            "",
            exact=False,
            tags=[("test tag", None)],
            typeclass="evennia.objects.character.DefaultCharacter",
        )
        self.assertEqual(list(query), [self.char1])

    def test_get_objs_with_attr(self):
        self.obj1.db.testattr = "testval1"
        query = ObjectDB.objects.get_objs_with_attr("testattr")
        self.assertEqual(list(query), [self.obj1])
        query = ObjectDB.objects.get_objs_with_attr("testattr", candidates=[self.char1, self.obj1])
        self.assertEqual(list(query), [self.obj1])
        query = ObjectDB.objects.get_objs_with_attr("NotFound", candidates=[self.char1, self.obj1])
        self.assertFalse(query)

    def test_copy_object(self):
        "Test that all attributes and tags properly copy across objects"

        # Add some tags
        self.obj1.tags.add("plugh", category="adventure")
        self.obj1.tags.add("xyzzy")

        # Add some attributes
        self.obj1.attributes.add("phrase", "plugh", category="adventure")
        self.obj1.attributes.add("phrase", "xyzzy")

        # Create object copy
        obj2 = self.obj1.copy()

        # Make sure each of the tags were replicated
        self.assertTrue("plugh" in obj2.tags.all())
        self.assertTrue("plugh" in obj2.tags.get(category="adventure"))
        self.assertTrue("xyzzy" in obj2.tags.all())

        # Make sure each of the attributes were replicated
        self.assertEqual(obj2.attributes.get(key="phrase"), "xyzzy")
        self.assertEqual(self.obj1.attributes.get(key="phrase", category="adventure"), "plugh")
        self.assertEqual(obj2.attributes.get(key="phrase", category="adventure"), "plugh")

    def test_copy_object_clone_key(self):
        # reset key to avoid overlap with other tests
        self.obj1.key = "CopyMe"
        copied = self.obj1.copy()
        self.assertEqual(copied.key, "CopyMe001")
        copied2 = self.obj1.copy()
        self.assertEqual(copied2.key, "CopyMe002")
        # verify that it increments based on max existing identifier
        # both for skipped numbers...
        copied.key = "CopyMe003"
        copied3 = self.obj1.copy()
        self.assertEqual(copied3.key, "CopyMe004")
        copied3.delete()
        # ...and for duplicate numbers
        copied.key = "CopyMe001"
        copied2.key = "CopyMe001"
        copied3 = self.obj1.copy()
        self.assertEqual(copied3.key, "CopyMe002")
        # and that sharing a partial prefix doesn't count
        copied3.delete()
        copied.key = "CopyMeMe002"
        copied2.key = "CopyMe001"
        copied3 = self.obj1.copy()
        self.assertEqual(copied3.key, "CopyMe002")
        # and that nothing breaks if something in the room doesn't share the prefix
        copied3.key = "NotACopy"
        copied4 = self.obj1.copy()
        self.assertEqual(copied4.key, "CopyMe002")

    def test_copy_object_no_location(self):
        self.obj1.location = None
        # we just want to make sure this doesn't error
        self.assertIsNotNone(self.obj1.copy())


class TestContentHandler(BaseEvenniaTest):
    "Test the ContentHandler (obj.contents)"

    def test_object_create_remove(self):
        """Create/destroy object"""
        self.assertTrue(self.obj1 in self.room1.contents)
        self.assertTrue(self.obj2 in self.room1.contents)

        obj3 = create.create_object(key="obj3", location=self.room1)
        self.assertTrue(obj3 in self.room1.contents)

        obj3.delete()
        self.assertFalse(obj3 in self.room1.contents)

    def test_object_move(self):
        """Move object from room to room in various ways"""
        self.assertTrue(self.obj1 in self.room1.contents)
        # use move_to hook
        self.obj1.move_to(self.room2)
        self.assertFalse(self.obj1 in self.room1.contents)
        self.assertTrue(self.obj1 in self.room2.contents)

        # move back via direct setting of .location
        self.obj1.location = self.room1
        self.assertTrue(self.obj1 in self.room1.contents)
        self.assertFalse(self.obj1 in self.room2.contents)

    def test_content_type(self):
        self.assertEqual(
            set(self.room1.contents_get()),
            set([self.char1, self.char2, self.obj1, self.obj2, self.exit]),
        )
        self.assertEqual(
            set(self.room1.contents_get(content_type="object")), set([self.obj1, self.obj2])
        )
        self.assertEqual(
            set(self.room1.contents_get(content_type="character")), set([self.char1, self.char2])
        )
        self.assertEqual(set(self.room1.contents_get(content_type="exit")), set([self.exit]))

    def test_contents_order(self):
        """Move object from room to room in various ways"""
        self.assertEqual(
            self.room1.contents, [self.exit, self.obj1, self.obj2, self.char1, self.char2]
        )
        self.assertEqual(self.room2.contents, [])

        # use move_to hook to move obj1
        self.obj1.move_to(self.room2)
        self.assertEqual(self.room1.contents, [self.exit, self.obj2, self.char1, self.char2])
        self.assertEqual(self.room2.contents, [self.obj1])

        # move obj2
        self.obj2.move_to(self.room2)
        self.assertEqual(self.room1.contents, [self.exit, self.char1, self.char2])
        self.assertEqual(self.room2.contents, [self.obj1, self.obj2])

        # move back and forth - it should
        self.obj1.move_to(self.room1)
        self.assertEqual(self.room1.contents, [self.exit, self.char1, self.char2, self.obj1])
        self.obj1.move_to(self.room2)
        self.assertEqual(self.room2.contents, [self.obj2, self.obj1])

        # use move_to hook
        self.obj2.move_to(self.room1)
        self.obj2.move_to(self.room2)
        self.assertEqual(self.room2.contents, [self.obj1, self.obj2])


class TestExitCommand(BaseEvenniaTest):
    """Test the ExitCommand class."""

    def _get_exit_cmd(self):
        """Create an ExitCommand from the test exit object."""
        cmdset = self.exit.create_exit_cmdset(self.exit)
        return [cmd for cmd in cmdset.commands if cmd.key == "out"][0]

    def test_get_display_name(self):
        """ExitCommand.get_display_name should delegate to the exit object."""
        cmd = self._get_exit_cmd()
        self.assertEqual(cmd.get_display_name(self.char1), "out")

    def test_get_extra_info_with_destination(self):
        """ExitCommand.get_extra_info should show destination."""
        cmd = self._get_exit_cmd()
        info = cmd.get_extra_info(self.char1)
        self.assertIn("Room2", info)

    def test_get_extra_info_no_destination(self):
        """ExitCommand.get_extra_info should return '(exit)' with no destination."""
        self.exit.destination = None
        cmd = self._get_exit_cmd()
        info = cmd.get_extra_info(self.char1)
        self.assertIn("exit", info)


class SubAttributeProperty(AttributeProperty):
    pass


class SubTagProperty(TagProperty):
    pass


class CustomizedProperty(AttributeProperty):
    def at_set(self, value, obj):
        obj.settest = value
        return value

    def at_get(self, value, obj):
        return value + obj.awaretest


class TestObjectPropertiesClass(DefaultObject):
    attr1 = AttributeProperty(default="attr1")
    attr2 = AttributeProperty(default="attr2", category="attrcategory")
    attr3 = AttributeProperty(default="attr3", autocreate=False)
    attr4 = SubAttributeProperty(default="attr4")
    attr5 = AttributeProperty(default=list, autocreate=False)
    attr6 = AttributeProperty(default=[None], autocreate=False)
    attr7 = AttributeProperty(default=list)
    attr8 = AttributeProperty(default=[None])
    cusattr = CustomizedProperty(default=5)
    tag1 = TagProperty()
    tag2 = TagProperty(category="tagcategory")
    tag3 = SubTagProperty()
    testalias = AliasProperty()
    testperm = PermissionProperty()
    awaretest = 5
    settest = 0
    tagcategory1 = TagCategoryProperty("category_tag1")
    tagcategory2 = TagCategoryProperty("category_tag1", "category_tag2", "category_tag3")

    @property
    def base_property(self):
        self.property_initialized = True


class MixinAttributeProperty:
    strength = AttributeProperty(default=0, category="stat")
    agility = AttributeProperty(default=0, category="stat", autocreate=False)


class MixinTagProperty:
    mytag = TagProperty(category="mixin_tags")


class TestObjectPropertiesMixinClass(MixinAttributeProperty, MixinTagProperty, DefaultObject):
    pass


class TestProperties(EvenniaTestCase):
    """
    Test Properties.

    """

    def setUp(self):
        self.obj: TestObjectPropertiesClass = create.create_object(
            TestObjectPropertiesClass, key="testobj"
        )

    def tearDown(self):
        self.obj.delete()

    def test_attribute_properties(self):
        obj = self.obj

        self.assertEqual(obj.db.attr1, "attr1")
        self.assertEqual(obj.attributes.get("attr1"), "attr1")
        self.assertEqual(obj.attr1, "attr1")

        self.assertEqual(obj.attributes.get("attr2", category="attrcategory"), "attr2")
        self.assertEqual(obj.db.attr2, None)  # category mismatch
        self.assertEqual(obj.attr2, "attr2")

        self.assertEqual(obj.db.attr3, None)  # non-autocreate, so not in db yet
        self.assertFalse(obj.attributes.has("attr3"))
        self.assertEqual(obj.attr3, "attr3")

        self.assertEqual(obj.db.attr4, "attr4")
        self.assertEqual(obj.attributes.get("attr4"), "attr4")
        self.assertEqual(obj.attr4, "attr4")

        obj.attr3 = "attr3b"  # stores it in db!

        self.assertEqual(obj.db.attr3, "attr3b")
        self.assertTrue(obj.attributes.has("attr3"))

    def test_tag_properties(self):
        obj = self.obj

        self.assertTrue(obj.tags.has("tag1"))
        self.assertTrue(obj.tags.has("tag2", category="tagcategory"))
        self.assertTrue(obj.tags.has("tag3"))

        self.assertTrue(obj.aliases.has("testalias"))
        self.assertTrue(obj.permissions.has("testperm"))

        # Verify that regular properties do not get fetched in init_evennia_properties,
        # only Attribute or TagProperties.
        self.assertFalse(hasattr(obj, "property_initialized"))

    def test_tag_category_properties(self):
        obj = self.obj

        self.assertFalse(obj.tags.has("category_tag1"))  # no category
        self.assertTrue(obj.tags.has("category_tag1", category="tagcategory1"))
        self.assertTrue(obj.tags.has("category_tag1", category="tagcategory2"))
        self.assertTrue(obj.tags.has("category_tag2", category="tagcategory2"))
        self.assertTrue(obj.tags.has("category_tag3", category="tagcategory2"))

        self.assertEqual(obj.tagcategory1, ["category_tag1"])
        self.assertEqual(
            set(obj.tagcategory2), set(["category_tag1", "category_tag2", "category_tag3"])
        )

    def test_tag_category_properties_external_modification(self):
        obj = self.obj

        self.assertEqual(obj.tagcategory1, ["category_tag1"])
        self.assertEqual(
            set(obj.tagcategory2), set(["category_tag1", "category_tag2", "category_tag3"])
        )

        # add extra tag to category
        obj.tags.add("category_tag2", category="tagcategory1")
        self.assertEqual(
            set(obj.tags.get(category="tagcategory1")),
            set(["category_tag1", "category_tag2"]),
        )
        self.assertEqual(set(obj.tagcategory1), set(["category_tag1", "category_tag2"]))

        # add/remove extra tags to category
        obj.tags.add("category_tag4", category="tagcategory2")
        obj.tags.remove("category_tag3", category="tagcategory2")
        self.assertEqual(
            set(obj.tags.get(category="tagcategory2", return_list=True)),
            set(["category_tag1", "category_tag2", "category_tag4"]),
        )
        # note that when we access the property again, it will be updated to contain the same tags
        self.assertEqual(
            set(obj.tagcategory2),
            set(["category_tag1", "category_tag2", "category_tag3", "category_tag4"]),
        )

        del obj.tagcategory1
        # should be deleted from database
        self.assertEqual(obj.tags.get(category="tagcategory1", return_list=True), [])
        # accessing the property should return the default value
        self.assertEqual(obj.tagcategory1, ["category_tag1"])

        del obj.tagcategory2
        # should be deleted from database
        self.assertEqual(obj.tags.get(category="tagcategory2", return_list=True), [])
        # accessing the property should return the default value
        self.assertEqual(
            set(obj.tagcategory2), set(["category_tag1", "category_tag2", "category_tag3"])
        )

    def test_object_awareness(self):
        """Test the "object-awareness" of customized AttributeProperty getter/setters"""
        obj = self.obj

        # attribute properties receive on obj ref in the getter/setter that can customize return
        self.assertEqual(obj.cusattr, 10)
        self.assertEqual(obj.settest, 5)
        obj.awaretest = 10
        self.assertEqual(obj.cusattr, 15)
        obj.cusattr = 10
        self.assertEqual(obj.cusattr, 20)
        self.assertEqual(obj.settest, 10)

        # attribute value mutates if you do += or similar (combined get-set)
        obj.cusattr += 10
        self.assertEqual(obj.attributes.get("cusattr"), 30)
        self.assertEqual(obj.settest, 30)
        self.assertEqual(obj.cusattr, 40)
        obj.awaretest = 0
        obj.cusattr += 20
        self.assertEqual(obj.attributes.get("cusattr"), 50)
        self.assertEqual(obj.settest, 50)
        self.assertEqual(obj.cusattr, 50)
        del obj.cusattr
        self.assertEqual(obj.cusattr, 5)
        self.assertEqual(obj.settest, 5)

    def test_stored_object_queries(self):
        """
        Test https://github.com/evennia/evennia/issues/3155, where AttributeProperties
        holding another object references would lead to db queries not finding
        that nested object.
        """
        obj1 = create.create_object(TestObjectPropertiesClass, key="obj1")
        obj2 = create.create_object(TestObjectPropertiesClass, key="obj2")
        obj1.attr1 = obj2

        # check property works
        self.assertEqual(obj1.attr1, obj2)

        self.assertEqual(obj1.attributes.get("attr1"), obj2)
        obj1.attributes.reset_cache()
        self.assertEqual(obj1.attributes.get("attr1"), obj2)

        obj1.delete()
        obj2.delete()

    def test_not_create_attribute_with_autocreate_false(self):
        """
        Test that AttributeProperty with autocreate=False does not create an attribute in the database.

        """
        obj = create.create_object(TestObjectPropertiesClass, key="obj1")

        self.assertEqual(obj.attr3, "attr3")
        self.assertEqual(obj.attributes.get("attr3"), None)

        self.assertEqual(obj.attr5, [])
        self.assertEqual(obj.attributes.get("attr5"), None)

        obj.delete()

    def test_callable_defaults__autocreate_false(self):
        """
        Test https://github.com/evennia/evennia/issues/3488, where a callable default value like `list`
        would produce an infinitely empty result even when appended to.

        """
        obj1 = create.create_object(TestObjectPropertiesClass, key="obj1")
        obj2 = create.create_object(TestObjectPropertiesClass, key="obj2")

        self.assertEqual(obj1.attr5, [])
        obj1.attr5.append(1)
        self.assertEqual(obj1.attr5, [1])

        # check cross-instance sharing
        self.assertEqual(obj2.attr5, [], "cross-instance sharing detected")

    def test_mutable_defaults__autocreate_false(self):
        """
        Test https://github.com/evennia/evennia/issues/3488, where a mutable default value (like a
        list `[]` or `[None]`) would not be updated in the database when appended to.

        Note that using a mutable default value is not recommended, as the mutable will share the
        same memory space across all instances of the class. This means that if one instance modifiesA
        the mutable, all instances will be affected.

        """
        obj1 = create.create_object(TestObjectPropertiesClass, key="obj1")
        obj2 = create.create_object(TestObjectPropertiesClass, key="obj2")

        self.assertEqual(obj1.attr6, [None])
        obj1.attr6.append(1)
        self.assertEqual(obj1.attr6, [None, 1])

        obj1.attr6[1] = 2
        self.assertEqual(obj1.attr6, [None, 2])

        # check cross-instance sharing
        self.assertEqual(obj2.attr6, [None], "cross-instance sharing detected")

        obj1.delete()
        obj2.delete()

    def test_callable_defaults__autocreate_true(self):
        """
        Test callables with autocreate=True.

        """
        obj1 = create.create_object(TestObjectPropertiesClass, key="obj1")
        obj2 = create.create_object(TestObjectPropertiesClass, key="obj1")

        self.assertEqual(obj1.attr7, [])
        obj1.attr7.append(1)
        self.assertEqual(obj1.attr7, [1])

        # check cross-instance sharing
        self.assertEqual(obj2.attr7, [])

    def test_mutable_defaults__autocreate_true(self):
        """
        Test mutable defaults with autocreate=True.

        """
        obj1 = create.create_object(TestObjectPropertiesClass, key="obj1")
        obj2 = create.create_object(TestObjectPropertiesClass, key="obj2")

        self.assertEqual(obj1.attr8, [None])
        obj1.attr8.append(1)
        self.assertEqual(obj1.attr8, [None, 1])

        obj1.attr8[1] = 2
        self.assertEqual(obj1.attr8, [None, 2])

        # check cross-instance sharing
        self.assertEqual(obj2.attr8, [None])

        obj1.delete()
        obj2.delete()

    def test_mixin_properties_initialized_on_creation(self):
        """
        Test regression for #3155: properties on mixins should initialize on create.
        """
        obj = create.create_object(TestObjectPropertiesMixinClass, key="mixin_prop_obj")

        self.assertEqual(obj.attributes.get("strength", category="stat"), 0)
        self.assertEqual(obj.strength, 0)

        # non-autocreate should still not exist in db until explicitly accessed
        self.assertEqual(obj.attributes.get("agility", category="stat"), None)
        self.assertEqual(obj.agility, 0)
        self.assertEqual(obj.attributes.get("agility", category="stat"), None)

        self.assertTrue(obj.tags.has("mytag", category="mixin_tags"))

        obj.delete()


class TestMsgContentsRecipients(BaseEvenniaTest):
    def test_get_message_recipients_respects_exclude(self):
        self.char2.location = self.room1
        recipients = self.room1.get_message_recipients(exclude=[self.char1])
        self.assertIn(self.char2, recipients)
        self.assertNotIn(self.char1, recipients)

    def test_msg_contents_director_only(self):
        self.char1.location = self.room1
        self.char2.location = self.room1
        with patch.object(self.char2, "msg") as mock_msg:
            self.room1.msg_contents(
                "{speaker} waves.",
                from_obj=self.char1,
                mapping={"speaker": self.char1},
            )
            self.assertTrue(mock_msg.called)
            text = mock_msg.call_args[1].get("text") or mock_msg.call_args[0][0]
            if isinstance(text, tuple):
                text = text[0]
            self.assertIn("waves", text)


class TestExtraDisplayState(BaseEvenniaTest):
    """get_extra_display_state renders via the {extra_state} template key."""

    def test_stub_is_empty(self):
        out = strip_ansi(self.char1.return_appearance(self.char1))
        self.assertNotIn("\n\n", out, msg="Stub should not introduce a blank line")

    def test_override_appears_in_output(self):
        with patch.object(
            DefaultCharacter,
            "get_extra_display_state",
            return_value="\nis poised for combat.",
        ):
            out = strip_ansi(self.char1.return_appearance(self.char1))
        self.assertIn("is poised for combat.", out)


class TestMovementHookRenames(BaseEvenniaTest):
    """Renamed and new movement hooks fire with the right semantics."""

    def test_at_pre_leave_veto_aborts_move(self):
        with patch.object(type(self.room1), "at_pre_leave", return_value=False):
            ok = self.char1.move_to(self.room2, quiet=True)
        self.assertFalse(ok)
        self.assertEqual(self.char1.location, self.room1)

    def test_at_pre_arrive_veto_aborts_move(self):
        with patch.object(type(self.room2), "at_pre_arrive", return_value=False):
            ok = self.char1.move_to(self.room2, quiet=True)
        self.assertFalse(ok)
        self.assertEqual(self.char1.location, self.room1)

    def test_at_post_leave_fires_after_location_change(self):
        observed = {}

        def spy(self, moved_obj, target_location, **kwargs):
            observed["src_contains_mover"] = moved_obj in self.contents
            observed["mover_loc"] = moved_obj.location

        with patch.object(type(self.room1), "at_post_leave", spy):
            self.char1.move_to(self.room2, quiet=True)
        self.assertFalse(observed["src_contains_mover"])
        self.assertEqual(observed["mover_loc"], self.room2)

    def test_at_post_arrive_fires_after_location_change(self):
        observed = {}

        def spy(self, moved_obj, source_location, **kwargs):
            observed["dest_contains_mover"] = moved_obj in self.contents

        with patch.object(type(self.room2), "at_post_arrive", spy):
            self.char1.move_to(self.room2, quiet=True)
        self.assertTrue(observed["dest_contains_mover"])


class TestAtPreRename(BaseEvenniaTest):
    """at_pre_rename can veto a rename."""

    def test_veto_blocks_rename(self):
        with patch.object(type(self.obj1), "at_pre_rename", return_value=False):
            old = self.obj1.key
            self.obj1.key = "shouldnotapply"
        self.assertEqual(self.obj1.key, old)

    def test_allow_lets_rename_proceed(self):
        self.obj1.key = "renamed_ok"
        self.assertEqual(self.obj1.key, "renamed_ok")

    def test_identity_rename_skips_hooks(self):
        same = self.obj1.key
        with (
            patch.object(type(self.obj1), "at_pre_rename") as pre,
            patch.object(type(self.obj1), "at_post_rename") as post,
        ):
            self.obj1.key = same
            pre.assert_not_called()
            post.assert_not_called()


class TestTraverseRefactor(BaseEvenniaTest):
    """do_traverse routes through at_pre_traverse for veto semantics."""

    def test_at_pre_traverse_veto_routes_to_failed(self):
        # self.exit goes from room1 -> room2; self.char1 is in room1.
        with (
            patch.object(type(self.exit), "at_pre_traverse", return_value=False),
            patch.object(type(self.exit), "at_failed_traverse") as failed,
        ):
            self.exit.do_traverse(self.char1, self.room2)
        failed.assert_called_once()
        self.assertEqual(self.char1.location, self.room1)

    def test_at_pre_traverse_allow_lets_move_through(self):
        self.exit.do_traverse(self.char1, self.room2)
        self.assertEqual(self.char1.location, self.room2)


class TestAtPostLoadRename(BaseEvenniaTest):
    """at_post_load replaces at_init as the cache-load hook."""

    def test_hook_exists_on_typed_object(self):
        # The hook is defined; the rename did not silently drop it.
        self.assertTrue(callable(getattr(self.obj1, "at_post_load", None)))
        self.assertFalse(hasattr(type(self.obj1), "at_init"))


class TestVetoRule(BaseEvenniaTest):
    """is_veto: implicit None allows; explicit False (and other non-None
    falsy) vetoes. Applied across every vetoable pre-hook in the engine.
    """

    def test_at_pre_move_none_allows(self):
        # Regression for the implicit-None footgun: an override that does
        # side effects and forgets `return True` should not block the move.
        with patch.object(type(self.char1), "at_pre_move", return_value=None):
            ok = self.char1.move_to(self.room2, quiet=True)
        self.assertTrue(ok)
        self.assertEqual(self.char1.location, self.room2)

    def test_at_pre_move_false_vetoes(self):
        with patch.object(type(self.char1), "at_pre_move", return_value=False):
            ok = self.char1.move_to(self.room2, quiet=True)
        self.assertFalse(ok)
        self.assertEqual(self.char1.location, self.room1)

    def test_at_pre_leave_none_allows(self):
        with patch.object(type(self.room1), "at_pre_leave", return_value=None):
            ok = self.char1.move_to(self.room2, quiet=True)
        self.assertTrue(ok)

    def test_at_pre_arrive_none_allows(self):
        with patch.object(type(self.room2), "at_pre_arrive", return_value=None):
            ok = self.char1.move_to(self.room2, quiet=True)
        self.assertTrue(ok)

    def test_at_pre_traverse_none_allows(self):
        with patch.object(type(self.exit), "at_pre_traverse", return_value=None):
            self.exit.do_traverse(self.char1, self.room2)
        self.assertEqual(self.char1.location, self.room2)

    def test_at_pre_rename_none_allows(self):
        with patch.object(type(self.obj1), "at_pre_rename", return_value=None):
            self.obj1.key = "renamed_via_none"
        self.assertEqual(self.obj1.key, "renamed_via_none")

    def test_at_pre_rename_false_vetoes(self):
        old = self.obj1.key
        with patch.object(type(self.obj1), "at_pre_rename", return_value=False):
            self.obj1.key = "should_not_apply"
        self.assertEqual(self.obj1.key, old)

    def test_at_pre_move_zero_vetoes(self):
        # Defensive coverage: a botched expression returning 0 should
        # block, not silently allow.
        with patch.object(type(self.char1), "at_pre_move", return_value=0):
            ok = self.char1.move_to(self.room2, quiet=True)
        self.assertFalse(ok)


class TestAtPrePuppetVeto(BaseEvenniaTest):
    """at_pre_puppet is now vetoable: False blocks the puppet attach."""

    def test_false_blocks_puppet(self):
        # Detach the fixture session first, then re-attempt with a vetoing
        # at_pre_puppet override on the character. Restore location
        # because at_post_unpuppet clears it and we're skipping the
        # location-restoration logic in DefaultCharacter.at_pre_puppet.
        self.account.unpuppet_object(self.session)
        self.char1.location = self.room1
        with patch.object(type(self.char1), "at_pre_puppet", return_value=False):
            self.account.puppet_object(self.session, self.char1)
        # Puppet did not attach; session drives no body.
        self.assertIsNone(self.session.get_puppet())
        self.assertIsNone(self.session.bid)

    def test_none_allows_puppet(self):
        self.account.unpuppet_object(self.session)
        self.char1.location = self.room1
        with patch.object(type(self.char1), "at_pre_puppet", return_value=None):
            self.account.puppet_object(self.session, self.char1)
        self.assertEqual(self.session.get_puppet(), self.char1)


class TestTransformHookNoneRule(BaseEvenniaTest):
    """C-class transform hooks: None means "use original"; non-None falsy
    aborts; truthy replaces. Regression for the symmetric footgun fix.
    """

    def test_at_pre_say_none_uses_original(self):
        # An override that does side effects and forgets to return the
        # message must NOT silently kill the say.
        from evennia.commands.default.general import CmdSay

        spoken = []
        original_at_say = type(self.char1).at_say

        def capture_say(self, speech, **kwargs):
            spoken.append(speech)
            return original_at_say(self, speech, **kwargs)

        with (
            patch.object(type(self.char1), "at_pre_say", return_value=None),
            patch.object(type(self.char1), "at_say", capture_say),
        ):
            cmd = CmdSay()
            cmd.caller = self.char1
            cmd.args = "hello world"
            cmd.func()
        self.assertEqual(spoken, ["hello world"])

    def test_at_pre_say_false_aborts(self):
        from evennia.commands.default.general import CmdSay

        with (
            patch.object(type(self.char1), "at_pre_say", return_value=False),
            patch.object(type(self.char1), "at_say") as say_mock,
        ):
            cmd = CmdSay()
            cmd.caller = self.char1
            cmd.args = "hello world"
            cmd.func()
        say_mock.assert_not_called()

    def test_at_pre_say_truthy_replaces(self):
        from evennia.commands.default.general import CmdSay

        spoken = []

        def at_say_spy(self, speech, **kwargs):
            spoken.append(speech)

        with (
            patch.object(type(self.char1), "at_pre_say", return_value="REWRITTEN"),
            patch.object(type(self.char1), "at_say", at_say_spy),
        ):
            cmd = CmdSay()
            cmd.caller = self.char1
            cmd.args = "original"
            cmd.func()
        self.assertEqual(spoken, ["REWRITTEN"])


class TestContentGroupLabel(BaseEvenniaTest):
    """get_content_group_label drives the 'Characters:' / 'You see:' prefixes.
    Stock returns '' so the prefix is suppressed; overrides restore opinionated
    labels.
    """

    def test_stock_characters_has_no_prefix(self):
        self.char2.location = self.room1
        out = strip_ansi(self.room1.get_display_characters(self.char1))
        self.assertNotIn("Characters:", out)
        self.assertIn(self.char2.name, out)

    def test_stock_things_has_no_prefix(self):
        self.obj1.location = self.room1
        out = strip_ansi(self.room1.get_display_things(self.char1))
        self.assertNotIn("You see:", out)

    def test_override_label_restores_prefix(self):
        self.char2.location = self.room1
        with patch.object(
            type(self.room1),
            "get_content_group_label",
            return_value="Characters",
        ):
            out = strip_ansi(self.room1.get_display_characters(self.char1))
        self.assertIn("Characters:", out)

    def test_empty_group_returns_empty_string(self):
        # No characters present besides looker -> empty result with no orphan
        # whitespace from a stale label.
        for char in list(self.room1.contents):
            if char is not self.char1:
                char.location = None
        self.assertEqual(self.room1.get_display_characters(self.char1), "")


class TestSayTemplateHooks(BaseEvenniaTest):
    """Stock at_say templates are empty by default; overrides on the three
    template hooks restore opinionated language without re-implementing the
    broadcast machinery.
    """

    def test_stock_say_broadcasts_nothing(self):
        with (
            patch.object(self.char1, "msg") as self_msg,
            patch.object(self.room1, "msg_contents") as loc_msg,
        ):
            self.char1.location = self.room1
            self.char1.at_say("hello", msg_self=True)
        self_msg.assert_not_called()
        loc_msg.assert_not_called()

    def test_self_template_override_echoes(self):
        with (
            patch.object(
                type(self.char1),
                "get_say_template_self",
                return_value='You say, "{speech}"',
            ),
            patch.object(self.char1, "msg") as self_msg,
        ):
            self.char1.at_say("hi", msg_self=True)
        self_msg.assert_called_once()
        text = self_msg.call_args.kwargs.get("text") or self_msg.call_args.args[0]
        if isinstance(text, tuple):
            text = text[0]
        self.assertEqual(text, 'You say, "hi"')

    def test_location_template_override_broadcasts(self):
        self.char1.location = self.room1
        with (
            patch.object(
                type(self.char1),
                "get_say_template_location",
                return_value='{object} says, "{speech}"',
            ),
            patch.object(self.room1, "msg_contents") as loc_msg,
        ):
            self.char1.at_say("hi")
        loc_msg.assert_called_once()

    def test_whisper_receiver_template_override(self):
        from unittest.mock import MagicMock

        receiver = MagicMock()
        receiver.get_display_name.return_value = "Target"
        with patch.object(
            type(self.char1),
            "get_say_template_receivers",
            return_value='{object} whispers: "{speech}"',
        ):
            self.char1.at_say("secret", receivers=[receiver], whisper=True)
        receiver.msg.assert_called_once()


class TestExitsContentGroupLabel(BaseEvenniaTest):
    """`get_display_exits` routes its prefix through `get_content_group_label`.
    Stock returns "" so no "Exits:" label ships engine-side; overrides
    restore an opinionated prefix without re-implementing the renderer.
    """

    def test_stock_exits_has_no_prefix(self):
        DefaultExit.create("south", self.room1, self.room2, account=self.account)
        out = strip_ansi(self.room1.get_display_exits(self.char1))
        self.assertNotIn("Exits:", out)
        self.assertIn("south", out)

    def test_override_label_restores_prefix(self):
        DefaultExit.create("south", self.room1, self.room2, account=self.account)
        with patch.object(
            type(self.room1),
            "get_content_group_label",
            return_value="Exits",
        ):
            out = strip_ansi(self.room1.get_display_exits(self.char1))
        self.assertIn("Exits:", out)

    def test_empty_exits_returns_empty_string(self):
        # room2 has no exits in BaseEvenniaTest setup
        self.assertEqual(self.room2.get_display_exits(self.char1), "")


class TestSelfPronounHook(BaseEvenniaTest):
    """`{self}` in at_say mappings is resolved via `get_self_pronoun(looker)`."""

    def test_stock_self_pronoun_is_you(self):
        self.assertEqual(self.char1.get_self_pronoun(self.char1), "You")

    def test_self_echo_uses_pronoun_hook(self):
        with (
            patch.object(
                type(self.char1),
                "get_say_template_self",
                return_value='{self} say, "{speech}"',
            ),
            patch.object(
                type(self.char1),
                "get_self_pronoun",
                return_value="Ye",
            ),
            patch.object(self.char1, "msg") as self_msg,
        ):
            self.char1.at_say("ahoy", msg_self=True)
        text = self_msg.call_args.kwargs.get("text") or self_msg.call_args.args[0]
        if isinstance(text, tuple):
            text = text[0]
        self.assertEqual(text, 'Ye say, "ahoy"')

    def test_pronoun_hook_receives_per_receiver_looker(self):
        from unittest.mock import MagicMock

        seen = []

        def fake_pronoun(self, looker, **kwargs):
            seen.append(looker)
            return "Y"

        receiver_a = MagicMock()
        receiver_a.get_display_name.return_value = "A"
        receiver_b = MagicMock()
        receiver_b.get_display_name.return_value = "B"
        with (
            patch.object(type(self.char1), "get_self_pronoun", fake_pronoun),
            patch.object(
                type(self.char1),
                "get_say_template_receivers",
                return_value="{self} hears",
            ),
        ):
            self.char1.at_say("x", receivers=[receiver_a, receiver_b], whisper=True)
        # self pronoun resolved once per receiver, with that receiver as looker
        self.assertEqual(seen, [receiver_a, receiver_b])


class TestListEndsep(BaseEvenniaTest):
    """`list_endsep` class attr controls the trailing joiner used by the
    three appearance-mixin content-group helpers (exits, characters, things).
    """

    def test_default_endsep_used_for_exits(self):
        # iter_to_str only emits ", and" with >= 3 items
        DefaultExit.create("south", self.room1, self.room2, account=self.account)
        DefaultExit.create("north", self.room1, self.room2, account=self.account)
        out = strip_ansi(self.room1.get_display_exits(self.char1))
        self.assertIn(", and ", out)

    def test_override_endsep_used_for_exits(self):
        DefaultExit.create("south", self.room1, self.room2, account=self.account)
        DefaultExit.create("north", self.room1, self.room2, account=self.account)
        with patch.object(type(self.room1), "list_endsep", " | "):
            out = strip_ansi(self.room1.get_display_exits(self.char1))
        self.assertIn(" | ", out)
        self.assertNotIn(", and ", out)
