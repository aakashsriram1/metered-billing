from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from billing.models import JobRun
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

        lock_key = f"generate_invoices:{period_start.isoformat()}:{period_end.isoformat()}"
        job = JobRun.start("generate_invoices", lock_key)
        if job is None:
            self.stdout.write("generate_invoices is already running for this period; skipped.")
            return

        try:
            invoices = generate_invoices(period_start, period_end)
            skipped = [invoice for invoice in invoices if getattr(invoice, "_generation_skipped", False)]
            for invoice in skipped:
                self.stdout.write(f"Skipped invoice {invoice.id}: {invoice._generation_skip_reason}.")
            job.mark_succeeded({"invoice_count": len(invoices), "skipped_count": len(skipped)})
            self.stdout.write(self.style.SUCCESS(f"Generated {len(invoices)} invoice(s)."))
        except Exception as exc:
            job.mark_failed(str(exc))
            raise
