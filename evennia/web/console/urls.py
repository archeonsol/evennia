"""URL routes for the console API.

Mounted by :mod:`evennia.web.urls` under ``api/console/`` when
``settings.CONSOLE_ENABLED`` is on.
"""

from django.urls import path

from evennia.web.console import views

app_name = "console"

urlpatterns = [
    path("", views.RootView.as_view(), name="root"),
    path("health/", views.HealthView.as_view(), name="health"),
    path("models/", views.ModelSpecView.as_view(), name="models"),
    path("models/<str:label>/", views.ModelSpecView.as_view(), name="model-detail"),
    path("panels/<str:key>/rows/", views.PanelRowsView.as_view(), name="panel-rows"),
    path("panels/<str:key>/detail/<str:pk>/", views.PanelDetailView.as_view(), name="panel-detail"),
    path(
        "panels/<str:key>/actions/<str:name>/", views.PanelActionView.as_view(), name="panel-action"
    ),
]
