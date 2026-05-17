from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import AuditLog, Credit, Customer, Invoice, InvoiceLineItem, PricePlan, UsageWindow


def micros_to_cents(units: int, price_micros: int) -> int:
    return (units * price_micros) // 10_000


def tiered_line_items(total_units: int, price_plan: PricePlan):
    free_units = min(total_units, price_plan.free_units)
    tier_1_units = min(max(total_units - price_plan.free_units, 0), price_plan.tier_1_limit - price_plan.free_units)
    tier_2_units = max(total_units - price_plan.tier_1_limit, 0)

    items = [
        {
            "description": f"First {price_plan.free_units:,} units free",
            "units": free_units,
            "unit_price_micros": 0,
            "amount_cents": 0,
            "metadata": {"tier": "free"},
        }
    ]

    if tier_1_units:
        items.append(
            {
                "description": f"Next {price_plan.tier_1_limit - price_plan.free_units:,} units",
                "units": tier_1_units,
                "unit_price_micros": price_plan.tier_1_price_micros,
                "amount_cents": micros_to_cents(tier_1_units, price_plan.tier_1_price_micros),
                "metadata": {"tier": "tier_1"},
            }
        )

    if tier_2_units:
        items.append(
            {
                "description": f"Units beyond {price_plan.tier_1_limit:,}",
                "units": tier_2_units,
                "unit_price_micros": price_plan.tier_2_price_micros,
                "amount_cents": micros_to_cents(tier_2_units, price_plan.tier_2_price_micros),
                "metadata": {"tier": "tier_2"},
            }
        )

    return items


def calculate_tiered_amount_cents(total_units: int, price_plan: PricePlan) -> int:
    return sum(item["amount_cents"] for item in tiered_line_items(total_units, price_plan))


def default_price_plan():
    plan, _ = PricePlan.objects.get_or_create(name="Default tiered plan")
    return plan


def generate_invoice_for_customer(customer: Customer, period_start, period_end):
    price_plan = customer.price_plan or default_price_plan()

    total_units = (
        UsageWindow.objects.filter(
            customer=customer,
            window_start__gte=period_start,
            window_start__lt=period_end,
        ).aggregate(total=Sum("total_units"))["total"]
        or 0
    )

    with transaction.atomic():
        invoice, _ = Invoice.objects.get_or_create(
            customer=customer,
            period_start=period_start,
            period_end=period_end,
        )

        InvoiceLineItem.objects.filter(invoice=invoice).delete()
        for item in tiered_line_items(total_units, price_plan):
            InvoiceLineItem.objects.create(invoice=invoice, **item)

        invoice.total_cents = sum(line.amount_cents for line in invoice.line_items.all())
        if invoice.status == Invoice.STATUS_DRAFT:
            invoice.status = Invoice.STATUS_ISSUED
            invoice.issued_at = timezone.now()
        invoice.save(update_fields=["total_cents", "status", "issued_at", "updated_at"])
        return invoice


def generate_invoices(period_start, period_end):
    invoices = []
    for customer in Customer.objects.all().select_related("price_plan"):
        invoices.append(generate_invoice_for_customer(customer, period_start, period_end))
    return invoices


def recompute_invoice_total(invoice: Invoice):
    invoice.total_cents = invoice.line_items.aggregate(total=Sum("amount_cents"))["total"] or 0
    invoice.save(update_fields=["total_cents", "updated_at"])
    return invoice


def issue_credit(customer: Customer, amount_cents: int, reason: str, idempotency_key: str, actor: str, invoice=None):
    with transaction.atomic():
        credit, created = Credit.objects.get_or_create(
            customer=customer,
            idempotency_key=idempotency_key,
            defaults={
                "invoice": invoice,
                "amount_cents": amount_cents,
                "reason": reason,
                "created_by": actor,
            },
        )
        if created:
            AuditLog.objects.create(
                actor=actor,
                action="credit.create",
                object_type="Credit",
                object_id=str(credit.id),
                before_json={},
                after_json={
                    "customer_id": str(customer.id),
                    "invoice_id": str(invoice.id) if invoice else None,
                    "amount_cents": amount_cents,
                    "reason": reason,
                    "idempotency_key": idempotency_key,
                },
                reason=reason,
            )
        return credit, created


def override_invoice_line_item(invoice_id, line_item_id, amount_cents: int, reason: str, actor: str):
    with transaction.atomic():
        line_item = (
            InvoiceLineItem.objects.select_for_update()
            .select_related("invoice")
            .get(id=line_item_id, invoice_id=invoice_id)
        )
        before = {
            "amount_cents": line_item.amount_cents,
            "override_reason": line_item.override_reason,
            "overridden_by": line_item.overridden_by,
            "overridden_at": line_item.overridden_at.isoformat() if line_item.overridden_at else None,
        }

        line_item.amount_cents = amount_cents
        line_item.override_reason = reason
        line_item.overridden_by = actor
        line_item.overridden_at = timezone.now()
        line_item.save(update_fields=["amount_cents", "override_reason", "overridden_by", "overridden_at", "updated_at"])

        invoice = recompute_invoice_total(line_item.invoice)
        AuditLog.objects.create(
            actor=actor,
            action="invoice_line_item.override",
            object_type="InvoiceLineItem",
            object_id=str(line_item.id),
            before_json=before,
            after_json={
                "amount_cents": line_item.amount_cents,
                "override_reason": line_item.override_reason,
                "overridden_by": line_item.overridden_by,
                "overridden_at": line_item.overridden_at.isoformat(),
                "invoice_total_cents": invoice.total_cents,
            },
            reason=reason,
        )
        return line_item
