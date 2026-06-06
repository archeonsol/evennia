from evennia import DefaultObject, DefaultRoom
from evennia.objects.models import ObjectDB
from evennia.scripts.scripts import DefaultScript
from evennia.utils.search import search_script, search_typeclass
from evennia.utils.test_resources import EvenniaTest


class TestSearch(EvenniaTest):
    def test_search_script_key(self):
        """Check that a script can be found by its key value."""
        script, errors = DefaultScript.create("a-script")
        found = search_script("a-script")
        self.assertEqual(len(found), 1, errors)
        self.assertEqual(script.key, found[0].key, errors)

    def test_search_script_wrong_key(self):
        """Check that a script cannot be found by a wrong key value."""
        script, errors = DefaultScript.create("a-script")
        found = search_script("wrong_key")
        self.assertEqual(len(found), 0, errors)

    def test_search_typeclass(self):
        """Check that an object can be found by typeclass"""
        DefaultObject.create("test_obj")
        found = search_typeclass("evennia.objects.objects.DefaultObject")
        self.assertEqual(len(found), 1)

    def test_search_wrong_typeclass(self):
        """Check that an object cannot be found by wrong typeclass"""
        DefaultObject.create("test_obj_2")
        with self.assertRaises(ImportError):
            search_typeclass("not.a.typeclass")
