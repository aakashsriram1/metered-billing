import base64
import binascii
import hashlib
import hmac
import json
from datetime import datetime, time, timedelta
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import exceptions
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import ApiKeyAuthentication, OpsTokenAuthentication
from .models import ApiKey, AuditLog, Customer, Invoice, InvoiceLineItem, JobRun, UsageEvent, UsageWindow, WebhookEvent
from .serializers import (
    BillingInspectorAuditLogSerializer,
    BillingInspectorCreditSerializer,
    BillingInspectorInvoiceSerializer,
    BillingInspectorJobRunSerializer,
    BillingInspectorSerializer,
    CreditSerializer,
    InvoiceDetailSerializer,
    InvoiceListSerializer,
    UsageEventInputSerializer,
    UsageWindowSerializer,
)
from .services import issue_credit, override_invoice_line_item


def has_usage_anomaly(customer):
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
    previous_daily_average = previous_30d_total / 30
    return previous_daily_average > 0 and last_24h_total >= previous_daily_average * 10


class CustomerScopedMixin:
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def scope_to_customer(self, queryset):
        return queryset.filter(customer=self.request.customer)


class OpsScopedMixin:
    authentication_classes = [OpsTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]


class EventIngestionView(CustomerScopedMixin, APIView):
    def post(self, request):
        events = request.data.get("events", [])
        serializer = UsageEventInputSerializer(data=events, many=True)
        serializer.is_valid(raise_exception=True)

        accepted_count = 0
        duplicate_count = 0

        for event in serializer.validated_data:
            try:
                with transaction.atomic():
                    UsageEvent.objects.create(
                        request_id=event["request_id"],
                        customer=request.customer,
                        api_key=request.api_key,
                        endpoint=event["endpoint"],
                        units=event["units"],
                        timestamp=event["timestamp"],
                    )
                accepted_count += 1
            except IntegrityError:
                duplicate_count += 1

        return Response(
            {
                "accepted_count": accepted_count,
                "duplicate_count": duplicate_count,
                "total_received": len(serializer.validated_data),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class UsageView(CustomerScopedMixin, APIView):
    def get(self, request):
        start, end = self.date_range(request)
        page_size = self.positive_int(request.query_params.get("page_size"), default=50, maximum=200)
        cursor = self.decode_cursor(request.query_params.get("cursor"))

        queryset = self.scope_to_customer(UsageWindow.objects.all()).filter(
            window_start__gte=start,
            window_start__lt=end,
        )

        api_key_id = request.query_params.get("api_key_id")
        if api_key_id:
            if not ApiKey.objects.filter(id=api_key_id, customer=request.customer).exists():
                queryset = queryset.none()
            else:
                queryset = queryset.filter(api_key_id=api_key_id)

        if cursor:
            cursor_start, cursor_id = cursor
            queryset = queryset.filter(
                Q(window_start__lt=cursor_start) | Q(window_start=cursor_start, id__lt=cursor_id)
            )

        queryset = queryset.order_by("-window_start", "-id")
        page_rows = list(queryset[: page_size + 1])
        has_more = len(page_rows) > page_size
        results = page_rows[:page_size]
        next_cursor = self.encode_cursor(results[-1]) if has_more and results else None

        return Response(
            {
                "results": UsageWindowSerializer(results, many=True).data,
                "page_size": page_size,
                "next_cursor": next_cursor,
                "has_more": has_more,
            }
        )

    def date_range(self, request):
        start_param = request.query_params.get("start")
        end_param = request.query_params.get("end")

        if start_param:
            start = self.parse_timestamp(start_param)
        else:
            now = timezone.now()
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        if end_param:
            end = self.parse_timestamp(end_param)
        else:
            end = timezone.now()

        return start, end

    def parse_timestamp(self, value):
        parsed = parse_datetime(value)
        if parsed is None:
            raise exceptions.ValidationError({"detail": f"Invalid timestamp: {value}"})
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed

    def positive_int(self, value, default, maximum):
        if value is None:
            return default
        try:
            parsed = int(value)
        except ValueError:
            return default
        parsed = max(parsed, 1)
        if maximum is not None:
            parsed = min(parsed, maximum)
        return parsed

    def encode_cursor(self, window):
        payload = json.dumps(
            {
                "window_start": window.window_start.isoformat(),
                "id": str(window.id),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")

    def decode_cursor(self, value):
        if not value:
            return None
        try:
            padded = value + "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
            window_start = self.parse_timestamp(payload["window_start"])
            cursor_id = UUID(payload["id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, binascii.Error):
            raise exceptions.ValidationError({"cursor": "Invalid cursor."})
        return window_start, cursor_id


class InvoiceListView(CustomerScopedMixin, APIView):
    def get(self, request):
        invoices = self.scope_to_customer(Invoice.objects.all()).order_by("-period_start")
        return Response({"results": InvoiceListSerializer(invoices, many=True).data})


class InvoiceDetailView(CustomerScopedMixin, APIView):
    def get(self, request, invoice_id):
        try:
            invoice = self.scope_to_customer(Invoice.objects.prefetch_related("line_items")).get(id=invoice_id)
        except Invoice.DoesNotExist:
            raise exceptions.NotFound("Invoice not found")

        return Response(InvoiceDetailSerializer(invoice).data)


class OpsCustomerListView(OpsScopedMixin, APIView):
    def get(self, request):
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        customers = Customer.objects.annotate(invoice_count=Count("invoices")).order_by("name")
        results = []
        for customer in customers:
            current_month_usage = (
                UsageWindow.objects.filter(customer=customer, window_start__gte=month_start)
                .aggregate(total=Sum("total_units"))["total"]
                or 0
            )
            results.append(
                {
                    "id": str(customer.id),
                    "name": customer.name,
                    "email": customer.email,
                    "created_at": customer.created_at,
                    "current_month_usage_total": current_month_usage,
                    "invoice_count": customer.invoice_count,
                    "anomaly": has_usage_anomaly(customer),
                }
            )
        return Response({"results": results})


class OpsCustomerDetailView(OpsScopedMixin, APIView):
    def get(self, request, customer_id):
        try:
            customer = Customer.objects.get(id=customer_id)
        except Customer.DoesNotExist:
            raise exceptions.NotFound("Customer not found")

        recent_usage = customer.usage_windows.order_by("-window_start")[:24]
        invoices = customer.invoices.prefetch_related("line_items").order_by("-period_start")
        credits = customer.credits.order_by("-created_at")

        return Response(
            {
                "id": str(customer.id),
                "name": customer.name,
                "email": customer.email,
                "created_at": customer.created_at,
                "recent_usage_windows": UsageWindowSerializer(recent_usage, many=True).data,
                "invoices": InvoiceDetailSerializer(invoices, many=True).data,
                "credits": CreditSerializer(credits, many=True).data,
                "anomaly": has_usage_anomaly(customer),
            }
        )


class OpsBillingInspectorView(OpsScopedMixin, APIView):
    def get(self, request, customer_id):
        try:
            customer = Customer.objects.get(id=customer_id)
        except Customer.DoesNotExist:
            raise exceptions.NotFound("Customer not found")

        period_start, period_end = self.period_range(request)

        events = UsageEvent.objects.filter(
            customer=customer,
            timestamp__gte=period_start,
            timestamp__lt=period_end,
        )
        windows = UsageWindow.objects.filter(
            customer=customer,
            window_start__gte=period_start,
            window_start__lt=period_end,
        )
        invoices = customer.invoices.prefetch_related("line_items").filter(
            period_start=period_start,
            period_end=period_end,
        )
        credits = customer.credits.filter(
            Q(created_at__gte=period_start, created_at__lt=period_end) | Q(invoice__in=invoices)
        ).distinct()

        event_count = events.count()
        event_units = events.aggregate(total=Sum("units"))["total"] or 0
        window_count = windows.count()
        window_units = windows.aggregate(total=Sum("total_units"))["total"] or 0
        invoice_line_items = InvoiceLineItem.objects.filter(invoice__in=invoices)
        invoice_line_item_units = invoice_line_items.aggregate(total=Sum("units"))["total"] or 0

        data = {
            "customer": {
                "id": str(customer.id),
                "name": customer.name,
                "email": customer.email,
            },
            "period": {
                "start": period_start.date().isoformat(),
                "end": period_end.date().isoformat(),
            },
            "events": {
                "count": event_count,
                "total_units": event_units,
            },
            "windows": {
                "count": window_count,
                "total_units": window_units,
            },
            "invoices": BillingInspectorInvoiceSerializer(invoices, many=True).data,
            "credits": {
                "count": credits.count(),
                "items": BillingInspectorCreditSerializer(credits.order_by("-created_at"), many=True).data,
            },
            "overrides": {
                "count": invoice_line_items.filter(overridden_at__isnull=False).count(),
            },
            "audit_logs": BillingInspectorAuditLogSerializer(
                self.audit_logs_for_customer(customer),
                many=True,
            ).data,
            "job_runs": BillingInspectorJobRunSerializer(JobRun.objects.order_by("-started_at")[:10], many=True).data,
            "warnings": {
                "raw_vs_window_mismatch": event_units != window_units,
                "window_vs_invoice_mismatch": invoices.exists() and window_units != invoice_line_item_units,
                "late_events_count": self.late_events_count(customer, invoices, period_start, period_end),
            },
        }
        return Response(BillingInspectorSerializer(data).data)

    def period_range(self, request):
        start_param = request.query_params.get("period_start")
        end_param = request.query_params.get("period_end")

        if start_param:
            start_date = parse_date(start_param)
            if start_date is None:
                raise exceptions.ValidationError({"period_start": "Expected YYYY-MM-DD."})
        else:
            now = timezone.now()
            start_date = now.replace(day=1).date()

        if end_param:
            end_date = parse_date(end_param)
            if end_date is None:
                raise exceptions.ValidationError({"period_end": "Expected YYYY-MM-DD."})
        else:
            end_date = timezone.now().date()

        period_start = timezone.make_aware(datetime.combine(start_date, time.min))
        period_end = timezone.make_aware(datetime.combine(end_date, time.min))
        if period_start >= period_end:
            raise exceptions.ValidationError({"period_end": "Must be after period_start."})
        return period_start, period_end

    def audit_logs_for_customer(self, customer):
        invoice_ids = [str(invoice_id) for invoice_id in customer.invoices.values_list("id", flat=True)]
        line_item_ids = [
            str(line_item_id)
            for line_item_id in InvoiceLineItem.objects.filter(invoice__customer=customer).values_list("id", flat=True)
        ]
        credit_ids = [str(credit_id) for credit_id in customer.credits.values_list("id", flat=True)]
        return AuditLog.objects.filter(
            Q(object_type="Invoice", object_id__in=invoice_ids)
            | Q(object_type="InvoiceLineItem", object_id__in=line_item_ids)
            | Q(object_type="Credit", object_id__in=credit_ids)
            | Q(after_json__customer_id=str(customer.id))
        ).order_by("-created_at")[:10]

    def late_events_count(self, customer, invoices, period_start, period_end):
        issued_invoice = invoices.filter(issued_at__isnull=False).order_by("issued_at").first()
        if issued_invoice is None:
            return 0
        return UsageEvent.objects.filter(
            customer=customer,
            timestamp__gte=period_start,
            timestamp__lt=period_end,
            ingested_at__gt=issued_invoice.issued_at,
        ).count()


class OpsCreditView(OpsScopedMixin, APIView):
    def post(self, request, customer_id):
        try:
            customer = Customer.objects.get(id=customer_id)
        except Customer.DoesNotExist:
            raise exceptions.NotFound("Customer not found")

        amount_cents = int(request.data.get("amount_cents", 0))
        reason = request.data.get("reason", "").strip()
        idempotency_key = request.data.get("idempotency_key", "").strip()
        invoice_id = request.data.get("invoice_id")
        actor = request.headers.get("X-Ops-Actor", "ops@example.com")

        if amount_cents <= 0:
            raise exceptions.ValidationError({"amount_cents": "Must be positive."})
        if not reason:
            raise exceptions.ValidationError({"reason": "This field is required."})
        if not idempotency_key:
            raise exceptions.ValidationError({"idempotency_key": "This field is required."})

        invoice = None
        if invoice_id:
            try:
                invoice = Invoice.objects.get(id=invoice_id, customer=customer)
            except Invoice.DoesNotExist:
                raise exceptions.NotFound("Invoice not found")

        credit, created = issue_credit(
            customer=customer,
            amount_cents=amount_cents,
            reason=reason,
            idempotency_key=idempotency_key,
            actor=actor,
            invoice=invoice,
        )
        return Response(
            {
                "credit": CreditSerializer(credit).data,
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class OpsLineItemOverrideView(OpsScopedMixin, APIView):
    def patch(self, request, invoice_id, line_item_id):
        reason = request.data.get("reason", "").strip()
        if not reason:
            raise exceptions.ValidationError({"reason": "This field is required."})

        try:
            amount_cents = int(request.data["amount_cents"])
        except (KeyError, TypeError, ValueError):
            raise exceptions.ValidationError({"amount_cents": "A valid integer is required."})

        actor = request.headers.get("X-Ops-Actor", "ops@example.com")
        try:
            line_item = override_invoice_line_item(
                invoice_id=invoice_id,
                line_item_id=line_item_id,
                amount_cents=amount_cents,
                reason=reason,
                actor=actor,
            )
        except InvoiceLineItem.DoesNotExist:
            raise exceptions.NotFound("Line item not found")

        return Response(
            {
                "id": str(line_item.id),
                "amount_cents": line_item.amount_cents,
                "override_reason": line_item.override_reason,
                "overridden_by": line_item.overridden_by,
                "overridden_at": line_item.overridden_at,
                "invoice_total_cents": line_item.invoice.total_cents,
            }
        )


class PaymentWebhookView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        provider_event_id = request.headers.get("X-Payment-Event-Id", "").strip()
        signature = request.headers.get("X-Payment-Signature", "").strip()
        raw_body = request.body

        if not provider_event_id:
            raise exceptions.ValidationError({"X-Payment-Event-Id": "This header is required."})
        if not self.valid_signature(raw_body, signature):
            return Response({"detail": "Invalid payment signature."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            raise exceptions.ValidationError({"detail": "Invalid JSON payload."})

        payload_hash = hashlib.sha256(raw_body).hexdigest()
        event_type = payload.get("type", "")

        with transaction.atomic():
            webhook_event, created = WebhookEvent.objects.get_or_create(
                provider_event_id=provider_event_id,
                defaults={
                    "event_type": event_type,
                    "payload_hash": payload_hash,
                },
            )
            if not created:
                return Response({"replay": True, "processed": False})

            processed = False
            if event_type == "invoice.paid":
                invoice = Invoice.objects.select_for_update().get(id=payload["invoice_id"])
                if invoice.status != Invoice.STATUS_PAID:
                    before = {
                        "status": invoice.status,
                        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
                    }
                    paid_at = self.parse_paid_at(payload.get("paid_at"))
                    invoice.status = Invoice.STATUS_PAID
                    invoice.paid_at = paid_at
                    invoice.save(update_fields=["status", "paid_at", "updated_at"])
                    AuditLog.objects.create(
                        actor="payment-webhook",
                        action="invoice.paid",
                        object_type="Invoice",
                        object_id=str(invoice.id),
                        before_json=before,
                        after_json={
                            "status": invoice.status,
                            "paid_at": invoice.paid_at.isoformat(),
                            "provider_event_id": provider_event_id,
                        },
                        reason="Payment webhook marked invoice paid.",
                    )
                    processed = True

            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=["processed_at"])

        return Response({"replay": False, "processed": processed})

    def valid_signature(self, raw_body, signature):
        if not signature:
            return False
        expected = hmac.new(
            settings.PAYMENT_WEBHOOK_SECRET.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_paid_at(self, value):
        if not value:
            return timezone.now()
        parsed = parse_datetime(value)
        if parsed is None:
            raise exceptions.ValidationError({"paid_at": "Invalid ISO timestamp."})
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed
