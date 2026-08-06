from django.contrib import admin
from expenses.models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "status", "active_from", "active_to",
                    "qbo_customer", "company")
    list_filter = ("status", "company")
    search_fields = ("code", "name")
    readonly_fields = ("version", "created_at", "updated_at")
