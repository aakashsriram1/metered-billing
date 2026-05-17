import hashlib
import hmac
import json
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import ApiKeyAuthentication
from .models import ApiKey, AuditLog, Customer, Invoice, InvoiceLineItem, UsageEvent, UsageWindow, WebhookEvent
from .serializers import (
    CreditSerializer,
    InvoiceDetailSerializer,
    InvoiceListSerializer,
    UsageEventInputSerializer,
    UsageWindowSerializer,
)
from .services import issue_credit, override_invoice_line_item


class EventIngestionView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]

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


class UsageView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        start, end = self.date_range(request)
        page = self.positive_int(request.query_params.get("page"), default=1, maximum=None)
        page_size = self.positive_int(request.query_params.get("page_size"), default=50, maximum=200)

        queryset = UsageWindow.objects.filter(
            customer=request.customer,
            window_start__gte=start,
            window_start__lt=end,
        )

        api_key_id = request.query_params.get("api_key_id")
        if api_key_id:
            if not ApiKey.objects.filter(id=api_key_id, customer=request.customer).exists():
                queryset = queryset.none()
            else:
                queryset = queryset.filter(api_key_id=api_key_id)

        queryset = queryset.order_by("-window_start")
        total = queryset.count()
        offset = (page - 1) * page_size
        results = queryset[offset : offset + page_size]

        return Response(
            {
                "results": UsageWindowSerializer(results, many=True).data,
                "page": page,
                "page_size": page_size,
                "total": total,
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


class InvoiceListView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        invoices = Invoice.objects.filter(customer=request.customer).order_by("-period_start")
        return Response({"results": InvoiceListSerializer(invoices, many=True).data})


class InvoiceDetailView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, invoice_id):
        try:
            invoice = Invoice.objects.prefetch_related("line_items").get(
                id=invoice_id,
                customer=request.customer,
            )
        except Invoice.DoesNotExist:
            raise exceptions.NotFound("Invoice not found")

        return Response(InvoiceDetailSerializer(invoice).data)


class OpsCustomerListView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

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
                }
            )
        return Response({"results": results})


class OpsCustomerDetailView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, customer_id):
        try:
            customer = Customer.objects.get(id=customer_id)
        except Customer.DoesNotExist:
            raise exceptions.NotFound("Customer not found")

        recent_usage = customer.usage_windows.order_by("-window_start")[:24]
        invoices = customer.invoices.order_by("-period_start")
        credits = customer.credits.order_by("-created_at")

        return Response(
            {
                "id": str(customer.id),
                "name": customer.name,
                "email": customer.email,
                "created_at": customer.created_at,
                "recent_usage_windows": UsageWindowSerializer(recent_usage, many=True).data,
                "invoices": InvoiceListSerializer(invoices, many=True).data,
                "credits": CreditSerializer(credits, many=True).data,
                "anomaly": self.has_usage_anomaly(customer),
            }
        )

    def has_usage_anomaly(self, customer):
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


class OpsCreditView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, customer_id):
        try:
            customer = Customer.objects.get(id=customer_id)
        except Customer.DoesNotExist:
            raise exceptions.NotFound("Customer not found")

        amount_cents = int(request.data.get("amount_cents", 0))
        reason = request.data.get("reason", "").strip()
        idempotency_key = request.data.get("idempotency_key", "").strip()
        actor = request.headers.get("X-Ops-Actor", "ops@example.com")

        if amount_cents <= 0:
            raise exceptions.ValidationError({"amount_cents": "Must be positive."})
        if not reason:
            raise exceptions.ValidationError({"reason": "This field is required."})
        if not idempotency_key:
            raise exceptions.ValidationError({"idempotency_key": "This field is required."})

        credit, created = issue_credit(
            customer=customer,
            amount_cents=amount_cents,
            reason=reason,
            idempotency_key=idempotency_key,
            actor=actor,
        )
        return Response(
            {
                "credit": CreditSerializer(credit).data,
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class OpsLineItemOverrideView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

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
