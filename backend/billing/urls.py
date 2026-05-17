from django.urls import path

from .views import EventIngestionView, UsageView

urlpatterns = [
    path("v1/events", EventIngestionView.as_view(), name="event-ingestion"),
    path("v1/usage", UsageView.as_view(), name="usage"),
]
