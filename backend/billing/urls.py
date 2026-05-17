from django.urls import path

from .views import EventIngestionView

urlpatterns = [
    path("v1/events", EventIngestionView.as_view(), name="event-ingestion"),
]
