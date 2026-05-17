from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum
from django.db.models.functions import TruncHour
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from billing.models import UsageEvent, UsageWindow


def parse_iso_datetime(value: str):
    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError(f"Invalid ISO timestamp: {value}")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=timezone.utc)
    return parsed


class Command(BaseCommand):
    help = "Aggregate raw usage events into hourly usage windows."

    def add_arguments(self, parser):
        parser.add_argument("--start", help="Inclusive ISO timestamp")
        parser.add_argument("--end", help="Exclusive ISO timestamp")

    def handle(self, *args, **options):
        end = parse_iso_datetime(options["end"]) if options.get("end") else timezone.now()
        start = parse_iso_datetime(options["start"]) if options.get("start") else end - timedelta(hours=48)

        if start >= end:
            raise CommandError("--start must be before --end")

        buckets = (
            UsageEvent.objects.filter(timestamp__gte=start, timestamp__lt=end)
            .annotate(window_start=TruncHour("timestamp"))
            .values("customer_id", "api_key_id", "window_start")
            .annotate(total_units=Sum("units"), event_count=Count("id"))
            .order_by()
        )

        window_count = 0
        for bucket in buckets:
            window_start = bucket["window_start"]
            UsageWindow.objects.update_or_create(
                customer_id=bucket["customer_id"],
                api_key_id=bucket["api_key_id"],
                window_start=window_start,
                defaults={
                    "window_end": window_start + timedelta(hours=1),
                    "total_units": bucket["total_units"],
                    "event_count": bucket["event_count"],
                },
            )
            window_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Aggregated {window_count} usage window(s) from {start.isoformat()} to {end.isoformat()}."
            )
        )
