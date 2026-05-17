from django.urls import path

from .views import (
    EventIngestionView,
    InvoiceDetailView,
    InvoiceListView,
    OpsBillingInspectorView,
    OpsCreditView,
    OpsCustomerDetailView,
    OpsCustomerListView,
    OpsLineItemOverrideView,
    PaymentWebhookView,
    UsageView,
)

urlpatterns = [
    path("v1/events", EventIngestionView.as_view(), name="event-ingestion"),
    path("v1/usage", UsageView.as_view(), name="usage"),
    path("v1/invoices", InvoiceListView.as_view(), name="invoice-list"),
    path("v1/invoices/<uuid:invoice_id>", InvoiceDetailView.as_view(), name="invoice-detail"),
    path("ops/customers", OpsCustomerListView.as_view(), name="ops-customer-list"),
    path("ops/customers/<uuid:customer_id>", OpsCustomerDetailView.as_view(), name="ops-customer-detail"),
    path(
        "ops/customers/<uuid:customer_id>/billing-inspector",
        OpsBillingInspectorView.as_view(),
        name="ops-billing-inspector",
    ),
    path("ops/customers/<uuid:customer_id>/credits", OpsCreditView.as_view(), name="ops-credit-create"),
    path(
        "ops/invoices/<uuid:invoice_id>/line-items/<uuid:line_item_id>",
        OpsLineItemOverrideView.as_view(),
        name="ops-line-item-override",
    ),
    path("webhooks/payments", PaymentWebhookView.as_view(), name="payment-webhook"),
]
