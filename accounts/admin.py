from django.contrib import admin
from .models import AuditEvent, VpnAccount


@admin.register(VpnAccount)
class VpnAccountAdmin(admin.ModelAdmin):
    list_display = ("username", "enabled", "created_at", "created_by")
    readonly_fields = ("username", "enabled", "created_at", "created_by")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "username", "action", "succeeded")
    readonly_fields = ("created_at", "actor", "username", "action", "succeeded")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
