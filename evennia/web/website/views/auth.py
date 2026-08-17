"""Owner-safe adapters for Django's stock authentication views."""

from django.contrib.auth import login as auth_login
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.http import HttpResponse
from django.views.generic.edit import FormView

from evennia.web.utils.auth import PasswordMutationRequest, mutate_password
from evennia.web.utils.io import (
    IOThreadCallIndeterminate,
    IOThreadCallTimeout,
    IOThreadCallUnavailable,
    release_worker_db_connections,
    run_on_io_thread,
)
from evennia.web.website.forms import OwnerPasswordChangeForm, OwnerSetPasswordForm


def _bridge_error(error):
    if isinstance(error, IOThreadCallIndeterminate):
        return HttpResponse(
            "The operation may have completed. Do not retry this request.",
            status=202,
            headers={"X-Evennia-Retryable": "false"},
        )
    return HttpResponse(
        "The operation did not start. This request may be retried.",
        status=503,
        headers={"X-Evennia-Retryable": "true", "Retry-After": "1"},
    )


class OwnerLoginView(auth_views.LoginView):
    """Map owner-bridge availability without leaking credentials."""

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except (
            IOThreadCallTimeout,
            IOThreadCallIndeterminate,
            IOThreadCallUnavailable,
        ) as error:
            return _bridge_error(error)


class OwnerPasswordChangeView(auth_views.PasswordChangeView):
    """Repeat old-password authorization and persist on the owner."""

    form_class = OwnerPasswordChangeForm

    def form_valid(self, form):
        try:
            release_worker_db_connections()
            result = run_on_io_thread(
                mutate_password,
                PasswordMutationRequest(
                    account_id=int(form.user.pk),
                    mode="change",
                    old_password=form.cleaned_data["old_password"],
                    new_password=form.cleaned_data["new_password1"],
                ),
            )
        except (
            IOThreadCallTimeout,
            IOThreadCallIndeterminate,
            IOThreadCallUnavailable,
        ) as error:
            return _bridge_error(error)
        if result.status != "changed":
            form.add_error("old_password", result.message or "The password was not changed.")
            return self.form_invalid(form)
        form.user.password = result.password_hash
        update_session_auth_hash(self.request, form.user)
        return FormView.form_valid(self, form)


class OwnerPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Repeat reset-token validation and password mutation on the owner."""

    form_class = OwnerSetPasswordForm

    def form_valid(self, form):
        token = self.request.session.get(auth_views.INTERNAL_RESET_SESSION_TOKEN)
        try:
            release_worker_db_connections()
            result = run_on_io_thread(
                mutate_password,
                PasswordMutationRequest(
                    account_id=int(form.user.pk),
                    mode="reset",
                    reset_token=token,
                    new_password=form.cleaned_data["new_password1"],
                ),
            )
        except (
            IOThreadCallTimeout,
            IOThreadCallIndeterminate,
            IOThreadCallUnavailable,
        ) as error:
            return _bridge_error(error)
        if result.status != "changed":
            form.add_error(None, result.message or "The password was not changed.")
            return self.form_invalid(form)
        form.user.password = result.password_hash
        self.request.session.pop(auth_views.INTERNAL_RESET_SESSION_TOKEN, None)
        if self.post_reset_login:
            auth_login(self.request, form.user, self.post_reset_login_backend)
        return FormView.form_valid(self, form)


__all__ = (
    "OwnerLoginView",
    "OwnerPasswordChangeView",
    "OwnerPasswordResetConfirmView",
)
