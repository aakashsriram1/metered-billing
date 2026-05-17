import hashlib
import hmac
import io
import json
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.conf import settings
from django.core.management import call_command
from django.db.models import Sum
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .management.commands.aggregate_usage import Command as AggregateUsageCommand
from .models import ApiKey, AuditLog, Credit, Customer, Invoice, JobRun, PricePlan, UsageEvent, UsageWindow, WebhookEvent
from .services import calculate_tiered_amount_cents, generate_invoice_for_customer


class EventIngestionTests(APITestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Alpha", email="alpha@example.com")
        self.api_key, self.raw_key = ApiKey.create_key(self.customer, "Alpha test key")
        self.other_customer = Customer.objects.create(name="Beta", email="beta@example.com")

    def auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.raw_key}")

    def payload(self, request_id="req_123", extra=None):
        event = {
            "request_id": request_id,
            "endpoint": "/v1/chat",
            "units": 42,
            "timestamp": timezone.now().isoformat().replace("+00:00", "Z"),
        }
        if extra:
            event.update(extra)
        return {"events": [event]}

    def test_valid_api_key_can_ingest_events(self):
        self.auth()

        response = self.client.post("/v1/events", self.payload(), format="json")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["accepted_count"], 1)
        self.assertEqual(response.data["duplicate_count"], 0)
        self.assertEqual(response.data["total_received"], 1)
        self.assertEqual(UsageEvent.objects.count(), 1)

    def test_wrong_api_key_fails(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer sk_test_wrong")

        response = self.client.post("/v1/events", self.payload(), format="json")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(UsageEvent.objects.count(), 0)

    def test_api_key_hash_is_hmac_not_raw_or_plain_sha256(self):
        self.assertNotEqual(self.api_key.key_hash, self.raw_key)
        self.assertNotEqual(self.api_key.key_hash, hashlib.sha256(self.raw_key.encode("utf-8")).hexdigest())

    def test_missing_api_key_returns_401(self):
        response = self.client.post("/v1/events", self.payload(), format="json")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(UsageEvent.objects.count(), 0)

    def test_duplicate_request_id_does_not_create_two_rows(self):
        self.auth()

        first = self.client.post("/v1/events", self.payload("req_dup"), format="json")
        second = self.client.post("/v1/events", self.payload("req_dup"), format="json")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.data["accepted_count"], 0)
        self.assertEqual(second.data["duplicate_count"], 1)
        self.assertEqual(UsageEvent.objects.filter(request_id="req_dup").count(), 1)

    def test_event_is_attached_to_authenticated_customer(self):
        self.auth()

        self.client.post("/v1/events", self.payload(), format="json")

        event = UsageEvent.objects.get(request_id="req_123")
        self.assertEqual(event.customer, self.customer)
        self.assertEqual(event.api_key, self.api_key)

    def test_payload_customer_id_cannot_spoof_another_customer(self):
        self.auth()

        self.client.post(
            "/v1/events",
            self.payload(extra={"customer_id": str(self.other_customer.id)}),
            format="json",
        )

        event = UsageEvent.objects.get(request_id="req_123")
        self.assertEqual(event.customer, self.customer)
        self.assertNotEqual(event.customer, self.other_customer)


