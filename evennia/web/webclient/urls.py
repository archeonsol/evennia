"""
This structures the (simple) structure of the webpage 'application'.

"""

from django.urls import path

from . import views

app_name = "webclient"

urlpatterns = [
    path("", views.webclient, name="index"),
    # Dual-route: the new Svelte shell client while it reaches parity.
    path("client2/", views.webclient2, name="client2"),
]
