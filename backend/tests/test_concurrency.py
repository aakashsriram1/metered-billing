from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone as datetime_timezone
from threading import Barrier

from django.conf import settings
from django.db import close_old_connections
from django.db.models import Sum
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from billing.models import ApiKey, Credit, Customer, Invoice, InvoiceLineItem, PricePlan, UsageEvent, UsageWindow
from billing.services import generate_invoice_for_customer


class ConcurrencyCorrectnessTests(TransactionTestCase):
    def test_concurrent_duplicate_request_id_creates_one_event(self):
        customer = Customer.objects.create(name="Concurrent Alpha", email="concurrent-alpha@example.com")
        _, raw_key = ApiKey.create_key(customer, "Concurrent key")
        barrier = Barrier(10)
        payload = {
            "events": [
                {
                    "request_id": "concurrent-duplicate-request",
                    "endpoint": "/v1/chat",
                    "units": 42,
                    "timestamp": "2026-05-16T18:00:00Z",
                }
            ]
        }

        def post_event():
            close_old_connections()
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_key}")
            barrier.wait(timeout=10)
            response = client.post("/v1/events", payload, format="json")
            close_old_connections()
            return response.status_code, response.data["accepted_count"], response.data["duplicate_count"]

        with ThreadPoolExecutor(max_workers=10) as executor:
            responses = list(executor.map(lambda _: post_event(), range(10)))

        self.assertTrue(all(status == 202 for status, _, _ in responses))
        self.assertEqual(sum(accepted for _, accepted, _ in responses), 1)
        self.assertEqual(sum(duplicates for _, _, duplicates in responses), 9)
        self.assertEqual(UsageEvent.objects.filter(request_id="concurrent-duplicate-request").count(), 1)
        self.assertEqual(UsageEvent.objects.aggregate(total=Sum("units"))["total"], 42)

    def test_concurrent_duplicate_invoice_linked_credit_creates_one_credit(self):
        price_plan = PricePlan.objects.create(name="Concurrent Default")
        customer = Customer.objects.create(
            name="Concurrent Billing",
            email="concurrent-billing@example.com",
            price_plan=price_plan,
        )
        api_key, _ = ApiKey.create_key(customer, "Concurrent billing key")
        period_start = datetime(2026, 5, 1, 0, 0, tzinfo=datetime_timezone.utc)
        period_end = datetime(2026, 6, 1, 0, 0, tzinfo=datetime_timezone.utc)
        UsageWindow.objects.create(
            customer=customer,
            api_key=api_key,
            window_start=datetime(2026, 5, 16, 18, 0, tzinfo=datetime_timezone.utc),
            window_end=datetime(2026, 5, 16, 19, 0, tzinfo=datetime_timezone.utc),
            total_units=20_000,
            event_count=5,
        )
        invoice = generate_invoice_for_customer(customer, period_start, period_end)
        original_total = invoice.total_cents
        barrier = Barrier(10)
        payload = {
            "amount_cents": 500,
            "reason": "Concurrent goodwill credit",
            "idempotency_key": "concurrent-credit-key",
            "invoice_id": str(invoice.id),
        }

        def post_credit():
            close_old_connections()
            client = APIClient()
            barrier.wait(timeout=10)
            response = client.post(
                f"/ops/customers/{customer.id}/credits",
                payload,
                format="json",
                HTTP_X_OPS_TOKEN=settings.OPS_TOKEN,
            )
            close_old_connections()
            return response.status_code, response.data["created"]

        with ThreadPoolExecutor(max_workers=10) as executor:
            responses = list(executor.map(lambda _: post_credit(), range(10)))

        self.assertTrue(all(status in (200, 201) for status, _ in responses))
        self.assertEqual(sum(1 for _, created in responses if created), 1)
        self.assertEqual(Credit.objects.filter(customer=customer, idempotency_key="concurrent-credit-key").count(), 1)
        self.assertEqual(
            InvoiceLineItem.objects.filter(invoice=invoice, metadata__type="credit", amount_cents=-500).count(),
            1,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.total_cents, max(original_total - 500, 0))
