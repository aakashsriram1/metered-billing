from datetime import datetime, timezone as datetime_timezone

from django.utils import timezone
from rest_framework.test import APITestCase

from .management.commands.aggregate_usage import Command as AggregateUsageCommand
from .models import ApiKey, Customer, UsageEvent, UsageWindow


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
