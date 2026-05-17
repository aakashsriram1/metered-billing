from rest_framework import serializers

from .models import Credit, Invoice, InvoiceLineItem, UsageWindow


class UsageEventInputSerializer(serializers.Serializer):
    request_id = serializers.CharField(max_length=255)
    endpoint = serializers.CharField(max_length=255)
    units = serializers.IntegerField(min_value=1)
    timestamp = serializers.DateTimeField()


class UsageWindowSerializer(serializers.ModelSerializer):
    api_key_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = UsageWindow
        fields = ("window_start", "window_end", "api_key_id", "total_units", "event_count")


class InvoiceLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLineItem
        fields = ("id", "description", "units", "unit_price_micros", "amount_cents", "metadata")


class InvoiceListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = (
            "id",
            "period_start",
            "period_end",
            "status",
            "total_cents",
            "issued_at",
            "paid_at",
            "created_at",
            "updated_at",
        )


class InvoiceDetailSerializer(InvoiceListSerializer):
    line_items = InvoiceLineItemSerializer(many=True, read_only=True)

    class Meta(InvoiceListSerializer.Meta):
        fields = InvoiceListSerializer.Meta.fields + ("line_items",)


class CreditSerializer(serializers.ModelSerializer):
    invoice_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Credit
        fields = ("id", "invoice_id", "amount_cents", "reason", "created_by", "idempotency_key", "created_at")
