import time
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Run lightweight scheduled billing jobs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one worker tick and exit.")

    def handle(self, *args, **options):
        while True:
            self.run_tick()
            if options["once"]:
                self.stdout.write("run_worker --once complete.")
                return
            time.sleep(60)

    def run_tick(self):
        now = timezone.now()
        aggregate_start = now - timedelta(hours=48)

        self.stdout.write(f"[{now.isoformat()}] worker tick started.")
        self.stdout.write(
            f"Running aggregate_usage from {aggregate_start.isoformat()} to {now.isoformat()}."
        )
        call_command(
            "aggregate_usage",
            start=aggregate_start.isoformat(),
            end=now.isoformat(),
            stdout=self.stdout,
        )

        if now.day != 1:
            self.stdout.write("Not the first day of the month; skipping invoice generation.")
            return

        period_end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_start = (period_end - timedelta(days=1)).replace(day=1)
        self.stdout.write(
            f"First day of month; running generate_invoices from {period_start.isoformat()} "
            f"to {period_end.isoformat()}."
        )
        call_command(
            "generate_invoices",
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            stdout=self.stdout,
        )
