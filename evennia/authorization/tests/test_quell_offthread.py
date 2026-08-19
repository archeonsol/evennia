"""Authorization must resolve off the IO thread.

``load_grants`` reads the principal's ``_quell`` attribute to decide whether
authority is suppressed. Attribute state is IO-owner-owned, so that read raised
on a web worker -- and the exception travelled past the caller's
``AttributeError`` guard into ``has_capability``'s blanket except, where it
became "this principal holds no capabilities at all".

The result was not fail-closed, it was fail-broken: every authorization
decision made from a non-owner thread came back False, silently, for a reason
that had nothing to do with authorization. It surfaced only when the console's
streaming endpoint became the first thing to authorize from a genuinely
non-owner thread.

The handler failure is injected by patching the handler class's ``get``, which
the context manager restores. Patching the typeclass leaks into whichever test
runs next, and the handler cannot be replaced on the instance at all -- Evennia
guards that assignment.

"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from evennia.authorization.storage import _read_quell_document, load_grants
from evennia.typeclasses.jsonb_handler import AttributeUpdateUnavailable

#: The stored shape of a set quell flag: default category, data section, key.
QUELL_DOCUMENT = {"~": {"_d": {"_quell": True}}}


class QuellTestCase(TestCase):
    """One ordinary account."""

    def setUp(self):
        self.account = get_user_model().objects.create(username="quell-subject", is_active=True)

    def store_quell(self):
        """Write the flag straight to the column the fallback reads."""
        get_user_model().objects.filter(pk=self.account.pk).update(db_attrs=QUELL_DOCUMENT)

    def handler_unavailable(self):
        """Make the attribute-handler read raise, as it does off-thread."""
        return patch.object(
            type(self.account.attributes),
            "get",
            side_effect=AttributeUpdateUnavailable("must run on the Evennia IO thread"),
        )


class TestDocumentPath(QuellTestCase):
    """The fallback read, straight from the principal's own column."""

    def test_reads_a_stored_flag(self):
        self.store_quell()
        self.assertTrue(_read_quell_document(self.account))

    def test_an_absent_flag_reads_false(self):
        self.assertFalse(_read_quell_document(self.account))

    def test_a_principal_without_a_primary_key_reads_false(self):
        class _Bare:
            pk = None

        self.assertFalse(_read_quell_document(_Bare()))

    def test_a_malformed_document_reads_false(self):
        for document in ({"~": "not a dict"}, {"~": {"_d": "not a dict"}}, {}):
            get_user_model().objects.filter(pk=self.account.pk).update(db_attrs=document)
            self.assertFalse(_read_quell_document(self.account), document)


class TestOffThread(QuellTestCase):
    """The regression: an unavailable handler must not deny authority."""

    def test_load_grants_does_not_raise(self):
        # Before the fix this propagated AttributeUpdateUnavailable, which
        # has_capability swallowed into "holds nothing".
        with self.handler_unavailable():
            self.assertIsNotNone(load_grants(self.account))

    def test_load_grants_reads_the_stored_quell_instead(self):
        # The fallback must not make a suppressed principal look
        # authoritative, so the flag still has to be seen.
        self.store_quell()
        self.assertTrue(_read_quell_document(self.account))
        with self.handler_unavailable():
            self.assertIsNotNone(load_grants(self.account))

    # Deliberately no test writes an attribute here. Attribute state is held in
    # a process-wide cache keyed by primary key, so a write inside a rolled-back
    # test still leaks a quell into whichever later test reuses that key -- as
    # this file did, breaking two unrelated recovery-grant tests.
