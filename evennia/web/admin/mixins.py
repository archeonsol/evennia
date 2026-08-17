"""Django-admin orchestration for IO-owner game-state mutation services."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.actions import delete_selected
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters

from evennia.web.utils.io import (
    IOThreadCallIndeterminate,
    IOThreadCallTimeout,
    IOThreadCallUnavailable,
    release_worker_db_connections,
    run_on_io_thread,
)

from .io import (
    AdminDeleteRequest,
    AdminMutationRequest,
    TagDelta,
    admin_spec,
    delete_admin,
    freeze_plain,
    mutate_admin,
)
from .tags import TagFormSet

_AUXILIARY_FORM_FIELDS = {
    "accounts.accountdb": frozenset(
        {
            "password",
            "password1",
            "password2",
            "usable_password",
            "set_usable_password",
        }
    )
}


def _bridge_response(status, message, *, retryable=False):
    """Build one explicit mutation response."""
    headers = {"X-Evennia-Retryable": "true" if retryable else "false"}
    if retryable:
        headers["Retry-After"] = "1"
    return HttpResponse(message, status=status, headers=headers)


class _AdminOutcome(Exception):
    """Carry a typed owner result out of Django's change-form orchestration."""

    def __init__(self, result):
        super().__init__(result.status)
        self.result = result


