from datetime import datetime, timedelta, timezone as datetime_timezone

from django.conf import settings
from django.db.models import Sum
from rest_framework.test import APITestCase

from billing.models import ApiKey, Customer, Invoice, PricePlan, UsageEvent, UsageWindow
from billing.services import generate_invoice_for_customer


class BillingInspectorTests(APITestCase):
    def setUp(self):
        self.price_plan = PricePlan.objects.create(name="Inspector Default")
        self.customer = Customer.objects.create(
            name="Inspector Alpha",
            email="inspector-alpha@example.com",
            price_plan=self.price_plan,
        )
        self.api_key, _ = ApiKey.create_key(self.customer, "Inspector Alpha key")
        self.period_start = datetime(2026, 5, 1, 0, 0, tzinfo=datetime_timezone.utc)
        self.period_end = datetime(2026, 6, 1, 0, 0, tzinfo=datetime_timezone.utc)

    def ops_headers(self):
        return {"HTTP_X_OPS_TOKEN": settings.OPS_TOKEN}

    def inspector_url(self, customer=None, start=None, end=None):
        customer = customer or self.customer
        start = start or self.period_start
        end = end or self.period_end
        return (
            f"/ops/customers/{customer.id}/billing-inspector"
            f"?period_start={start.date().isoformat()}&period_end={end.date().isoformat()}"
        )

    def create_event(self, request_id, customer=None, api_key=None, units=100):
        return UsageEvent.objects.create(
            request_id=request_id,
            customer=customer or self.customer,
            api_key=api_key or self.api_key,
            endpoint="/v1/chat",
            units=units,
            timestamp=datetime(2026, 5, 16, 18, 0, tzinfo=datetime_timezone.utc),
        )

    def create_window(self, customer=None, api_key=None, units=100):
        return UsageWindow.objects.create(
            customer=customer or self.customer,
            api_key=api_key or self.api_key,
            window_start=datetime(2026, 5, 16, 18, 0, tzinfo=datetime_timezone.utc),
            window_end=datetime(2026, 5, 16, 19, 0, tzinfo=datetime_timezone.utc),
            total_units=units,
            event_count=1,
        )

    def test_billing_inspector_returns_correct_totals(self):
        self.create_event("inspector-req-1", units=8_000)
        self.create_event("inspector-req-2", units=12_000)
        self.create_window(units=20_000)
        invoice = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)

        response = self.client.get(self.inspector_url(), **self.ops_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["events"]["count"], 2)
        self.assertEqual(response.data["events"]["total_units"], 20_000)
        self.assertEqual(response.data["windows"]["count"], 1)
        self.assertEqual(response.data["windows"]["total_units"], 20_000)
        self.assertEqual(response.data["invoices"][0]["id"], str(invoice.id))
        self.assertEqual(len(response.data["invoices"][0]["line_items"]), invoice.line_items.count())

    def test_billing_inspector_tenant_safe(self):
        self.create_event("tenant-a-req", units=1_000)
        self.create_window(units=1_000)
        generate_invoice_for_customer(self.customer, self.period_start, self.period_end)

        other_customer = Customer.objects.create(
            name="Inspector Beta",
            email="inspector-beta@example.com",
            price_plan=self.price_plan,
        )
        other_api_key, _ = ApiKey.create_key(other_customer, "Inspector Beta key")
        self.create_event("tenant-b-req", customer=other_customer, api_key=other_api_key, units=9_000)
        self.create_window(customer=other_customer, api_key=other_api_key, units=9_000)
        other_invoice = generate_invoice_for_customer(other_customer, self.period_start, self.period_end)

        response = self.client.get(self.inspector_url(), **self.ops_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["customer"]["id"], str(self.customer.id))
        self.assertEqual(response.data["events"]["total_units"], 1_000)
        self.assertEqual(response.data["windows"]["total_units"], 1_000)
        self.assertNotIn(str(other_invoice.id), [invoice["id"] for invoice in response.data["invoices"]])

    def test_billing_inspector_requires_ops_token(self):
        response = self.client.get(self.inspector_url())

        self.assertEqual(response.status_code, 401)

    def test_billing_inspector_late_event_warning(self):
        self.create_window(units=20_000)
        invoice = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)
        late_event = self.create_event("late-inspector-req", units=20_000)
        UsageEvent.objects.filter(id=late_event.id).update(ingested_at=invoice.issued_at + timedelta(hours=1))

        response = self.client.get(self.inspector_url(), **self.ops_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["warnings"]["late_events_count"], 1)
        self.assertEqual(
            response.data["windows"]["total_units"],
            UsageWindow.objects.filter(customer=self.customer).aggregate(total=Sum("total_units"))["total"],
        )
