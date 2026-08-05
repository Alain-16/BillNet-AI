from django.contrib import admin

from accounting.models import AccountingReference


@admin.register(AccountingReference)
class AccountingReferenceAdmin(admin.ModelAdmin):
    list_display = ("name", "entity_type", "external_id", "active", "company",
                    "sync_token", "qbo_updated_at")
    list_filter = ("entity_type", "active", "company")
    search_fields = ("name", "external_id")
    ordering = ("entity_type", "name")
    readonly_fields = ("id", "created_at", "updated_at")
    list_per_page = 50
