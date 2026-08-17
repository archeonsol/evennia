"""
Views for managing accounts.

"""

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse_lazy

from evennia.utils import class_from_module
from evennia.web.utils.auth import RegistrationRequest, register_account
from evennia.web.utils.io import (
    IOThreadCallIndeterminate,
    IOThreadCallTimeout,
    IOThreadCallUnavailable,
    release_worker_db_connections,
    run_on_io_thread,
)
from evennia.web.website import forms

from .mixins import EvenniaCreateView, TypeclassMixin


class AccountMixin(TypeclassMixin):
    """
    This is used to grant abilities to classes it is added to.

    Any view class with this in its inheritance list will be modified to work
    with Account objects instead of generic Objects or otherwise.

    """

    # -- Django constructs --
    model = class_from_module(
        settings.BASE_ACCOUNT_TYPECLASS, fallback=settings.FALLBACK_ACCOUNT_TYPECLASS
    )
    form_class = forms.AccountForm


class AccountCreateView(AccountMixin, EvenniaCreateView):
    """
    Account creation view.

    """

    # -- Django constructs --
    template_name = "website/registration/register.html"
    success_url = reverse_lazy("login")

    def form_valid(self, form):
        """
        Django hook, modified for Evennia.

        This hook is called after a valid form is submitted.

        When an account creation form is submitted and the data is deemed valid,
        proceeds with creating the Account object.

        """
        # Get values provided
        username = form.cleaned_data["username"]
        password = form.cleaned_data["password1"]
        email = form.cleaned_data.get("email", "")

        try:
            release_worker_db_connections()
            result = run_on_io_thread(
                register_account,
                RegistrationRequest(
                    username=username,
                    email=email,
                    password=password,
                    ip=str(getattr(self.request, "origin_ip", ""))[:128],
                    typeclass_path=f"{self.typeclass.__module__}.{self.typeclass.__name__}",
                ),
            )
        except IOThreadCallIndeterminate:
            return HttpResponse(
                "Account creation may have completed. Do not retry this request.",
                status=202,
                headers={"X-Evennia-Retryable": "false"},
            )
        except (IOThreadCallTimeout, IOThreadCallUnavailable):
            return HttpResponse(
                "Account creation did not start. This request may be retried.",
                status=503,
                headers={"X-Evennia-Retryable": "true", "Retry-After": "1"},
            )

        if result.status == "recovery_required":
            return HttpResponse(
                "Account creation requires staff recovery. Do not retry this request.",
                status=202,
                headers={"X-Evennia-Retryable": "false"},
            )
        if result.status == "fault":
            return HttpResponse(
                "Account creation failed after internal processing. Do not retry this request.",
                status=500,
                headers={"X-Evennia-Retryable": "false"},
            )
        if result.status != "created":
            for field, _code, message in result.issues:
                form.add_error(field if field in form.fields else None, message)
            if not result.issues:
                form.add_error(None, "Account creation requires staff review.")
            return self.form_invalid(form)
        else:
            # Inform user of success
            messages.success(
                self.request,
                f"Your account '{result.account_name}' was successfully created!",
            )
            return HttpResponseRedirect(self.success_url)
