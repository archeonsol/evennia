"""Tests for console runtime introspection.

Two things matter here. First, that the spec sees every installed model
without a registration step -- that is the whole reason the Records lens beats
the admin's eight ModelAdmins. Second, that the models with a domain owner are
classified read-only *with a stated reason*, because a generic writer over any
of them corrupts something specific.

"""

from django.test import SimpleTestCase

from evennia.console import spec
from evennia.console.services import writable_models

#: Models the engine ships that Django admin never registered. Each one is a
#: concrete gap the console closes; if one disappears from the installed set,
#: this list is the record of what it was.
UNADMINISTERED = (
    "server.gameevent",
    "server.authorizationgrant",
    "server.authorizationscopelabel",
    "server.authorizationpolicyoverride",
    "server.authorizationprincipalstate",
    "server.authorizationauditevent",
    "server.enginejob",
    "server.sessionrecord",
    "server.sanction",
    "server.sanctionhit",
    "server.moderationflag",
)


class TestModelSpecs(SimpleTestCase):
    """The spec reflects the app registry, not a hand-maintained list."""

    def test_every_installed_model_is_covered(self):
        labels = {item.label for item in spec.model_specs()}
        for label in UNADMINISTERED:
            self.assertIn(label, labels, f"{label} missing from the console spec")

    def test_console_audit_model_is_present(self):
        self.assertIn("console.consoleauditevent", {s.label for s in spec.model_specs()})

    def test_specs_are_label_ordered(self):
        labels = [item.label for item in spec.model_specs()]
        self.assertEqual(labels, sorted(labels))

    def test_lookup_by_label(self):
        found = spec.get_model_spec("server.sanction")
        self.assertEqual(found.model_name, "sanction")
        self.assertEqual(found.app_label, "server")

    def test_unknown_label_fails_closed(self):
        with self.assertRaises(LookupError):
            spec.get_model_spec("nope.nothing")

    def test_fields_are_described(self):
        found = spec.get_model_spec("server.sanction")
        by_name = {field.name: field for field in found.fields}
        self.assertIn("subject_type", by_name)
        self.assertTrue(by_name["id"].primary_key)
        # Choice fields carry their choices so a form can be generated.
        self.assertTrue(by_name["level"].choices)

    def test_relations_are_labelled(self):
        found = spec.get_model_spec("server.moderationflag")
        relations = {field.name: field.relation for field in found.fields if field.relation}
        self.assertEqual(relations.get("sanction"), "server.sanction")

    def test_as_dict_is_json_shaped(self):
        payload = spec.get_model_spec("server.sanction").as_dict()
        self.assertIsInstance(payload["fields"], list)
        self.assertIsInstance(payload["default_ordering"], list)
        self.assertIsInstance(payload["writable"], bool)


class TestStorageClassification(SimpleTestCase):
    """Idmapper models are distinguished, because the read rules differ.

    A partial load cannot construct an uncached ``SharedMemoryModel``, so the
    generic list view must never use ``.only()`` or ``.defer()`` against one.
    The classification is what lets the lens know.
    """

    def test_typeclass_models_are_idmapper(self):
        for label in ("objects.objectdb", "accounts.accountdb", "scripts.scriptdb"):
            self.assertEqual(spec.get_model_spec(label).storage, "idmapper")

    def test_substrate_models_are_plain(self):
        for label in ("server.sanction", "server.enginejob", "console.consoleauditevent"):
            self.assertEqual(spec.get_model_spec(label).storage, "plain")


class TestWritePolicy(SimpleTestCase):
    """Writes are allowlisted; everything else is read plus domain actions."""

    def test_only_adapted_models_are_writable(self):
        adapters = writable_models()
        for item in spec.model_specs():
            self.assertEqual(
                item.writable,
                item.label in adapters,
                f"{item.label} writability disagrees with the adapter registry",
            )

    def test_the_seven_adapted_models(self):
        self.assertEqual(
            writable_models(),
            frozenset(
                {
                    "accounts.accountdb",
                    "objects.objectdb",
                    "comms.channeldb",
                    "comms.msg",
                    "scripts.scriptdb",
                    "help.helpentry",
                    "server.serverconfig",
                }
            ),
        )

    def test_no_unadministered_model_gets_a_generic_writer(self):
        for label in UNADMINISTERED:
            item = spec.get_model_spec(label)
            self.assertTrue(item.read_only, f"{label} must not be generically writable")

    def test_domain_owned_models_state_their_owner(self):
        for label, reason in spec.DOMAIN_OWNED.items():
            item = spec.get_model_spec(label)
            self.assertTrue(item.read_only, f"{label} is marked writable but has a domain owner")
            self.assertEqual(item.write_via, reason)
            self.assertTrue(reason.strip(), f"{label} has an empty reason")

    def test_tag_is_read_only(self):
        # One shared Tag row may be cached by many owners; the mutation bridge
        # excludes it deliberately. Tags are edited through the owning object.
        item = spec.get_model_spec("typeclasses.tag")
        self.assertTrue(item.read_only)
        self.assertIn("handler", item.write_via)

    def test_console_audit_trail_has_no_write_path(self):
        item = spec.get_model_spec("console.consoleauditevent")
        self.assertTrue(item.read_only)
        self.assertIn("append-only", item.write_via)

    def test_sanction_reason_names_the_hash_chain(self):
        self.assertIn("hash chain", spec.get_model_spec("server.sanction").write_via)
