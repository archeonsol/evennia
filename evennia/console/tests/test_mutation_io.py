"""Falsification tests for the stock admin owner-mutation protocol."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections
from django.test import AsyncClient, SimpleTestCase, TransactionTestCase
from django.urls import reverse

from evennia.accounts.models import AccountDB
from evennia.comms.models import ChannelDB, Msg
from evennia.console.services import (
    AdminDeleteRequest,
    AdminDeleteResult,
    AdminMutationRequest,
    TagDelta,
    delete_admin,
    freeze_plain,
    mutate_admin,
)
from evennia.help.models import HelpEntry
from evennia.objects.models import ObjectDB
from evennia.scripts.models import ScriptDB
from evennia.server.models import AuthorizationGrant, ServerConfig
from evennia.utils import class_from_module, clock
from evennia.utils.create import create_account, create_object
from evennia.utils.test_resources import BaseEvenniaTest
from evennia.web.admin.objects import ObjectAdmin


class AdminCodecTest(SimpleTestCase):
    """Reject executable or unbounded payload shapes before bridging."""

    def test_hostile_deepcopy_protocol_is_never_invoked(self):
        class Hostile:
            def __deepcopy__(self, memo):
                raise AssertionError("arbitrary copy protocol ran")

        with self.assertRaisesRegex(ValueError, "unsupported"):
            freeze_plain(Hostile())

    def test_cycles_and_nonfinite_numbers_reject(self):
        cyclic = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(ValueError, "cycle"):
            freeze_plain(cyclic)
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            freeze_plain(float("nan"))

    def test_secret_request_repr_hides_password(self):
        request = AdminMutationRequest(1, "accounts.accountdb", None, (), (), (), "secret")
        self.assertNotIn("secret", repr(request))

    def test_save_form_does_not_call_custom_model_form_save(self):
        model_admin = ObjectAdmin(ObjectDB, admin.site)
        instance = object()
        form = SimpleNamespace(instance=instance, save=Mock())
        self.assertIs(model_admin.save_form(None, form, False), instance)
        form.save.assert_not_called()

    def test_protocol_rejects_hostile_relation_ids_before_authorization_sql(self):
        class HostileInt(int):
            def __int__(self):
                raise AssertionError("attacker conversion ran")

        request = AdminMutationRequest(
            1,
            "comms.channeldb",
            1,
            (),
            (("db_account_subscriptions", (HostileInt(1),)),),
            (),
        )
        with patch("evennia.console.services._fresh_actor") as fresh_actor:
            result = mutate_admin(request)
        self.assertEqual(result.status, "conflict")
        fresh_actor.assert_not_called()

    def test_protocol_rejects_wrong_field_codec_before_authorization_sql(self):
        request = AdminMutationRequest(
            1,
            "objects.objectdb",
            1,
            (("db_key", freeze_plain({"not": "a string"})),),
            (),
            (),
        )
        with patch("evennia.console.services._fresh_actor") as fresh_actor:
            result = mutate_admin(request)
        self.assertEqual(result.status, "conflict")
        fresh_actor.assert_not_called()

    def test_fk_existence_is_not_queried_before_fresh_authorization(self):
        request = AdminMutationRequest(
            1,
            "objects.objectdb",
            1,
            (("db_home", 999),),
            (),
            (),
        )
        with (
            patch(
                "evennia.console.services._fresh_actor",
                side_effect=PermissionDenied("revoked"),
            ),
            patch.object(ObjectDB.objects, "filter", side_effect=AssertionError("FK SQL ran")),
            self.assertRaises(PermissionDenied),
        ):
            mutate_admin(request)

    def test_builder_rejects_unknown_cleaned_fields(self):
        model_admin = ObjectAdmin(ObjectDB, admin.site)
        request = SimpleNamespace(user=SimpleNamespace(pk=1))
        form = SimpleNamespace(
            cleaned_data={"db_key": "safe", "unexpected": "unsafe"},
            instance=SimpleNamespace(pk=None),
        )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            model_admin._mutation_request(request, form, (), False)


class AdminMutationServiceTest(BaseEvenniaTest):
    """The owner service mutates canonical state and returns plain results."""

    def setUp(self):
        super().setUp()
        self.account.is_staff = True
        self.account.is_superuser = True
        self.account.save(update_fields=["is_staff", "is_superuser"])

    def test_object_change_runs_complete_owner_operation(self):
        request = AdminMutationRequest(
            actor_id=self.account.pk,
            model_label="objects.objectdb",
            object_id=self.obj1.pk,
            concrete=(("db_key", "Owner changed"),),
            relations=(),
            tags=(),
        )

        result = mutate_admin(request)

        self.assertEqual(result.status, "changed")
        self.obj1.refresh_from_db()
        self.assertEqual(self.obj1.db_key, "Owner changed")

    def test_revoked_staff_cannot_mutate_even_with_primed_permissions(self):
        self.account.has_perm("objects.change_objectdb")
        self.account.is_staff = False
        self.account.save(update_fields=["is_staff"])
        request = AdminMutationRequest(
            self.account.pk,
            "objects.objectdb",
            self.obj1.pk,
            (("db_key", "Forbidden"),),
            (),
            (),
        )

        with self.assertRaisesMessage(Exception, "revoked"):
            mutate_admin(request)

        self.obj1.refresh_from_db()
        self.assertNotEqual(self.obj1.db_key, "Forbidden")

    def test_non_superuser_can_change_email_without_changing_authority(self):
        actor = self.account2
        actor.is_staff = True
        actor.is_superuser = False
        actor.save(update_fields=["is_staff", "is_superuser"])
        actor.user_permissions.add(Permission.objects.get(codename="change_accountdb"))
        target = create_account("ordinary-account-edit", "before@example.com", None)
        request = AdminMutationRequest(
            actor.pk,
            "accounts.accountdb",
            target.pk,
            (
                ("email", "after@example.com"),
                ("is_staff", False),
                ("is_superuser", False),
            ),
            (("groups", ()), ("user_permissions", ())),
            (),
        )

        result = mutate_admin(request)

        self.assertEqual(result.status, "changed", result)
        target.refresh_from_db()
        self.assertEqual(target.email, "after@example.com")

    def test_remaining_creation_adapters_return_exact_durable_ids(self):
        requests = (
            AdminMutationRequest(
                self.account.pk,
                "objects.objectdb",
                None,
                (
                    ("db_key", "Admin-created object"),
                    ("db_typeclass_path", settings.BASE_OBJECT_TYPECLASS),
                    ("db_home", self.room1.pk),
                ),
                (),
                (),
            ),
            AdminMutationRequest(
                self.account.pk,
                "comms.channeldb",
                None,
                (("db_key", "admin-created-channel"),),
                (),
                (),
            ),
            AdminMutationRequest(
                self.account.pk,
                "scripts.scriptdb",
                None,
                (("db_key", "admin-created-script"),),
                (),
                (),
            ),
            AdminMutationRequest(
                self.account.pk,
                "help.helpentry",
                None,
                (
                    ("db_key", "admin-created-help"),
                    ("db_entrytext", "body"),
                    ("db_help_category", "General"),
                ),
                (),
                (),
            ),
            AdminMutationRequest(
                self.account.pk,
                "comms.msg",
                None,
                (("db_message", "admin-created-message"),),
                (("db_sender_accounts", (self.account.pk,)),),
                (),
            ),
            AdminMutationRequest(
                self.account.pk,
                "server.serverconfig",
                None,
                (
                    ("db_key", "admin-created-config"),
                    ("db_value", freeze_plain({"enabled": [True, 3]})),
                ),
                (),
                (),
            ),
            AdminMutationRequest(
                self.account.pk,
                "server.serverconfig",
                None,
                (
                    ("db_key", "admin-created-null-config"),
                    ("db_value", None),
                ),
                (),
                (),
            ),
        )

        for request in requests:
            with self.subTest(model=request.model_label):
                result = mutate_admin(request)
                self.assertEqual(result.status, "created", result)
                model = {
                    "objects.objectdb": ObjectDB,
                    "comms.channeldb": ChannelDB,
                    "scripts.scriptdb": ScriptDB,
                    "help.helpentry": HelpEntry,
                    "comms.msg": Msg,
                    "server.serverconfig": ServerConfig,
                }[request.model_label]
                self.assertTrue(model.objects.filter(pk=result.object_id).exists())
        self.assertIsNone(ServerConfig.objects.get(db_key="admin-created-null-config").value)

    def test_channel_relations_and_tags_use_live_handlers(self):
        channel = ChannelDB.objects.create_channel("admin-relations-channel")
        request = AdminMutationRequest(
            self.account.pk,
            "comms.channeldb",
            channel.pk,
            (),
            (
                ("db_account_subscriptions", (self.account.pk,)),
                ("db_object_subscriptions", (self.obj1.pk,)),
            ),
            (TagDelta(None, ("admin-tag", "tests", None, "data")),),
        )

        result = mutate_admin(request)

        self.assertEqual(result.status, "changed", result)
        self.assertTrue(channel.subscriptions.has(self.account))
        self.assertTrue(channel.subscriptions.has(self.obj1))
        self.assertTrue(channel.tags.has("admin-tag", category="tests"))

    def test_message_participant_changes_reconcile_capability_grants(self):
        message = Msg.objects.create_message(self.account, "capability message")
        message_ref = message.authorization_resource_ref()

        result = mutate_admin(
            AdminMutationRequest(
                self.account.pk,
                "comms.msg",
                message.pk,
                (),
                (("db_sender_accounts", (self.account2.pk,)),),
                (),
            )
        )

        self.assertEqual(result.status, "changed", result)
        active = AuthorizationGrant.objects.filter(
            scope_kind="resource",
            scope_key=message_ref,
            revoked_at__isnull=True,
        )
        self.assertFalse(active.filter(principal_ref=f"account:{self.account.pk}").exists())
        self.assertEqual(
            set(
                active.filter(principal_ref=f"account:{self.account2.pk}").values_list(
                    "capability", flat=True
                )
            ),
            {"engine.message.read", "engine.message.edit", "engine.message.delete"},
        )

    def test_post_load_recovery_fault_returns_verified_partial(self):
        request = AdminMutationRequest(
            self.account.pk,
            "objects.objectdb",
            self.obj1.pk,
            (("db_key", "durable-before-recovery-fault"),),
            (),
            (),
        )
        with patch.object(
            self.obj1.__class__,
            "at_post_load",
            side_effect=RuntimeError("repeatable post-load fault"),
        ):
            result = mutate_admin(request)
        self.assertEqual(result.status, "recovery_required", result)
        self.assertIn(self.obj1.pk, result.recovery_ids)
        self.assertEqual(
            ObjectDB.objects.filter(pk=self.obj1.pk).values_list("db_key", flat=True).get(),
            "durable-before-recovery-fault",
        )

    def test_delete_fault_after_sql_is_reported_as_deleted(self):
        doomed = create_object(key="durable delete before fault", home=self.room1)
        doomed_id = doomed.pk
        original_delete = doomed.__class__.delete

        def delete_then_fault(instance, *args, **kwargs):
            original_delete(instance, *args, **kwargs)
            raise RuntimeError("post-delete retirement fault")

        with patch.object(doomed.__class__, "delete", new=delete_then_fault):
            result = delete_admin(
                AdminDeleteRequest(
                    self.account.pk,
                    "objects.objectdb",
                    (doomed_id,),
                )
            )
        self.assertIn(doomed_id, result.deleted_ids)
        self.assertFalse(ObjectDB.objects.filter(pk=doomed_id).exists())

    def test_false_delete_return_cannot_override_fresh_absence(self):
        doomed = create_object(key="false return after delete", home=self.room1)
        doomed_id = doomed.pk
        original_delete = doomed.__class__.delete

        def delete_then_return_false(instance, *args, **kwargs):
            original_delete(instance, *args, **kwargs)
            return False

        with patch.object(doomed.__class__, "delete", new=delete_then_return_false):
            result = delete_admin(
                AdminDeleteRequest(
                    self.account.pk,
                    "objects.objectdb",
                    (doomed_id,),
                )
            )
        self.assertIn(doomed_id, result.deleted_ids)
        self.assertNotIn(doomed_id, result.vetoed_ids)

    def test_typeclass_changes_preserve_canonical_identity(self):
        channel = ChannelDB.objects.create_channel("admin-reclass-channel")
        script = ScriptDB.objects.create_script(key="admin-reclass-script")
        cases = (
            (
                self.obj1,
                "objects.objectdb",
                "evennia.objects.objects.DefaultObject",
            ),
            (
                channel,
                "comms.channeldb",
                "evennia.comms.comms.DefaultChannel",
            ),
            (
                script,
                "scripts.scriptdb",
                "evennia.scripts.scripts.DefaultScript",
            ),
            (
                self.account2,
                "accounts.accountdb",
                "evennia.accounts.accounts.DefaultAccount",
            ),
        )
        for instance, label, typeclass_path in cases:
            with self.subTest(model=label):
                result = mutate_admin(
                    AdminMutationRequest(
                        self.account.pk,
                        label,
                        instance.pk,
                        (("db_typeclass_path", typeclass_path),),
                        (),
                        (),
                    )
                )
                self.assertEqual(result.status, "changed", result)
                model = {
                    "objects.objectdb": ObjectDB,
                    "comms.channeldb": ChannelDB,
                    "scripts.scriptdb": ScriptDB,
                    "accounts.accountdb": AccountDB,
                }[label]
                self.assertIs(model.objects.get(pk=instance.pk), instance)
                self.assertEqual(instance.db_typeclass_path, typeclass_path)
                self.assertIs(instance.__class__, class_from_module(typeclass_path))


class CreationRecorderSeamTest(BaseEvenniaTest):
    """Creation helpers expose exact partial instances before late failures."""

    def test_channel_and_script_record_before_post_create_signal(self):
        channel_recorder = SimpleNamespace(record_channel=Mock())
        with (
            patch(
                "evennia.comms.managers.signals.SIGNAL_CHANNEL_POST_CREATE.send",
                side_effect=RuntimeError("late channel fault"),
            ),
            self.assertRaisesRegex(RuntimeError, "late channel fault"),
        ):
            ChannelDB.objects.create_channel(
                "recorded-channel", _creation_recorder=channel_recorder
            )
        channel = channel_recorder.record_channel.call_args.args[0]
        self.assertIsNotNone(channel.pk)
        self.assertTrue(ChannelDB.objects.filter(pk=channel.pk).exists())

        script_recorder = SimpleNamespace(record_script=Mock())
        with (
            patch(
                "evennia.scripts.manager.signals.SIGNAL_SCRIPT_POST_CREATE.send",
                side_effect=RuntimeError("late script fault"),
            ),
            self.assertRaisesRegex(RuntimeError, "late script fault"),
        ):
            ScriptDB.objects.create_script(
                key="recorded-script", _creation_recorder=script_recorder
            )
        script = script_recorder.record_script.call_args.args[0]
        self.assertIsNotNone(script.pk)
        self.assertTrue(ScriptDB.objects.filter(pk=script.pk).exists())

    def test_message_and_help_record_exact_partial_rows(self):
        message_recorder = SimpleNamespace(record_message=Mock())
        with (
            patch(
                "evennia.authorization.storage.grant_capability",
                side_effect=RuntimeError("late message fault"),
            ),
            self.assertRaisesRegex(RuntimeError, "late message fault"),
        ):
            Msg.objects.create_message(
                self.account,
                "recorded message",
                _creation_recorder=message_recorder,
            )
        message = message_recorder.record_message.call_args.args[0]
        self.assertIsNotNone(message.pk)
        self.assertTrue(Msg.objects.filter(pk=message.pk).exists())

        help_recorder = SimpleNamespace(record_help_entry=Mock())
        with patch(
            "evennia.help.manager.signals.SIGNAL_HELPENTRY_POST_CREATE.send",
            side_effect=RuntimeError("late help fault"),
        ):
            result = HelpEntry.objects.create_help(
                "recorded-help",
                "body",
                _creation_recorder=help_recorder,
            )
        self.assertIsNone(result)
        entry = help_recorder.record_help_entry.call_args.args[0]
        self.assertIsNotNone(entry.pk)
        self.assertTrue(HelpEntry.objects.filter(pk=entry.pk).exists())


class AdminMutationASGITest(TransactionTestCase):
    """A real ASGI worker dispatches one complete admin change to the owner."""

    def setUp(self):
        super().setUp()
        self.actor = create_account(
            "Mutation admin",
            None,
            password="Admin-password-48!",
            is_superuser=True,
        )
        self.actor.is_staff = True
        self.actor.save(update_fields=["is_staff"])
        self.room = create_object(key="Mutation room", nohome=True)
        self.obj = create_object(key="Before mutation", home=self.room, location=self.room)
        self.doomed = create_object(key="Delete through owner", home=self.room)

    def tearDown(self):
        close_old_connections()
        ObjectDB.flush_instance_cache(force=True)
        super().tearDown()

    @staticmethod
    def _tag_management(prefix):
        return {
            f"{prefix}-TOTAL_FORMS": "0",
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }

    async def test_remaining_admin_add_forms_bridge_complete_payloads(self):
        client = AsyncClient()
        await client.aforce_login(self.actor)
        loop = asyncio.get_running_loop()
        previous = clock.get_bound_loop()
        previous_thread = clock.get_loop_thread_id()
        clock.bind_loop(loop)
        requests = (
            (
                "admin:comms_channeldb_add",
                {
                    "db_key": "asgi-created-channel",
                    "db_lock_storage": "",
                    **self._tag_management("ChannelDB_db_tags"),
                },
            ),
            (
                "admin:scripts_scriptdb_add",
                {
                    "db_key": "asgi-created-script",
                    "db_typeclass_path": settings.BASE_SCRIPT_TYPECLASS,
                    "db_persistent": "on",
                    "db_obj": "",
                    "db_lock_storage": "",
                    **self._tag_management("ScriptDB_db_tags"),
                },
            ),
            (
                "admin:help_helpentry_add",
                {
                    "db_key": "asgi-created-help",
                    "db_help_category": "General",
                    "db_entrytext": "body",
                    "db_lock_storage": "view:all()",
                    **self._tag_management("HelpEntry_db_tags"),
                },
            ),
            (
                "admin:comms_msg_add",
                {
                    "db_message": "asgi-created-message",
                    "db_header": "",
                    **self._tag_management("Msg_db_tags"),
                },
            ),
            (
                "admin:server_serverconfig_add",
                {
                    "db_key": "asgi-created-config",
                    "db_value": "{'enabled': True}",
                },
            ),
        )
        responses = []
        try:
            for url_name, payload in requests:
                responses.append(await client.post(reverse(url_name), {**payload, "_save": "Save"}))
            self.assertEqual(
                [response.status_code for response in responses],
                [302] * len(requests),
                [response.content.decode() for response in responses],
            )
            object_ids = (
                ChannelDB.objects.get(db_key="asgi-created-channel").pk,
                ScriptDB.objects.get(db_key="asgi-created-script").pk,
                HelpEntry.objects.get(db_key="asgi-created-help").pk,
                Msg.objects.get(db_message="asgi-created-message").pk,
                ServerConfig.objects.get(db_key="asgi-created-config").pk,
            )
            changes = (
                (
                    "admin:comms_channeldb_change",
                    {**requests[0][1], "db_key": "asgi-changed-channel"},
                ),
                (
                    "admin:scripts_scriptdb_change",
                    {**requests[1][1], "db_key": "asgi-changed-script"},
                ),
                (
                    "admin:help_helpentry_change",
                    {**requests[2][1], "db_key": "asgi-changed-help"},
                ),
                (
                    "admin:comms_msg_change",
                    {**requests[3][1], "db_message": "asgi-changed-message"},
                ),
                (
                    "admin:server_serverconfig_change",
                    {**requests[4][1], "db_value": "{'enabled': False}"},
                ),
            )
            for (url_name, payload), object_id in zip(changes, object_ids, strict=True):
                responses.append(
                    await client.post(
                        reverse(url_name, args=[object_id]),
                        {**payload, "_save": "Save"},
                    )
                )
        finally:
            clock._main_loop = previous
            clock._loop_thread_id = previous_thread
            await sync_to_async(close_old_connections, thread_sensitive=True)()
        self.assertEqual(
            [response.status_code for response in responses],
            [302] * (len(requests) * 2),
            [response.content.decode() for response in responses],
        )
        self.assertTrue(ChannelDB.objects.filter(db_key="asgi-changed-channel").exists())
        self.assertTrue(ScriptDB.objects.filter(db_key="asgi-changed-script").exists())
        self.assertTrue(HelpEntry.objects.filter(db_key="asgi-changed-help").exists())
        self.assertTrue(Msg.objects.filter(db_message="asgi-changed-message").exists())
        self.assertEqual(
            ServerConfig.objects.get(db_key="asgi-created-config").value,
            {"enabled": False},
        )

    async def test_object_change_crosses_bound_loop_without_worker_transaction(self):
        client = AsyncClient()
        await client.aforce_login(self.actor)
        loop = asyncio.get_running_loop()
        previous = clock.get_bound_loop()
        previous_thread = clock.get_loop_thread_id()
        clock.bind_loop(loop)
        prefix = "ObjectDB_db_tags"
        try:
            response = await client.post(
                reverse("admin:objects_objectdb_change", args=[self.obj.pk]),
                {
                    "db_key": "After mutation",
                    "db_typeclass_path": self.obj.db_typeclass_path,
                    "db_location": self.room.pk,
                    "db_home": self.room.pk,
                    "db_destination": "",
                    "db_account": "",
                    "db_cmdset_storage": self.obj.db_cmdset_storage or "",
                    "db_lock_storage": self.obj.db_lock_storage or "",
                    f"{prefix}-TOTAL_FORMS": "0",
                    f"{prefix}-INITIAL_FORMS": "0",
                    f"{prefix}-MIN_NUM_FORMS": "0",
                    f"{prefix}-MAX_NUM_FORMS": "1000",
                    "_save": "Save",
                },
            )
            owner_obj = ObjectDB.objects.get(pk=self.obj.pk)
        finally:
            clock._main_loop = previous
            clock._loop_thread_id = previous_thread
            await sync_to_async(close_old_connections, thread_sensitive=True)()
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertEqual(owner_obj.db_key, "After mutation")
        self.assertFalse(owner_obj._state.adding)

    async def test_account_add_runs_password_lifecycle_only_on_owner(self):
        client = AsyncClient()
        await client.aforce_login(self.actor)
        loop = asyncio.get_running_loop()
        owner_thread = threading.get_ident()
        previous = clock.get_bound_loop()
        previous_thread = clock.get_loop_thread_id()
        clock.bind_loop(loop)
        hook_threads = []
        prefix = "AccountDB_db_tags"

        AccountTypeclass = class_from_module(settings.BASE_ACCOUNT_TYPECLASS)

        def record_hook(_account):
            hook_threads.append(threading.get_ident())

        try:
            with patch(
                f"{AccountTypeclass.__module__}.{AccountTypeclass.__name__}.at_post_password_change",
                new=record_hook,
            ):
                response = await client.post(
                    reverse("admin:accounts_accountdb_add"),
                    {
                        "username": "asgicreatedaccount",
                        "password1": "Created-password-58!",
                        "password2": "Created-password-58!",
                        "email": "created@example.com",
                        f"{prefix}-TOTAL_FORMS": "0",
                        f"{prefix}-INITIAL_FORMS": "0",
                        f"{prefix}-MIN_NUM_FORMS": "0",
                        f"{prefix}-MAX_NUM_FORMS": "1000",
                        "_save": "Save",
                    },
                )
        finally:
            clock._main_loop = previous
            clock._loop_thread_id = previous_thread
            await sync_to_async(close_old_connections, thread_sensitive=True)()
        self.assertEqual(response.status_code, 302, response.content.decode())
        created = AccountDB.objects.get(username="asgicreatedaccount")
        self.assertEqual(hook_threads, [owner_thread])
        self.assertTrue(created.check_password("Created-password-58!"))

    async def test_single_delete_mutates_before_audit_and_survives_audit_failure(self):
        doomed_id = self.doomed.pk
        client = AsyncClient()
        await client.aforce_login(self.actor)
        loop = asyncio.get_running_loop()
        previous = clock.get_bound_loop()
        previous_thread = clock.get_loop_thread_id()
        clock.bind_loop(loop)

        def unsafe_string(_instance):
            raise AssertionError("typeclass string hook ran during delete")

        try:
            with (
                patch.object(
                    LogEntry.objects,
                    "create",
                    side_effect=RuntimeError("audit unavailable"),
                ),
                patch.object(self.doomed.__class__, "__str__", new=unsafe_string),
            ):
                response = await client.post(
                    reverse("admin:objects_objectdb_delete", args=[doomed_id]),
                    {"post": "yes"},
                )
            exists = ObjectDB.objects.filter(pk=doomed_id).exists()
        finally:
            clock._main_loop = previous
            clock._loop_thread_id = previous_thread
            await sync_to_async(close_old_connections, thread_sensitive=True)()
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertFalse(exists)

    async def test_single_delete_maps_owner_missing_race_to_404(self):
        client = AsyncClient()
        await client.aforce_login(self.actor)
        loop = asyncio.get_running_loop()
        previous = clock.get_bound_loop()
        previous_thread = clock.get_loop_thread_id()
        clock.bind_loop(loop)
        try:
            with patch(
                "evennia.web.admin.mixins.delete_admin",
                return_value=AdminDeleteResult(
                    "partial",
                    missing_ids=(self.doomed.pk,),
                ),
            ):
                response = await client.post(
                    reverse("admin:objects_objectdb_delete", args=[self.doomed.pk]),
                    {"post": "yes"},
                )
        finally:
            clock._main_loop = previous
            clock._loop_thread_id = previous_thread
            await sync_to_async(close_old_connections, thread_sensitive=True)()
        self.assertEqual(response.status_code, 404)
