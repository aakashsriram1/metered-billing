from django.contrib import admin

from .models import ApiKey, Customer, Invoice, InvoiceLineItem, PricePlan, UsageEvent, UsageWindow


@admin.register(PricePlan)
class PricePlanAdmin(admin.ModelAdmin):
    list_display = ("name", "free_units", "tier_1_limit", "tier_1_price_micros", "tier_2_price_micros")
    search_fields = ("name",)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "created_at", "updated_at")
    search_fields = ("name", "email")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "customer", "key_prefix", "created_at", "revoked_at")
    list_filter = ("revoked_at", "created_at")
    search_fields = ("name", "customer__name", "customer__email", "key_prefix")
    readonly_fields = ("id", "key_prefix", "key_hash", "created_at")


@admin.register(UsageEvent)
class UsageEventAdmin(admin.ModelAdmin):
    list_display = ("request_id", "customer", "api_key", "endpoint", "units", "timestamp", "ingested_at")
    list_filter = ("endpoint", "timestamp", "ingested_at")
    search_fields = ("request_id", "customer__name", "customer__email", "api_key__key_prefix")
    readonly_fields = ("id", "ingested_at")


@admin.register(UsageWindow)
class UsageWindowAdmin(admin.ModelAdmin):
    list_display = ("customer", "api_key", "window_start", "window_end", "total_units", "event_count")
    list_filter = ("window_start", "window_end")
    search_fields = ("customer__name", "customer__email", "api_key__key_prefix")
    readonly_fields = ("id", "created_at", "updated_at")


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 0
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("customer", "period_start", "period_end", "status", "total_cents", "issued_at", "paid_at")
    list_filter = ("status", "period_start", "period_end")
    search_fields = ("customer__name", "customer__email")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [InvoiceLineItemInline]


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(admin.ModelAdmin):
    list_display = ("invoice", "description", "units", "unit_price_micros", "amount_cents")
    search_fields = ("description", "invoice__customer__name", "invoice__customer__email")
    readonly_fields = ("id", "created_at", "updated_at")
