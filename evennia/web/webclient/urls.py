"""
This structures the (simple) structure of the webpage 'application'.

"""

from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "webclient"

urlpatterns = [
    path("", views.webclient, name="index"),
    # The shell reached parity and became the default at "". Keep the old
    # dual-route URL alive for one release so bookmarks and in-game links that
    # still say client2/ do not 404.
    path(
        "client2/",
        RedirectView.as_view(pattern_name="webclient:index", permanent=False),
        name="client2",
    ),
]
