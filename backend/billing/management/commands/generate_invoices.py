from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from billing.services import generate_invoices


def parse_iso_datetime(value: str):
    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError(f"Invalid ISO timestamp: {value}")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


class Command(BaseCommand):
    help = "Generate monthly invoices from usage windows."

    def add_arguments(self, parser):
        parser.add_argument("--period-start", required=True, help="Inclusive ISO timestamp")
        parser.add_argument("--period-end", required=True, help="Exclusive ISO timestamp")

    def handle(self, *args, **options):
        period_start = parse_iso_datetime(options["period_start"])
        period_end = parse_iso_datetime(options["period_end"])

        if period_start >= period_end:
            raise CommandError("--period-start must be before --period-end")

        invoices = generate_invoices(period_start, period_end)
        self.stdout.write(self.style.SUCCESS(f"Generated {len(invoices)} invoice(s)."))