class OwnerSafeModelAdminMixin:
    """Stage validated admin forms and persist them only on the IO owner."""

    def save_form(self, request, form, change):
        """Avoid custom ModelForm.save hooks before the owner bridge."""
        return form.instance

    @method_decorator([sensitive_post_parameters(), csrf_protect])
    def add_view(self, request, form_url="", extra_context=None):
        """Bypass UserAdmin's separate worker transaction wrapper."""
        parent = super()
        user_admin_orchestration = getattr(parent, "_add_view", None)
        if user_admin_orchestration is not None:
            return user_admin_orchestration(request, form_url, extra_context)
        return parent.add_view(request, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        """Defer the parent write until all validated formsets are available."""
        return None

    def _tag_deltas(self, formsets):
        """Serialize the finite TagInline surface without saving a formset."""
        deltas = []
        for formset in formsets:
            if not isinstance(formset, TagFormSet):
                raise ValueError("This editable inline has no IO mutation adapter")
            formset.new_objects = []
            formset.changed_objects = []
            formset.deleted_objects = []
            for form in formset.forms:
                cleaned = getattr(form, "cleaned_data", None)
                if not cleaned or not form.has_changed():
                    continue
                instance = form.instance
                old = None
                if getattr(instance, "pk", None) is not None:
                    old = (
                        getattr(instance, "tag_key", None),
                        getattr(instance, "tag_category", None),
                        getattr(instance, "tag_type", None),
                        getattr(instance, "tag_data", None),
                    )
                new = None
                if not cleaned.get("DELETE"):
                    new = (
                        cleaned.get("tag_key"),
                        cleaned.get("tag_category") or None,
                        cleaned.get("tag_type") or None,
                        cleaned.get("tag_data") or None,
                    )
                    if getattr(instance, "pk", None) is None:
                        formset.new_objects.append(instance)
                    else:
                        formset.changed_objects.append((instance, form.changed_data))
                elif getattr(instance, "pk", None) is not None:
                    formset.deleted_objects.append(instance)
                deltas.append(TagDelta(old=old, new=new))
        return tuple(deltas)

    def _mutation_request(self, request, form, formsets, change):
        """Encode validated form state into an allowlisted immutable request."""
        model_label = self.model._meta.label_lower
        spec = admin_spec(model_label)
        relation_names = {name for name, _label in spec.relations}
        unknown = (
            set(form.cleaned_data)
            - spec.fields
            - relation_names
            - _AUXILIARY_FORM_FIELDS.get(model_label, frozenset())
        )
        if unknown:
            raise ValueError("The admin form contains an unsupported editable field")
        concrete = []
        for name in sorted(spec.fields & form.cleaned_data.keys()):
            value = form.cleaned_data[name]
            if name in spec.foreign_keys:
                value = None if value is None else int(value.pk)
            concrete.append((name, freeze_plain(value)))
        relations = []
        for name, _related_label in spec.relations:
            if name not in form.cleaned_data:
                continue
            value = form.cleaned_data[name]
            relation_ids = tuple(int(item.pk) for item in value)
            relations.append((name, relation_ids))
        password = None
        if not change and model_label == "accounts.accountdb":
            password = form.cleaned_data.get("password1")
        return AdminMutationRequest(
            actor_id=int(request.user.pk),
            model_label=model_label,
            object_id=int(form.instance.pk) if change else None,
            concrete=tuple(concrete),
            relations=tuple(relations),
            tags=self._tag_deltas(formsets),
            password=password,
        )

    def save_related(self, request, form, formsets, change):
        """Bridge the complete validated mutation and update detached response state."""
        owner_request = self._mutation_request(request, form, formsets, change)
        release_worker_db_connections()
        result = run_on_io_thread(mutate_admin, owner_request)
        if result.status not in ("created", "changed"):
            raise _AdminOutcome(result)
        form.instance.pk = result.object_id
        form.instance._state.adding = False
        form.instance._state.db = self.model.objects.db
        request._evennia_admin_owner_result = result

    def _record_audit_warning(self, request):
        request._evennia_admin_audit_warning = True

    def log_addition(self, request, obj, message):
        """Write auxiliary audit only after definite owner success."""
        try:
            with transaction.atomic():
                return super().log_addition(request, obj, message)
        except Exception:
            self._record_audit_warning(request)
            return None

    def log_change(self, request, obj, message):
        """Write auxiliary audit only after definite owner success."""
        try:
            with transaction.atomic():
                return super().log_change(request, obj, message)
        except Exception:
            self._record_audit_warning(request)
            return None

    def _map_bridge_error(self, error):
        if isinstance(error, IOThreadCallIndeterminate):
            return _bridge_response(
                202,
                "The mutation may have completed. Do not retry this request.",
            )
        if isinstance(error, (IOThreadCallTimeout, IOThreadCallUnavailable)):
            return _bridge_response(
                503,
                "The mutation did not start. This request may be retried.",
                retryable=True,
            )
        raise error

    def _map_owner_result(self, result):
        if result.status == "missing":
            return _bridge_response(404, result.message or "The target no longer exists.")
        if result.status == "conflict":
            return _bridge_response(409, result.message or "The mutation was rejected.")
        if result.status in ("partial", "recovery_required"):
            return _bridge_response(
                202,
                result.message or "The mutation completed partially and requires review.",
            )
        return _bridge_response(
            500,
            result.message or "The mutation failed before writing state.",
            retryable=result.retryable,
        )

    @method_decorator(csrf_protect)
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        """Keep Django orchestration while excluding its worker transaction."""
        if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            return super().changeform_view(request, object_id, form_url, extra_context)
        try:
            response = self._changeform_view(request, object_id, form_url, extra_context)
        except _AdminOutcome as error:
            return self._map_owner_result(error.result)
        except PermissionDenied:
            raise
        except ValueError:
            return _bridge_response(409, "The admin form payload is not supported.")
        except (
            IOThreadCallTimeout,
            IOThreadCallIndeterminate,
            IOThreadCallUnavailable,
        ) as error:
            return self._map_bridge_error(error)
        if getattr(request, "_evennia_admin_audit_warning", False):
            self.message_user(
                request,
                "The game-state mutation completed, but its admin audit row could not be written.",
                level=messages.WARNING,
            )
        return response

    def _write_delete_audits(self, request, snapshots, deleted_ids):
        """Write post-delete LogEntry rows from detached pre-delete snapshots."""
        if not deleted_ids:
            return
        content_type_id = ContentType.objects.get_for_model(self.model, for_concrete_model=False).pk
        try:
            with transaction.atomic():
                for object_id in deleted_ids:
                    LogEntry.objects.create(
                        user_id=request.user.pk,
                        content_type_id=content_type_id,
                        object_id=str(object_id),
                        object_repr=snapshots.get(object_id, str(object_id))[:200],
                        action_flag=DELETION,
                        change_message="",
                    )
        except Exception:
            self._record_audit_warning(request)

    def _run_delete_bridge(self, request, ids):
        owner_request = AdminDeleteRequest(
            actor_id=int(request.user.pk),
            model_label=self.model._meta.label_lower,
            object_ids=tuple(int(value) for value in ids),
        )
        release_worker_db_connections()
        return run_on_io_thread(delete_admin, owner_request)

    @method_decorator(csrf_protect)
    def delete_view(self, request, object_id, extra_context=None):
        """Execute confirmed deletion before its auxiliary audit write."""
        if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            return super().delete_view(request, object_id, extra_context)
        obj = self.get_object(request, object_id)
        if obj is None:
            return self._get_obj_does_not_exist_redirect(request, self.opts, object_id)
        if not self.has_delete_permission(request, obj):
            raise PermissionDenied
        object_id_int = int(obj.pk)
        try:
            result = self._run_delete_bridge(request, (object_id_int,))
        except PermissionDenied:
            raise
        except (
            IOThreadCallTimeout,
            IOThreadCallIndeterminate,
            IOThreadCallUnavailable,
        ) as error:
            return self._map_bridge_error(error)
        snapshots = dict(result.object_reprs)
        self._write_delete_audits(request, snapshots, result.deleted_ids)
        object_repr = snapshots.get(object_id_int, str(object_id_int))
        if object_id_int not in result.deleted_ids:
            if object_id_int in result.missing_ids:
                return _bridge_response(404, "The target no longer exists.")
            if object_id_int in result.failed_ids:
                return _bridge_response(
                    202,
                    result.message or "Deletion outcome requires review. Do not retry.",
                )
            return _bridge_response(409, result.message or "Deletion was vetoed.")
        if getattr(request, "_evennia_admin_audit_warning", False):
            self.message_user(
                request,
                "Deletion completed, but its admin audit row could not be written.",
                level=messages.WARNING,
            )
        return self.response_delete(request, object_repr, object_id_int)

    def get_actions(self, request):
        """Replace Django's pre-audit/raw-queryset bulk delete action."""
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        if self.has_delete_permission(request):
            actions["delete_selected"] = (
                self.__class__.delete_selected_owner_safe,
                "delete_selected",
                delete_selected.short_description,
            )
        return actions

    def delete_selected_owner_safe(self, request, queryset):
        """Render stock confirmation, then bridge deterministic domain deletes."""
        if not request.POST.get("post"):
            return delete_selected(self, request, queryset)
        ids = tuple(queryset.values_list("pk", flat=True).order_by("pk"))
        try:
            result = self._run_delete_bridge(request, ids)
        except PermissionDenied:
            raise
        except (
            IOThreadCallTimeout,
            IOThreadCallIndeterminate,
            IOThreadCallUnavailable,
        ) as error:
            return self._map_bridge_error(error)
        snapshots = dict(result.object_reprs)
        self._write_delete_audits(request, snapshots, result.deleted_ids)
        level = messages.SUCCESS if result.status == "deleted" else messages.WARNING
        self.message_user(request, result.message, level=level)
        if getattr(request, "_evennia_admin_audit_warning", False):
            self.message_user(
                request,
                "Deletion completed, but one or more admin audit rows could not be written.",
                level=messages.WARNING,
            )
        return None

    delete_selected_owner_safe.short_description = delete_selected.short_description
    delete_selected_owner_safe.allowed_permissions = ("delete",)


__all__ = ("OwnerSafeModelAdminMixin",)
