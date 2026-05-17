from django.urls import path

from .views import EventIngestionView, InvoiceDetailView, InvoiceListView, UsageView

urlpatterns = [
    path("v1/events", EventIngestionView.as_view(), name="event-ingestion"),
    path("v1/usage", UsageView.as_view(), name="usage"),
    path("v1/invoices", InvoiceListView.as_view(), name="invoice-list"),
    path("v1/invoices/<uuid:invoice_id>", InvoiceDetailView.as_view(), name="invoice-detail"),
]
