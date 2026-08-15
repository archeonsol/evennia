"""
Views for manipulating Characters (children of Objects often used for
puppeting).

"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import ListView, View

from evennia.utils import class_from_module
from evennia.web.utils.io import (
    IOThreadCallIndeterminate,
    IOThreadCallTimeout,
    run_on_io_thread,
)
from evennia.web.website import forms

from .io import (
    WebObjectNotFound,
    WebObjectPermissionDenied,
    WebObjectSlugMismatch,
    authorize_character_puppet,
    create_character,
    delete_character,
    load_character_page,
    load_owned_character_detail,
    typeclass_path,
    update_owned_character_attributes,
)
from .mixins import TypeclassMixin
from .objects import (
    ObjectCreateView,
    ObjectDeleteView,
    ObjectDetailView,
    ObjectUpdateView,
    _account_id,
    _mutation_timeout_response,
    _read_timeout_response,
)


class CharacterMixin(TypeclassMixin):
    """
    This is a "mixin", a modifier of sorts.

    Any view class with this in its inheritance list will be modified to work
    with Character objects instead of generic Objects or otherwise.

    """

    # -- Django constructs --
    model = class_from_module(
        settings.BASE_CHARACTER_TYPECLASS,
        fallback=settings.FALLBACK_CHARACTER_TYPECLASS,
    )
    form_class = forms.CharacterForm
    success_url = reverse_lazy("character-manage")

    def get_queryset(self):
        """Return frozen rows for owned characters of the configured typeclass."""
        page = run_on_io_thread(
            load_character_page,
            typeclass_path(self.typeclass),
            _account_id(self.request),
            getattr(self, "access_type", "view"),
            True,
            self.request.session.get("puppet"),
        )
        self.request.evennia_character_menu = page.menu
        return page.rows


class CharacterListView(LoginRequiredMixin, CharacterMixin, ListView):
    """
    This view provides a mechanism by which a logged-in player can view a list
    of all other characters.

    This view requires authentication by default as a nominal effort to prevent
    human stalkers and automated bots/scrapers from harvesting data on your users.

    """

    # -- Django constructs --
    template_name = "website/character_list.html"
    paginate_by = 100

    # -- Evennia constructs --
    page_title = "Character List"
    access_type = "view"

    def get_queryset(self):
        """Return frozen visible-character rows from one IO-owned service."""
        page = run_on_io_thread(
            load_character_page,
            typeclass_path(self.typeclass),
            _account_id(self.request),
            self.access_type,
            False,
            self.request.session.get("puppet"),
        )
        self.request.evennia_character_menu = page.menu
        return page.rows

    def get(self, request, *args, **kwargs):
        """Map character collection timeouts to a retryable gateway timeout."""
        try:
            return super().get(request, *args, **kwargs)
        except (IOThreadCallTimeout, IOThreadCallIndeterminate):
            return _read_timeout_response()


class CharacterPuppetView(LoginRequiredMixin, CharacterMixin, View):
    """
    This view provides a mechanism by which a logged-in player can "puppet" one
    of their characters within the context of the website.

    It also ensures that any user attempting to puppet something is logged in,
    and that their intended puppet is one that they own.

    """

    def post(self, request, *args, **kwargs):
        """
        Django hook.

        This view returns the URL to which the user should be redirected after
        a passed or failed puppet attempt.

        Returns:
            url (str): Path to post-puppet destination.

        """
        try:
            character = run_on_io_thread(
                authorize_character_puppet,
                typeclass_path(self.typeclass),
                self.kwargs.get("pk"),
                self.kwargs.get("slug"),
                _account_id(request),
            )
        except (WebObjectNotFound, WebObjectSlugMismatch) as err:
            raise Http404(str(err)) from err
        except WebObjectPermissionDenied as err:
            raise PermissionDenied(str(err)) from err
        except (IOThreadCallTimeout, IOThreadCallIndeterminate):
            return _read_timeout_response()

        # Get the page the user came from
        next_page = request.POST.get("next", self.success_url)

        # since next_page is untrusted input from the user, we need to check it's safe to
        next_page = iri_to_uri(next_page)
        if not url_has_allowed_host_and_scheme(
            url=next_page,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_page = self.success_url

        request.session["puppet"] = character.id
        messages.success(request, "You become '%s'!" % character.key)

        return HttpResponseRedirect(next_page)


class CharacterManageView(LoginRequiredMixin, CharacterMixin, ListView):
    """
    This view provides a mechanism by which a logged-in player can browse,
    edit, or delete their own characters.

    """

    # -- Django constructs --
    paginate_by = 10
    template_name = "website/character_manage_list.html"

    # -- Evennia constructs --
    page_title = "Manage Characters"

    def get_queryset(self):
        """Return frozen owned-character rows and cache their menu DTO."""
        page = run_on_io_thread(
            load_character_page,
            typeclass_path(self.typeclass),
            _account_id(self.request),
            "view",
            True,
            self.request.session.get("puppet"),
        )
        self.request.evennia_character_menu = page.menu
        return page.rows

    def get(self, request, *args, **kwargs):
        """Map character collection timeouts to a retryable gateway timeout."""
        try:
            return super().get(request, *args, **kwargs)
        except (IOThreadCallTimeout, IOThreadCallIndeterminate):
            return _read_timeout_response()


class CharacterUpdateView(CharacterMixin, ObjectUpdateView):
    """
    This view provides a mechanism by which a logged-in player (enforced by
    ObjectUpdateView) can edit the attributes of a character they own.

    """

    # -- Django constructs --
    form_class = forms.CharacterUpdateForm
    template_name = "website/character_form.html"

    def _load_dto(self):
        """Load an owned character rather than relying only on its access rule."""
        return run_on_io_thread(
            load_owned_character_detail,
            typeclass_path(self.typeclass),
            self.kwargs.get("pk"),
            self.kwargs.get(self.slug_url_kwarg),
            _account_id(self.request),
            self.access_type,
            tuple(self.attributes),
        )

    def form_valid(self, form):
        """Repeat ownership and access checks in the IO-owned update call."""
        model_fields = tuple(
            getattr(getattr(self.form_class, "Meta", None), "fields", ())
        )
        data = {
            key: value
            for key, value in form.cleaned_data.items()
            if key not in model_fields
        }
        try:
            self.object, result_messages = run_on_io_thread(
                update_owned_character_attributes,
                typeclass_path(self.typeclass),
                self.kwargs.get("pk"),
                self.kwargs.get(self.slug_url_kwarg),
                _account_id(self.request),
                self.access_type,
                data,
                tuple(self.attributes),
            )
        except (WebObjectNotFound, WebObjectSlugMismatch) as err:
            raise Http404(str(err)) from err
        except WebObjectPermissionDenied as err:
            raise PermissionDenied(str(err)) from err
        except IOThreadCallIndeterminate:
            return _mutation_timeout_response(indeterminate=True)
        except IOThreadCallTimeout:
            return _mutation_timeout_response(indeterminate=False)
        for result_message in result_messages:
            messages.success(self.request, result_message)
        return HttpResponseRedirect(self.get_success_url())


class CharacterDetailView(CharacterMixin, ObjectDetailView):
    """
    This view provides a mechanism by which a user can view the attributes of
    a character, owned by them or not.

    """

    # -- Django constructs --
    template_name = "website/object_detail.html"

    # -- Evennia constructs --
    # What attributes to display for this object
    attributes = ["name", "desc"]
    access_type = "view"


class CharacterDeleteView(CharacterMixin, ObjectDeleteView):
    """
    This view provides a mechanism by which a logged-in player (enforced by
    ObjectDeleteView) can delete a character they own.

    """

    # using the character form fails there
    form_class = forms.EvenniaForm

    def _load_dto(self):
        """Load an owned deletion target through the IO service."""
        return run_on_io_thread(
            load_owned_character_detail,
            typeclass_path(self.typeclass),
            self.kwargs.get("pk"),
            self.kwargs.get(self.slug_url_kwarg),
            _account_id(self.request),
            self.access_type,
            tuple(self.attributes),
        )

    def post(self, request, *args, **kwargs):
        """Authorize ownership and delete without invoking Django's live-model path."""
        try:
            key = run_on_io_thread(
                delete_character,
                typeclass_path(self.typeclass),
                self.kwargs.get("pk"),
                self.kwargs.get(self.slug_url_kwarg),
                _account_id(request),
                self.access_type,
            )
        except (WebObjectNotFound, WebObjectSlugMismatch) as err:
            raise Http404(str(err)) from err
        except WebObjectPermissionDenied as err:
            raise PermissionDenied(str(err)) from err
        except IOThreadCallIndeterminate:
            return _mutation_timeout_response(indeterminate=True)
        except IOThreadCallTimeout:
            return _mutation_timeout_response(indeterminate=False)
        messages.success(request, "Successfully deleted '%s'." % key)
        return HttpResponseRedirect(self.success_url)


