"""
Views for managing a specific object)

"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.utils.text import slugify

from evennia.utils import class_from_module
from evennia.web.utils.io import (
    IOThreadCallIndeterminate,
    IOThreadCallTimeout,
    run_on_io_thread,
)

from .io import (
    WebObjectNotFound,
    WebObjectPermissionDenied,
    WebObjectSlugMismatch,
    load_object_detail,
    typeclass_path,
    update_object_attributes,
)
from .mixins import EvenniaCreateView, EvenniaDeleteView, EvenniaDetailView, EvenniaUpdateView


def _account_id(request):
    """Return the authenticated account ID without crossing a live model."""
    user = request.user
    return int(user.pk) if getattr(user, "is_authenticated", False) else None


def _read_timeout_response():
    """Return the stock response for a timed-out read."""
    return HttpResponse("Game state did not answer in time.", status=504)


def _mutation_timeout_response(*, indeterminate):
    """Return a response that distinguishes cancelled and unknown mutations."""
    if indeterminate:
        return HttpResponse(
            "The update may have completed. Do not retry this request.",
            status=202,
            headers={"X-Evennia-Retryable": "false"},
        )
    return HttpResponse(
        "The update did not start. This request may be retried.",
        status=503,
        headers={"Retry-After": "1"},
    )


class ObjectDetailView(EvenniaDetailView):
    """
    This is an important view.

    Any view you write that deals with displaying, updating or deleting a
    specific object will want to inherit from this. It provides the mechanisms
    by which to retrieve the object and make sure the user requesting it has
    permissions to actually *do* things to it.

    """

    # -- Django constructs --
    #
    # Choose what class of object this view will display. Note that this should
    # be an actual Python class (i.e. do `from typeclasses.characters import
    # Character`, then put `Character`), not an Evennia typeclass path
    # (i.e. `typeclasses.characters.Character`).
    #
    # So when you extend it, this line should look simple, like:
    # model = Object
    model = class_from_module(
        settings.BASE_OBJECT_TYPECLASS, fallback=settings.FALLBACK_OBJECT_TYPECLASS
    )

    # What HTML template you wish to use to display this page.
    template_name = "website/object_detail.html"

    # -- Evennia constructs --
    #
    # What authorization operation to check for the requesting user.
    access_type = "view"

    # What attributes of the object you wish to display on the page. Model-level
    # attributes will take precedence over identically-named db.attributes!
    # The order you specify here will be followed.
    attributes = ["name", "desc"]

    def _load_dto(self):
        """Load and authorize this request through the IO-owned service."""
        return run_on_io_thread(
            load_object_detail,
            typeclass_path(self.typeclass),
            self.kwargs.get("pk"),
            self.kwargs.get(self.slug_url_kwarg),
            _account_id(self.request),
            self.access_type,
            tuple(self.attributes),
        )

    def get(self, request, *args, **kwargs):
        """Render a DTO without exposing a live typeclass to the template."""
        try:
            self.object = self._load_dto()
        except (WebObjectNotFound, WebObjectSlugMismatch) as err:
            raise Http404(str(err)) from err
        except WebObjectPermissionDenied as err:
            raise PermissionDenied(str(err)) from err
        except (IOThreadCallTimeout, IOThreadCallIndeterminate):
            return _read_timeout_response()
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        """
        Adds an 'attributes' list to the request context consisting of the
        attributes specified at the class level, and in the order provided.

        Django views do not provide a way to reference dynamic attributes, so
        we have to grab them all before we render the template.

        Returns:
            context (dict): Django context object

        """
        context = super().get_context_data(**kwargs)
        obj = context.get("object")
        context["attribute_list"] = dict(getattr(obj, "attributes", ()))
        return context

    def get_object(self, queryset=None):
        """
        Override of Django hook that provides some important Evennia-specific
        functionality.

        Evennia does not natively store slugs, so where a slug is provided,
        calculate the same for the object and make sure it matches.

        This also checks to make sure the user has access to view/edit/delete
        this object!

        """
        # A queryset can be provided to pre-emptively limit what objects can
        # possibly be returned. For example, you can supply a queryset that
        # only returns objects whose name begins with "a".
        if not queryset:
            queryset = self.get_queryset()

        # Get the object, ignoring all checks and filters for now
        obj = self.typeclass.objects.get(pk=self.kwargs.get("pk"))

        # Check if this object was requested in a valid manner
        if slugify(obj.name) != self.kwargs.get(self.slug_url_kwarg):
            raise HttpResponseBadRequest(
                "No %(verbose_name)s found matching the query"
                % {"verbose_name": queryset.model._meta.verbose_name}
            )

        # Check if the requestor account has permissions to access object
        account = self.request.user
        if not obj.access(account, self.access_type):
            raise PermissionDenied("You are not authorized to %s this object." % self.access_type)

        # Get the object, if it is in the specified queryset
        obj = super().get_object(queryset)

        return obj


class ObjectCreateView(LoginRequiredMixin, EvenniaCreateView):
    """
    This is an important view.

    Any view you write that deals with creating a specific object will want to
    inherit from this. It provides the mechanisms by which to make sure the user
    requesting creation of an object is authenticated, and provides a sane
    default title for the page.

    """

    model = class_from_module(
        settings.BASE_OBJECT_TYPECLASS, fallback=settings.FALLBACK_OBJECT_TYPECLASS
    )


class ObjectDeleteView(LoginRequiredMixin, ObjectDetailView, EvenniaDeleteView):
    """
    This is an important view for obvious reasons!

    Any view you write that deals with deleting a specific object will want to
    inherit from this. It provides the mechanisms by which to make sure the user
    requesting deletion of an object is authenticated, and that they have
    permissions to delete the requested object.

    """

    # -- Django constructs --
    model = class_from_module(
        settings.BASE_OBJECT_TYPECLASS, fallback=settings.FALLBACK_OBJECT_TYPECLASS
    )
    template_name = "website/object_confirm_delete.html"

    # -- Evennia constructs --
    access_type = "delete"


class ObjectUpdateView(LoginRequiredMixin, ObjectDetailView, EvenniaUpdateView):
    """
    This is an important view.

    Any view you write that deals with updating a specific object will want to
    inherit from this. It provides the mechanisms by which to make sure the user
    requesting editing of an object is authenticated, and that they have
    permissions to edit the requested object.

    This functions slightly different from default Django UpdateViews in that
    it does not update core model fields, *only* object attributes!

    """

    # -- Django constructs --
    model = class_from_module(
        settings.BASE_OBJECT_TYPECLASS, fallback=settings.FALLBACK_OBJECT_TYPECLASS
    )

    # -- Evennia constructs --
    access_type = "edit"

    def get_success_url(self):
        """
        Django hook.

        Can be overridden to return any URL you want to redirect the user to
        after the object is successfully updated, but by default it goes to the
        object detail page so the user can see their changes reflected.

        """
        if self.success_url:
            return self.success_url
        if hasattr(self.object, "detail_url"):
            return self.object.detail_url
        return self.object.web_get_detail_url()

    def get_initial(self):
        """
        Django hook, modified for Evennia.

        Prepopulates the update form field values based on object db attributes.

        Returns:
            data (dict): Dictionary of key:value pairs containing initial form
                data.

        """
        obj = self.object
        values = dict(obj.attributes)
        data = {key: values.get(key.title(), "") for key in self.form_class.base_fields}
        if "db_key" in data:
            data["db_key"] = obj.key
        return data

    def get(self, request, *args, **kwargs):
        """Render a scalar form initialized from an IO-built DTO."""
        try:
            self.object = self._load_dto()
        except (WebObjectNotFound, WebObjectSlugMismatch) as err:
            raise Http404(str(err)) from err
        except WebObjectPermissionDenied as err:
            raise PermissionDenied(str(err)) from err
        except (IOThreadCallTimeout, IOThreadCallIndeterminate):
            return _read_timeout_response()
        form = self.form_class(initial=self.get_initial())
        return self.render_to_response(self.get_context_data(object=self.object, form=form))

    def post(self, request, *args, **kwargs):
        """Validate scalar input, then authorize and mutate in one IO call."""
        form = self.form_class(request.POST)
        if not form.is_valid():
            try:
                self.object = self._load_dto()
            except (WebObjectNotFound, WebObjectSlugMismatch) as err:
                raise Http404(str(err)) from err
            except WebObjectPermissionDenied as err:
                raise PermissionDenied(str(err)) from err
            except (IOThreadCallTimeout, IOThreadCallIndeterminate):
                return _read_timeout_response()
            return self.render_to_response(self.get_context_data(object=self.object, form=form))
        return self.form_valid(form)

    def form_valid(self, form):
        """
        Override of Django hook.

        Updates object attributes based on values submitted.

        This is run when the form is submitted and the data on it is deemed
        valid-- all values are within expected ranges, all strings contain
        valid characters and lengths, etc.

        This method is only called if all values for the fields submitted
        passed form validation, so at this point we can assume the data is
        validated and sanitized.

        """
        model_fields = tuple(getattr(getattr(self.form_class, "Meta", None), "fields", ()))
        data = {key: value for key, value in form.cleaned_data.items() if key not in model_fields}
        try:
            self.object, result_messages = run_on_io_thread(
                update_object_attributes,
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