class UsageAggregationTests(APITestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Alpha", email="alpha-agg@example.com")
        self.api_key, _ = ApiKey.create_key(self.customer, "Alpha key")
        self.other_customer = Customer.objects.create(name="Beta", email="beta-agg@example.com")
        self.other_api_key, _ = ApiKey.create_key(self.other_customer, "Beta key")
        self.command = AggregateUsageCommand()
        self.base_time = datetime(2026, 5, 16, 18, 15, tzinfo=datetime_timezone.utc)

    def create_event(self, request_id, customer=None, api_key=None, units=1, timestamp=None):
        return UsageEvent.objects.create(
            request_id=request_id,
            customer=customer or self.customer,
            api_key=api_key or self.api_key,
            endpoint="/v1/chat",
            units=units,
            timestamp=timestamp or self.base_time,
        )

    def aggregate(self):
        self.command.handle(
            start="2026-05-16T00:00:00Z",
            end="2026-05-17T00:00:00Z",
        )

    def test_creates_usage_window_from_events(self):
        self.create_event("agg_req_1", units=3)

        self.aggregate()

        self.assertEqual(UsageWindow.objects.count(), 1)

    def test_sums_units_and_counts_events(self):
        self.create_event("agg_req_1", units=3)
        self.create_event("agg_req_2", units=7)

        self.aggregate()

        window = UsageWindow.objects.get()
        self.assertEqual(window.total_units, 10)
        self.assertEqual(window.event_count, 2)

    def test_running_aggregation_twice_does_not_double_count(self):
        self.create_event("agg_req_1", units=3)

        self.aggregate()
        self.aggregate()

        window = UsageWindow.objects.get()
        self.assertEqual(window.total_units, 3)
        self.assertEqual(window.event_count, 1)
        self.assertEqual(UsageWindow.objects.count(), 1)

    def test_separate_customers_get_separate_windows(self):
        self.create_event("agg_req_1", units=3)
        self.create_event(
            "agg_req_2",
            customer=self.other_customer,
            api_key=self.other_api_key,
            units=5,
        )

        self.aggregate()

        self.assertEqual(UsageWindow.objects.count(), 2)
        self.assertEqual(
            UsageWindow.objects.get(customer=self.customer).total_units,
            3,
        )
        self.assertEqual(
            UsageWindow.objects.get(customer=self.other_customer).total_units,
            5,
        )

    def test_separate_api_keys_get_separate_windows(self):
        second_api_key, _ = ApiKey.create_key(self.customer, "Second Alpha key")
        self.create_event("agg_req_1", api_key=self.api_key, units=3)
        self.create_event("agg_req_2", api_key=second_api_key, units=5)

        self.aggregate()

        self.assertEqual(UsageWindow.objects.count(), 2)
        self.assertEqual(
            UsageWindow.objects.get(api_key=self.api_key).total_units,
            3,
        )
        self.assertEqual(
            UsageWindow.objects.get(api_key=second_api_key).total_units,
            5,
        )


class UsageEndpointTests(APITestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Alpha Usage", email="alpha-usage@example.com")
        self.api_key, self.raw_key = ApiKey.create_key(self.customer, "Alpha usage key")
        self.second_api_key, _ = ApiKey.create_key(self.customer, "Alpha second key")
        self.other_customer = Customer.objects.create(name="Beta Usage", email="beta-usage@example.com")
        self.other_api_key, _ = ApiKey.create_key(self.other_customer, "Beta usage key")

        self.window_time = datetime(2026, 5, 16, 18, 0, tzinfo=datetime_timezone.utc)
        self.old_window_time = datetime(2026, 4, 16, 18, 0, tzinfo=datetime_timezone.utc)

    def auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.raw_key}")

    def create_window(self, customer=None, api_key=None, window_start=None, total_units=10, event_count=1):
        start = window_start or self.window_time
        return UsageWindow.objects.create(
            customer=customer or self.customer,
            api_key=api_key or self.api_key,
            window_start=start,
            window_end=start + timedelta(hours=1),
            total_units=total_units,
            event_count=event_count,
        )

    def test_authenticated_customer_can_list_own_usage(self):
        self.create_window(total_units=123, event_count=4)
        self.auth()

        response = self.client.get("/v1/usage")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["total_units"], 123)
        self.assertEqual(response.data["results"][0]["event_count"], 4)

    def test_customer_cannot_see_another_customers_usage(self):
        self.create_window(customer=self.other_customer, api_key=self.other_api_key, total_units=999)
        self.auth()

        response = self.client.get("/v1/usage")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 0)
        self.assertEqual(response.data["results"], [])

    def test_api_key_id_filter_works_for_own_api_key(self):
        self.create_window(api_key=self.api_key, total_units=10)
        self.create_window(api_key=self.second_api_key, total_units=20)
        self.auth()

        response = self.client.get(f"/v1/usage?api_key_id={self.second_api_key.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["api_key_id"], str(self.second_api_key.id))
        self.assertEqual(response.data["results"][0]["total_units"], 20)

    def test_api_key_id_from_another_customer_does_not_leak_data(self):
        self.create_window(customer=self.other_customer, api_key=self.other_api_key, total_units=999)
        self.auth()

        response = self.client.get(f"/v1/usage?api_key_id={self.other_api_key.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 0)
        self.assertEqual(response.data["results"], [])

    def test_date_range_filter_works(self):
        self.create_window(window_start=self.window_time, total_units=10)
        self.create_window(window_start=self.old_window_time, total_units=20)
        self.auth()

        response = self.client.get(
            "/v1/usage?start=2026-05-01T00:00:00Z&end=2026-06-01T00:00:00Z"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["total_units"], 10)

    def test_pagination_works(self):
        for hour in range(3):
            self.create_window(
                window_start=datetime(2026, 5, 16, hour, 0, tzinfo=datetime_timezone.utc),
                total_units=hour + 1,
            )
        self.auth()

        response = self.client.get("/v1/usage?page=2&page_size=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["page"], 2)
        self.assertEqual(response.data["page_size"], 1)
        self.assertEqual(response.data["total"], 3)
        self.assertEqual(len(response.data["results"]), 1)


class BillingTests(APITestCase):
    def setUp(self):
        self.price_plan = PricePlan.objects.create(name="Default")
        self.customer = Customer.objects.create(
            name="Alpha Billing",
            email="alpha-billing@example.com",
            price_plan=self.price_plan,
        )
        self.api_key, self.raw_key = ApiKey.create_key(self.customer, "Alpha billing key")
        self.other_customer = Customer.objects.create(
            name="Beta Billing",
            email="beta-billing@example.com",
            price_plan=self.price_plan,
        )
        self.other_api_key, _ = ApiKey.create_key(self.other_customer, "Beta billing key")
        self.period_start = datetime(2026, 5, 1, 0, 0, tzinfo=datetime_timezone.utc)
        self.period_end = datetime(2026, 6, 1, 0, 0, tzinfo=datetime_timezone.utc)

    def auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.raw_key}")

    def create_window(self, customer=None, api_key=None, total_units=0):
        return UsageWindow.objects.create(
            customer=customer or self.customer,
            api_key=api_key or self.api_key,
            window_start=datetime(2026, 5, 16, 18, 0, tzinfo=datetime_timezone.utc),
            window_end=datetime(2026, 5, 16, 19, 0, tzinfo=datetime_timezone.utc),
            total_units=total_units,
            event_count=1,
        )

    def test_pricing_tiers_are_correct(self):
        self.assertEqual(calculate_tiered_amount_cents(0, self.price_plan), 0)
        self.assertEqual(calculate_tiered_amount_cents(10_000, self.price_plan), 0)
        self.assertEqual(calculate_tiered_amount_cents(10_001, self.price_plan), 0)
        self.assertEqual(calculate_tiered_amount_cents(100_000, self.price_plan), 9_000)
        self.assertEqual(calculate_tiered_amount_cents(150_000, self.price_plan), 11_500)

    def test_invoice_generation_creates_invoice(self):
        self.create_window(total_units=20_000)

        invoice = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)

        self.assertEqual(invoice.customer, self.customer)
        self.assertEqual(invoice.status, Invoice.STATUS_ISSUED)
        self.assertIsNotNone(invoice.issued_at)

    def test_invoice_generation_creates_correct_line_items(self):
        self.create_window(total_units=150_000)

        invoice = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)

        line_items = list(invoice.line_items.order_by("unit_price_micros"))
        self.assertEqual(len(line_items), 3)
        self.assertEqual(invoice.total_cents, 11_500)
        self.assertEqual(sum(item.amount_cents for item in line_items), 11_500)

    def test_running_invoice_generation_twice_does_not_double_bill(self):
        self.create_window(total_units=150_000)

        first = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)
        second = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)

        self.assertEqual(first.id, second.id)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(second.line_items.count(), 3)
        self.assertEqual(second.total_cents, 11_500)

    def test_paid_invoice_is_not_changed_by_generate_invoices(self):
        self.create_window(total_units=20_000)
        invoice = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)
        original_total = invoice.total_cents
        original_line_count = invoice.line_items.count()
        invoice.status = Invoice.STATUS_PAID
        invoice.paid_at = timezone.now()
        invoice.save(update_fields=["status", "paid_at", "updated_at"])
        UsageWindow.objects.filter(customer=self.customer, api_key=self.api_key).update(total_units=150_000)

        regenerated = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)

        regenerated.refresh_from_db()
        self.assertEqual(regenerated.status, Invoice.STATUS_PAID)
        self.assertEqual(regenerated.total_cents, original_total)
        self.assertEqual(regenerated.line_items.count(), original_line_count)
        self.assertTrue(regenerated._generation_skipped)

    def test_overridden_line_item_is_preserved_by_generate_invoices(self):
        self.create_window(total_units=20_000)
        invoice = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)
        line_item = invoice.line_items.order_by("-amount_cents").first()
        line_item.amount_cents = 1234
        line_item.override_reason = "Manual adjustment"
        line_item.overridden_by = "ops@example.com"
        line_item.overridden_at = timezone.now()
        line_item.save(update_fields=["amount_cents", "override_reason", "overridden_by", "overridden_at", "updated_at"])

        regenerated = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)

        line_item.refresh_from_db()
        regenerated.refresh_from_db()
        self.assertEqual(line_item.amount_cents, 1234)
        self.assertEqual(regenerated.total_cents, sum(item.amount_cents for item in regenerated.line_items.all()))
        self.assertTrue(regenerated._generation_skipped)

    def test_customer_can_list_own_invoices(self):
        self.create_window(total_units=20_000)
        generate_invoice_for_customer(self.customer, self.period_start, self.period_end)
        self.auth()

        response = self.client.get("/v1/invoices")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["total_cents"], 1_000)

    def test_customer_can_retrieve_own_invoice_detail(self):
        self.create_window(total_units=20_000)
        invoice = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)
        self.auth()

        response = self.client.get(f"/v1/invoices/{invoice.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(invoice.id))
        self.assertIn("line_items", response.data)
        self.assertGreater(len(response.data["line_items"]), 0)

    def test_customer_cannot_retrieve_another_customers_invoice(self):
        self.create_window(customer=self.other_customer, api_key=self.other_api_key, total_units=20_000)
        invoice = generate_invoice_for_customer(self.other_customer, self.period_start, self.period_end)
        self.auth()

        response = self.client.get(f"/v1/invoices/{invoice.id}")

        self.assertEqual(response.status_code, 404)


class OpsTests(APITestCase):
    def setUp(self):
        self.price_plan = PricePlan.objects.create(name="Ops Default")
        self.customer = Customer.objects.create(
            name="Alpha Ops",
            email="alpha-ops@example.com",
            price_plan=self.price_plan,
        )
        self.api_key, _ = ApiKey.create_key(self.customer, "Alpha ops key")
        self.period_start = datetime(2026, 5, 1, 0, 0, tzinfo=datetime_timezone.utc)
        self.period_end = datetime(2026, 6, 1, 0, 0, tzinfo=datetime_timezone.utc)
        self.window = UsageWindow.objects.create(
            customer=self.customer,
            api_key=self.api_key,
            window_start=datetime(2026, 5, 16, 18, 0, tzinfo=datetime_timezone.utc),
            window_end=datetime(2026, 5, 16, 19, 0, tzinfo=datetime_timezone.utc),
            total_units=20_000,
            event_count=5,
        )
        self.invoice = generate_invoice_for_customer(self.customer, self.period_start, self.period_end)

    def ops_headers(self, **extra):
        headers = {"HTTP_X_OPS_TOKEN": settings.OPS_TOKEN}
        headers.update(extra)
        return headers

    def test_ops_can_list_customers(self):
        response = self.client.get("/ops/customers", **self.ops_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["name"], "Alpha Ops")
        self.assertEqual(response.data["results"][0]["invoice_count"], 1)

    def test_missing_ops_token_rejected(self):
        response = self.client.get("/ops/customers")

        self.assertEqual(response.status_code, 401)

    def test_invalid_ops_token_rejected(self):
        response = self.client.get("/ops/customers", HTTP_X_OPS_TOKEN="wrong")

        self.assertEqual(response.status_code, 401)

    def test_valid_ops_token_accepted(self):
        response = self.client.get("/ops/customers", **self.ops_headers())

        self.assertEqual(response.status_code, 200)

    def test_ops_can_retrieve_customer_detail(self):
        response = self.client.get(f"/ops/customers/{self.customer.id}", **self.ops_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(self.customer.id))
        self.assertIn("recent_usage_windows", response.data)
        self.assertIn("invoices", response.data)
        self.assertIn("credits", response.data)
        self.assertIn("anomaly", response.data)

    def test_credit_creates_credit_and_audit_log(self):
        response = self.client.post(
            f"/ops/customers/{self.customer.id}/credits",
            {
                "amount_cents": 500,
                "reason": "Goodwill credit",
                "idempotency_key": "credit_123",
            },
            format="json",
            **self.ops_headers(HTTP_X_OPS_ACTOR="ops-user@example.com"),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Credit.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)
        audit = AuditLog.objects.get()
        self.assertEqual(audit.actor, "ops-user@example.com")
        self.assertEqual(audit.action, "credit.create")
        self.assertEqual(audit.after_json["amount_cents"], 500)

    def test_duplicate_credit_idempotency_key_does_not_double_credit(self):
        payload = {
            "amount_cents": 500,
            "reason": "Goodwill credit",
            "idempotency_key": "credit_123",
        }

        first = self.client.post(f"/ops/customers/{self.customer.id}/credits", payload, format="json", **self.ops_headers())
        second = self.client.post(f"/ops/customers/{self.customer.id}/credits", payload, format="json", **self.ops_headers())

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Credit.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertFalse(second.data["created"])

    def test_credit_endpoint_requires_token(self):
        response = self.client.post(
            f"/ops/customers/{self.customer.id}/credits",
            {
                "amount_cents": 500,
                "reason": "Goodwill credit",
                "idempotency_key": "credit_123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    def test_invoice_linked_credit_creates_negative_line_item(self):
        response = self.client.post(
            f"/ops/customers/{self.customer.id}/credits",
            {
                "amount_cents": 500,
                "reason": "Invoice goodwill credit",
                "idempotency_key": "invoice-credit-123",
                "invoice_id": str(self.invoice.id),
            },
            format="json",
            **self.ops_headers(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(self.invoice.line_items.filter(amount_cents=-500, metadata__type="credit").exists())

    def test_invoice_linked_credit_recomputes_invoice_total(self):
        original_total = self.invoice.total_cents

        self.client.post(
            f"/ops/customers/{self.customer.id}/credits",
            {
                "amount_cents": 500,
                "reason": "Invoice goodwill credit",
                "idempotency_key": "invoice-credit-123",
                "invoice_id": str(self.invoice.id),
            },
            format="json",
            **self.ops_headers(),
        )

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.total_cents, max(original_total - 500, 0))

    def test_duplicate_invoice_linked_credit_does_not_double_apply(self):
        payload = {
            "amount_cents": 500,
            "reason": "Invoice goodwill credit",
            "idempotency_key": "invoice-credit-123",
            "invoice_id": str(self.invoice.id),
        }

        self.client.post(f"/ops/customers/{self.customer.id}/credits", payload, format="json", **self.ops_headers())
        self.client.post(f"/ops/customers/{self.customer.id}/credits", payload, format="json", **self.ops_headers())

        self.assertEqual(Credit.objects.filter(idempotency_key="invoice-credit-123").count(), 1)
        self.assertEqual(self.invoice.line_items.filter(metadata__type="credit").count(), 1)

    def test_invoice_id_from_another_customer_is_rejected(self):
        other_customer = Customer.objects.create(name="Other Ops", email="other-ops@example.com", price_plan=self.price_plan)
        other_key, _ = ApiKey.create_key(other_customer, "Other ops key")
        UsageWindow.objects.create(
            customer=other_customer,
            api_key=other_key,
            window_start=datetime(2026, 5, 16, 18, 0, tzinfo=datetime_timezone.utc),
            window_end=datetime(2026, 5, 16, 19, 0, tzinfo=datetime_timezone.utc),
            total_units=20_000,
            event_count=5,
        )
        other_invoice = generate_invoice_for_customer(other_customer, self.period_start, self.period_end)

        response = self.client.post(
            f"/ops/customers/{self.customer.id}/credits",
            {
                "amount_cents": 500,
                "reason": "Bad invoice id",
                "idempotency_key": "bad-invoice-id",
                "invoice_id": str(other_invoice.id),
            },
            format="json",
            **self.ops_headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Credit.objects.filter(idempotency_key="bad-invoice-id").exists())

    def test_line_item_override_updates_amount(self):
        line_item = self.invoice.line_items.order_by("-amount_cents").first()

        response = self.client.patch(
            f"/ops/invoices/{self.invoice.id}/line-items/{line_item.id}",
            {
                "amount_cents": 2500,
                "reason": "Corrected usage dispute",
            },
            format="json",
            **self.ops_headers(HTTP_X_OPS_ACTOR="ops-user@example.com"),
        )

        self.assertEqual(response.status_code, 200)
        line_item.refresh_from_db()
        self.assertEqual(line_item.amount_cents, 2500)
        self.assertEqual(line_item.override_reason, "Corrected usage dispute")
        self.assertEqual(line_item.overridden_by, "ops-user@example.com")

    def test_line_item_override_creates_audit_log_with_before_after(self):
        line_item = self.invoice.line_items.order_by("-amount_cents").first()
        original_amount = line_item.amount_cents

        self.client.patch(
            f"/ops/invoices/{self.invoice.id}/line-items/{line_item.id}",
            {
                "amount_cents": 2500,
                "reason": "Corrected usage dispute",
            },
            format="json",
            **self.ops_headers(),
        )

        audit = AuditLog.objects.get(action="invoice_line_item.override")
        self.assertEqual(audit.before_json["amount_cents"], original_amount)
        self.assertEqual(audit.after_json["amount_cents"], 2500)
        self.assertEqual(audit.reason, "Corrected usage dispute")

    def test_override_requires_reason(self):
        line_item = self.invoice.line_items.order_by("-amount_cents").first()

        response = self.client.patch(
            f"/ops/invoices/{self.invoice.id}/line-items/{line_item.id}",
            {"amount_cents": 2500},
            format="json",
            **self.ops_headers(),
        )

        self.assertEqual(response.status_code, 400)

    def test_line_item_override_requires_token(self):
        line_item = self.invoice.line_items.order_by("-amount_cents").first()

        response = self.client.patch(
            f"/ops/invoices/{self.invoice.id}/line-items/{line_item.id}",
            {
                "amount_cents": 2500,
                "reason": "Corrected usage dispute",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    def test_invoice_total_is_recomputed_after_override(self):
        line_item = self.invoice.line_items.order_by("-amount_cents").first()

        self.client.patch(
            f"/ops/invoices/{self.invoice.id}/line-items/{line_item.id}",
            {
                "amount_cents": 2500,
                "reason": "Corrected usage dispute",
            },
            format="json",
            **self.ops_headers(),
        )

        self.invoice.refresh_from_db()
        self.assertEqual(
            self.invoice.total_cents,
            sum(item.amount_cents for item in self.invoice.line_items.all()),
        )


class JobRunTests(APITestCase):
    def test_second_run_with_existing_running_lock_skips(self):
        JobRun.objects.create(
            job_name="aggregate_usage",
            lock_key="aggregate_usage",
            status=JobRun.STATUS_RUNNING,
            started_at=timezone.now(),
        )
        out = io.StringIO()

        call_command("aggregate_usage", start="2026-05-16T00:00:00Z", end="2026-05-17T00:00:00Z", stdout=out)

        self.assertIn("already running", out.getvalue())
        self.assertEqual(JobRun.objects.get(lock_key="aggregate_usage").status, JobRun.STATUS_RUNNING)

    def test_successful_job_marks_jobrun_succeeded(self):
        out = io.StringIO()

        call_command("aggregate_usage", start="2026-05-16T00:00:00Z", end="2026-05-17T00:00:00Z", stdout=out)

        job = JobRun.objects.get(lock_key="aggregate_usage")
        self.assertEqual(job.status, JobRun.STATUS_SUCCEEDED)
        self.assertIsNotNone(job.finished_at)


@override_settings(PAYMENT_WEBHOOK_SECRET="test-payment-secret")
class PaymentWebhookTests(APITestCase):
    def setUp(self):
        self.price_plan = PricePlan.objects.create(name="Webhook Default")
        self.customer = Customer.objects.create(
            name="Alpha Webhook",
            email="alpha-webhook@example.com",
            price_plan=self.price_plan,
        )
        self.api_key, _ = ApiKey.create_key(self.customer, "Alpha webhook key")
        period_start = datetime(2026, 5, 1, 0, 0, tzinfo=datetime_timezone.utc)
        period_end = datetime(2026, 6, 1, 0, 0, tzinfo=datetime_timezone.utc)
        UsageWindow.objects.create(
            customer=self.customer,
            api_key=self.api_key,
            window_start=datetime(2026, 5, 16, 18, 0, tzinfo=datetime_timezone.utc),
            window_end=datetime(2026, 5, 16, 19, 0, tzinfo=datetime_timezone.utc),
            total_units=20_000,
            event_count=5,
        )
        self.invoice = generate_invoice_for_customer(self.customer, period_start, period_end)

    def raw_payload(self, invoice=None):
        payload = {
            "type": "invoice.paid",
            "invoice_id": str((invoice or self.invoice).id),
            "paid_at": "2026-05-16T18:00:00Z",
        }
        return json.dumps(payload).encode("utf-8")

    def signature(self, raw_body):
        return hmac.new(b"test-payment-secret", raw_body, hashlib.sha256).hexdigest()

    def post_webhook(self, provider_event_id="evt_123", raw_body=None, signature=None):
        raw_body = raw_body or self.raw_payload()
        signature = signature if signature is not None else self.signature(raw_body)
        return self.client.post(
            "/webhooks/payments",
            data=raw_body,
            content_type="application/json",
            HTTP_X_PAYMENT_EVENT_ID=provider_event_id,
            HTTP_X_PAYMENT_SIGNATURE=signature,
        )

    def test_valid_signature_marks_invoice_paid(self):
        response = self.post_webhook()

        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertIsNotNone(self.invoice.paid_at)
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_missing_signature_returns_401(self):
        response = self.post_webhook(signature="")

        self.assertEqual(response.status_code, 401)
        self.invoice.refresh_from_db()
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)

    def test_invalid_signature_returns_401(self):
        response = self.post_webhook(signature="not-valid")

        self.assertEqual(response.status_code, 401)
        self.invoice.refresh_from_db()
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)

    def test_same_provider_event_id_replay_does_not_double_apply(self):
        first = self.post_webhook(provider_event_id="evt_replay")
        second = self.post_webhook(provider_event_id="evt_replay")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.data["replay"])
        self.assertTrue(second.data["replay"])
        self.assertEqual(WebhookEvent.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="invoice.paid").count(), 1)

    def test_webhook_for_already_paid_invoice_is_safe(self):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.paid_at = timezone.now()
        self.invoice.save(update_fields=["status", "paid_at", "updated_at"])

        response = self.post_webhook(provider_event_id="evt_already_paid")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["processed"])
        self.assertEqual(AuditLog.objects.filter(action="invoice.paid").count(), 0)
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_audit_log_created_when_invoice_is_marked_paid(self):
        self.post_webhook(provider_event_id="evt_audit")

        audit = AuditLog.objects.get(action="invoice.paid")
        self.assertEqual(audit.actor, "payment-webhook")
        self.assertEqual(audit.object_id, str(self.invoice.id))
        self.assertEqual(audit.before_json["status"], Invoice.STATUS_ISSUED)
        self.assertEqual(audit.after_json["status"], Invoice.STATUS_PAID)
        self.assertEqual(audit.after_json["provider_event_id"], "evt_audit")


class SeedDemoDataTests(APITestCase):
    def seed(self):
        out = io.StringIO()
        call_command("seed_demo_data", reset=True, stdout=out)
        return out.getvalue()

    def test_command_creates_customers(self):
        self.seed()

        self.assertGreaterEqual(Customer.objects.count(), 5)
        self.assertTrue(Customer.objects.filter(name="Anomaly Systems").exists())

    def test_command_creates_api_keys(self):
        output = self.seed()

        self.assertGreaterEqual(ApiKey.objects.count(), Customer.objects.count())
        self.assertIn("sk_test_demo_", output)

    def test_command_creates_usage_events_and_windows(self):
        self.seed()

        self.assertGreater(UsageEvent.objects.count(), 0)
        self.assertGreater(UsageWindow.objects.count(), 0)

    def test_command_creates_invoices(self):
        self.seed()

        self.assertGreater(Invoice.objects.count(), 0)
        self.assertTrue(Invoice.objects.filter(status=Invoice.STATUS_ISSUED).exists())

    def test_anomaly_customer_exists(self):
        self.seed()

        customer = Customer.objects.get(name="Anomaly Systems")
        now = timezone.now()
        last_24h_total = (
            UsageWindow.objects.filter(customer=customer, window_start__gte=now - timedelta(hours=24))
            .aggregate(total=Sum("total_units"))["total"]
            or 0
        )
        previous_30d_total = (
            UsageWindow.objects.filter(
                customer=customer,
                window_start__gte=now - timedelta(days=31),
                window_start__lt=now - timedelta(hours=24),
            ).aggregate(total=Sum("total_units"))["total"]
            or 0
        )
        self.assertGreater(previous_30d_total, 0)
        self.assertGreaterEqual(last_24h_total, (previous_30d_total / 30) * 10)