class CharacterCreateView(CharacterMixin, ObjectCreateView):
    """
    This view provides a mechanism by which a logged-in player (enforced by
    ObjectCreateView) can create a new character.

    """

    # -- Django constructs --
    template_name = "website/character_form.html"

    def get_form_kwargs(self):
        """Do not pass ModelForm-only state into the scalar create form."""
        kwargs = super().get_form_kwargs()
        kwargs.pop("instance", None)
        return kwargs

    def form_valid(self, form):
        """
        Django hook, modified for Evennia.

        This hook is called after a valid form is submitted.

        When an character creation form is submitted and the data is deemed valid,
        proceeds with creating the Character object.

        """
        attributes = {key: value for key, value in form.cleaned_data.items()}
        try:
            result = run_on_io_thread(
                create_character,
                typeclass_path(self.typeclass),
                _account_id(self.request),
                attributes,
            )
        except IOThreadCallIndeterminate:
            return _mutation_timeout_response(indeterminate=True)
        except IOThreadCallTimeout:
            return _mutation_timeout_response(indeterminate=False)
        for error in result.errors:
            messages.error(self.request, error)
        if result.created:
            messages.success(
                self.request, "Your character '%s' was created!" % result.key
            )
            return HttpResponseRedirect(self.success_url)
        messages.error(self.request, "Your character could not be created.")
        return self.form_invalid(form)
