from evennia import DefaultObject, DefaultRoom
from evennia.accounts.models import AccountDB
from evennia.objects.models import ObjectDB
from evennia.scripts.models import ScriptDB
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


class TestDbrefMiss(EvenniaTest):
    """A dbref that names nothing finds nothing, and is not searched for as a name."""

    def test_a_script_is_found_by_its_dbref(self):
        script, errors = DefaultScript.create("a-script")
        found = ScriptDB.objects.search_script(f"#{script.id}")
        self.assertEqual([found_script.id for found_script in found], [script.id], errors)

    def test_a_missing_script_dbref_costs_one_query(self):
        with self.assertNumQueries(1):
            self.assertEqual(list(ScriptDB.objects.search_script("#99999999")), [])

    def test_a_script_keyed_like_a_dbref_is_not_found_by_that_text(self):
        DefaultScript.create("#99999997")
        self.assertEqual(list(ScriptDB.objects.search_script("#99999997")), [])

    def test_an_account_is_found_by_its_dbref(self):
        found = AccountDB.objects.search_account(f"#{self.account.id}")
        self.assertEqual([acct.id for acct in found], [self.account.id])

    def test_a_missing_account_dbref_costs_one_query(self):
        with self.assertNumQueries(1):
            self.assertEqual(list(AccountDB.objects.search_account("#99999999")), [])
