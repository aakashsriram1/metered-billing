import random
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import (
    ApiKey,
    AuditLog,
    Credit,
    Customer,
    Invoice,
    InvoiceLineItem,
    PricePlan,
    UsageEvent,
    UsageWindow,
    WebhookEvent,
    hash_api_key,
)
from billing.services import generate_invoices, issue_credit, override_invoice_line_item


class Command(BaseCommand):
    help = "Seed deterministic local demo data for customers, usage, invoices, credits, and ops workflows."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing billing demo data before seeding.")

    def handle(self, *args, **options):
        rng = random.Random(20260517)
        now = timezone.now()
        start = now - timedelta(days=60)
        raw_keys = []

        if options["reset"]:
            self.reset_data()

        price_plan, _ = PricePlan.objects.update_or_create(
            name="Standard",
            defaults={
                "free_units": 10_000,
                "tier_1_limit": 100_000,
                "tier_1_price_micros": 1_000,
                "tier_2_price_micros": 500,
            },
        )

        customers = self.create_customers(price_plan)
        for customer_index, customer in enumerate(customers):
            key_count = 1 + (customer_index % 3)
            for key_index in range(key_count):
                raw_key = f"sk_test_demo_{customer_index}_{key_index}_{rng.randrange(10**12):012d}"
                ApiKey.objects.get_or_create(
                    customer=customer,
                    name=f"Demo key {key_index + 1}",
                    key_prefix=raw_key[:16],
                    defaults={"key_hash": hash_api_key(raw_key)},
                )
                raw_keys.append((customer, raw_key))

        self.create_usage_events(customers, rng, now)
        call_command("aggregate_usage", start=start.isoformat(), end=(now + timedelta(hours=1)).isoformat())

        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous_month_end = current_month_start
        previous_month_start = (current_month_start - timedelta(days=1)).replace(day=1)
        generate_invoices(previous_month_start, previous_month_end)
        generate_invoices(current_month_start, now)

        self.create_ops_examples(customers)
        self.print_summary(customers, raw_keys)

    def reset_data(self):
        WebhookEvent.objects.all().delete()
        AuditLog.objects.all().delete()
        Credit.objects.all().delete()
        InvoiceLineItem.objects.all().delete()
        Invoice.objects.all().delete()
        UsageWindow.objects.all().delete()
        UsageEvent.objects.all().delete()
        ApiKey.objects.all().delete()
        Customer.objects.all().delete()
        PricePlan.objects.all().delete()

    def create_customers(self, price_plan):
        names = [
            ("Alpha Analytics", "billing@alpha.test"),
            ("Beacon Labs", "billing@beacon.test"),
            ("Cobalt Cloud", "billing@cobalt.test"),
            ("Delta Data", "billing@delta.test"),
            ("Echo Health", "billing@echo.test"),
            ("Flux Retail", "billing@flux.test"),
            ("Gamma AI", "billing@gamma.test"),
            ("Anomaly Systems", "billing@anomaly.test"),
        ]
        customers = []
        for name, email in names:
            customer, _ = Customer.objects.update_or_create(
                email=email,
                defaults={"name": name, "price_plan": price_plan},
            )
            customers.append(customer)
        return customers

    def create_usage_events(self, customers, rng, now):
        endpoints = ["/v1/chat", "/v1/embeddings", "/v1/search", "/v1/classify"]
        events = []

        for customer_index, customer in enumerate(customers):
            api_keys = list(customer.api_keys.order_by("name"))
            for day_offset in range(60, 0, -1):
                event_count = rng.randint(3, 8)
                for event_index in range(event_count):
                    api_key = rng.choice(api_keys)
                    timestamp = now - timedelta(
                        days=day_offset,
                        hours=rng.randint(0, 23),
                        minutes=rng.randint(0, 59),
                    )
                    units = self.random_units(rng)
                    events.append(
                        UsageEvent(
                            request_id=f"demo-{customer_index}-{day_offset}-{event_index}",
                            customer=customer,
                            api_key=api_key,
                            endpoint=rng.choice(endpoints),
                            units=units,
                            timestamp=timestamp,
                        )
                    )

        anomaly_customer = customers[-1]
        anomaly_api_key = anomaly_customer.api_keys.first()
        for event_index in range(40):
            events.append(
                UsageEvent(
                    request_id=f"demo-anomaly-last24h-{event_index}",
                    customer=anomaly_customer,
                    api_key=anomaly_api_key,
                    endpoint="/v1/chat",
                    units=5_000,
                    timestamp=now - timedelta(hours=rng.randint(0, 23), minutes=rng.randint(0, 59)),
                )
            )

        UsageEvent.objects.bulk_create(events, ignore_conflicts=True, batch_size=500)

    def random_units(self, rng):
        bucket = rng.random()
        if bucket < 0.7:
            return rng.randint(1, 50)
        if bucket < 0.93:
            return rng.randint(50, 500)
        return rng.randint(500, 5_000)

    def create_ops_examples(self, customers):
        customer = customers[0]
        invoice = customer.invoices.order_by("-period_start").first()
        if invoice:
            issue_credit(
                customer=customer,
                invoice=invoice,
                amount_cents=500,
                reason="Demo goodwill credit",
                idempotency_key="demo-credit-001",
                actor="ops-demo@example.com",
            )
            line_item = invoice.line_items.order_by("-amount_cents").first()
            if line_item:
                override_invoice_line_item(
                    invoice_id=invoice.id,
                    line_item_id=line_item.id,
                    amount_cents=max(line_item.amount_cents - 250, 0),
                    reason="Demo usage dispute adjustment",
                    actor="ops-demo@example.com",
                )

    def print_summary(self, customers, raw_keys):
        self.stdout.write(self.style.SUCCESS("Seeded demo billing data."))
        self.stdout.write("")
        self.stdout.write("Customers and demo API keys:")
        for customer, raw_key in raw_keys:
            self.stdout.write(f"- {customer.name} <{customer.email}>: {raw_key}")

        first_key = raw_keys[0][1]
        self.stdout.write("")
        self.stdout.write("Example API calls:")
        self.stdout.write(
            "curl -X POST http://localhost:8000/v1/events "
            f'-H "Authorization: Bearer {first_key}" '
            '-H "Content-Type: application/json" '
            """-d '{"events":[{"request_id":"manual_demo_req_1","endpoint":"/v1/chat","units":42,"timestamp":"2026-05-16T18:00:00Z"}]}'"""
        )
        self.stdout.write(f'curl -H "Authorization: Bearer {first_key}" http://localhost:8000/v1/usage')
        self.stdout.write(f'curl -H "Authorization: Bearer {first_key}" http://localhost:8000/v1/invoices')
