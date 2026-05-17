from datetime import datetime, timedelta, timezone as datetime_timezone

from django.utils import timezone
from rest_framework.test import APITestCase

from .management.commands.aggregate_usage import Command as AggregateUsageCommand
from .models import ApiKey, Customer, Invoice, PricePlan, UsageEvent, UsageWindow
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
