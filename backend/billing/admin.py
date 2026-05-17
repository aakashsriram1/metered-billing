from django.contrib import admin

from .models import ApiKey, Customer, UsageEvent


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
