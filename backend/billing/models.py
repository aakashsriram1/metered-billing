import hashlib
import hmac
import secrets
import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


def hash_api_key(raw_key: str) -> str:
    return hmac.new(
        settings.API_KEY_HASH_SECRET.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class Customer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    price_plan = models.ForeignKey(
        "PricePlan",
        related_name="customers",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} <{self.email}>"

    @property
    def is_authenticated(self):
        return True


class PricePlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    free_units = models.PositiveIntegerField(default=10_000)
    tier_1_limit = models.PositiveIntegerField(default=100_000)
    tier_1_price_micros = models.PositiveIntegerField(default=1_000)
    tier_2_price_micros = models.PositiveIntegerField(default=500)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class ApiKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, related_name="api_keys", on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    key_prefix = models.CharField(max_length=16, db_index=True)
    key_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def create_key(cls, customer: Customer, name: str):
        raw_key = f"sk_test_{secrets.token_urlsafe(32)}"
        api_key = cls.objects.create(
            customer=customer,
            name=name,
            key_prefix=raw_key[:16],
            key_hash=hash_api_key(raw_key),
        )
        return api_key, raw_key

    def __str__(self):
        return f"{self.name} ({self.key_prefix}...)"


class UsageEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.CharField(max_length=255, unique=True)
    customer = models.ForeignKey(Customer, related_name="usage_events", on_delete=models.CASCADE)
    api_key = models.ForeignKey(ApiKey, related_name="usage_events", on_delete=models.CASCADE)
    endpoint = models.CharField(max_length=255)
    units = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    timestamp = models.DateTimeField()
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["customer", "timestamp"]),
            models.Index(fields=["api_key", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.request_id} ({self.units} units)"


class UsageWindow(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, related_name="usage_windows", on_delete=models.CASCADE)
    api_key = models.ForeignKey(ApiKey, related_name="usage_windows", on_delete=models.CASCADE)
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    total_units = models.PositiveIntegerField(default=0)
    event_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "api_key", "window_start"],
                name="unique_usage_window_customer_api_key_hour",
            ),
        ]
        indexes = [
            models.Index(fields=["customer", "window_start"]),
            models.Index(fields=["api_key", "window_start"]),
        ]

    def __str__(self):
        return f"{self.customer} {self.window_start} ({self.total_units} units)"


class Invoice(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_ISSUED = "issued"
    STATUS_PAID = "paid"
    STATUS_VOID = "void"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_ISSUED, "Issued"),
        (STATUS_PAID, "Paid"),
        (STATUS_VOID, "Void"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, related_name="invoices", on_delete=models.CASCADE)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    total_cents = models.IntegerField(default=0)
    issued_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "period_start", "period_end"],
                name="unique_invoice_customer_period",
            ),
        ]
        indexes = [
            models.Index(fields=["customer", "period_start"]),
        ]

    def __str__(self):
        return f"{self.customer} invoice {self.period_start:%Y-%m-%d}"


class InvoiceLineItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, related_name="line_items", on_delete=models.CASCADE)
    description = models.CharField(max_length=255)
    units = models.IntegerField()
    unit_price_micros = models.IntegerField()
    amount_cents = models.IntegerField()
    metadata = models.JSONField(default=dict)
    overridden_at = models.DateTimeField(null=True, blank=True)
    override_reason = models.TextField(blank=True)
    overridden_by = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.description}: {self.amount_cents} cents"


class Credit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, related_name="credits", on_delete=models.CASCADE)
    invoice = models.ForeignKey(Invoice, related_name="credits", on_delete=models.CASCADE, null=True, blank=True)
    amount_cents = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    reason = models.TextField()
    created_by = models.CharField(max_length=255)
    idempotency_key = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "idempotency_key"],
                name="unique_credit_customer_idempotency_key",
            ),
        ]
        indexes = [
            models.Index(fields=["customer", "created_at"]),
        ]

    def __str__(self):
        return f"{self.customer} credit {self.amount_cents} cents"


class AuditLog(models.Model):
    """Append-only audit row for money-moving ops actions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.CharField(max_length=255)
    action = models.CharField(max_length=255)
    object_type = models.CharField(max_length=255)
    object_id = models.CharField(max_length=255)
    before_json = models.JSONField(default=dict)
    after_json = models.JSONField(default=dict)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "audit log"
        verbose_name_plural = "audit logs"

    def save(self, *args, **kwargs):
        if self.pk and AuditLog.objects.filter(pk=self.pk).exists():
            raise ValueError("AuditLog rows are append-only and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog rows are append-only and cannot be deleted.")

    def __str__(self):
        return f"{self.action} by {self.actor} at {self.created_at}"


class WebhookEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=255)
    payload_hash = models.CharField(max_length=64)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.provider_event_id} ({self.event_type})"


class JobRun(models.Model):
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_name = models.CharField(max_length=255)
    lock_key = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict)

    @classmethod
    def start(cls, job_name: str, lock_key: str):
        from django.db import transaction

        now = timezone.now()
        with transaction.atomic():
            job, _ = cls.objects.select_for_update().get_or_create(
                lock_key=lock_key,
                defaults={
                    "job_name": job_name,
                    "status": cls.STATUS_RUNNING,
                    "started_at": now,
                },
            )
            if job.status == cls.STATUS_RUNNING and job.finished_at is None and job.started_at != now:
                return None
            job.job_name = job_name
            job.status = cls.STATUS_RUNNING
            job.started_at = now
            job.finished_at = None
            job.metadata = {}
            job.save(update_fields=["job_name", "status", "started_at", "finished_at", "metadata"])
            return job

    def mark_succeeded(self, metadata=None):
        self.status = self.STATUS_SUCCEEDED
        self.finished_at = timezone.now()
        self.metadata = metadata or {}
        self.save(update_fields=["status", "finished_at", "metadata"])

    def mark_failed(self, error: str):
        self.status = self.STATUS_FAILED
        self.finished_at = timezone.now()
        self.metadata = {"error": error}
        self.save(update_fields=["status", "finished_at", "metadata"])

    def __str__(self):
        return f"{self.job_name} ({self.status})"
