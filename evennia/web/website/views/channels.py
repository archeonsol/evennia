"""
Views for managing channels.

"""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.views.generic import ListView

from evennia.utils import class_from_module
from evennia.utils.logger import tail_log_file
from evennia.web.utils.io import IOThreadCallIndeterminate, IOThreadCallTimeout, run_on_io_thread

from .io import (
    WebObjectNotFound,
    WebObjectPermissionDenied,
    load_channel_detail,
    load_channel_list,
    typeclass_path,
)
from .mixins import TypeclassMixin
from .objects import ObjectDetailView, _account_id


class ChannelMixin(TypeclassMixin):
    """
    This is a "mixin", a modifier of sorts.

    Any view class with this in its inheritance list will be modified to work
    with HelpEntry objects instead of generic Objects or otherwise.

    """

    # -- Django constructs --
    model = class_from_module(
        settings.BASE_CHANNEL_TYPECLASS, fallback=settings.FALLBACK_CHANNEL_TYPECLASS
    )

    # -- Evennia constructs --
    page_title = "Channels"

    # What lock type to check for the requesting user, authenticated or not.
    # https://github.com/evennia/evennia/wiki/Locks#valid-access_types
    access_type = "listen"

    def get_queryset(self):
        """
        Django hook; here we want to return a list of only those Channels
        and other documentation that the current user is allowed to see.

        Returns:
            queryset (QuerySet): List of Channels available to the user.

        """
        return run_on_io_thread(
            load_channel_list,
            typeclass_path(self.typeclass),
            _account_id(self.request),
        )


class ChannelListView(ChannelMixin, ListView):
    """
    Returns a list of channels that can be viewed by a user, authenticated
    or not.

    """

    # -- Django constructs --
    paginate_by = 100
    template_name = "website/channel_list.html"

    # -- Evennia constructs --
    page_title = "Channel Index"

    max_popular = 10

    def get(self, request, *args, **kwargs):
        """Render serialized channel rows or a bounded timeout response."""
        try:
            return super().get(request, *args, **kwargs)
        except (IOThreadCallTimeout, IOThreadCallIndeterminate):
            return HttpResponse("Game state did not answer in time.", status=504)

    def get_context_data(self, **kwargs):
        """
        Django hook; we override it to calculate the most popular channels.

        Returns:
            context (dict): Django context object

        """
        context = super().get_context_data(**kwargs)

        # Calculate which channels are most popular
        context["most_popular"] = sorted(
            list(context["object_list"]),
            key=lambda channel: channel.subscription_count,
            reverse=True,
        )[: self.max_popular]

        return context


class ChannelDetailView(ChannelMixin, ObjectDetailView):
    """
    Returns the log entries for a given channel.

    """

    # -- Django constructs --
    template_name = "website/channel_detail.html"

    # -- Evennia constructs --
    # What attributes of the object you wish to display on the page. Model-level
    # attributes will take precedence over identically-named db.attributes!
    # The order you specify here will be followed.
    attributes = ["name"]

    # How many log entries to read and display.
    max_num_lines = 10000

    def get(self, request, *args, **kwargs):
        """Authorize and serialize the channel before reading its log on the worker."""
        try:
            self.object = run_on_io_thread(
                load_channel_detail,
                typeclass_path(self.typeclass),
                self.kwargs.get("slug", ""),
                _account_id(request),
            )
        except WebObjectNotFound as err:
            raise Http404(str(err)) from err
        except WebObjectPermissionDenied as err:
            raise PermissionDenied(str(err)) from err
        except (IOThreadCallTimeout, IOThreadCallIndeterminate):
            return HttpResponse("Game state did not answer in time.", status=504)
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        """
        Django hook; before we can display the channel logs, we need to recall
        the logfile and read its lines.

        Returns:
            context (dict): Django context object

        """
        # Get the parent context object, necessary first step
        context = super().get_context_data(**kwargs)
        channel = self.object
        filename = channel.log_filename

        # Split log entries so we can filter by time
        bucket = []
        for log in (x.strip() for x in tail_log_file(filename, 0, self.max_num_lines)):
            if not log:
                continue
            try:
                time, msg = log.split(" [-] ")
                time_key = time.split(":")[0]
            except ValueError:
                # malformed log line. Skip.
                continue

            bucket.append({"key": time_key, "timestamp": time, "message": msg})

        # Add the processed entries to the context
        context["object_list"] = bucket

        # Get a list of unique timestamps by hour and sort them
        context["object_filters"] = sorted(set([x["key"] for x in bucket]))

        return context
