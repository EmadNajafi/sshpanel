from django.conf import settings
from django.db import models
from django.utils import timezone


class VpnAccount(models.Model):
    username = models.CharField(max_length=32, unique=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    expires_at = models.DateTimeField(null=True, blank=True)
    max_connections = models.PositiveIntegerField(null=True, blank=True)
    password_ciphertext = models.TextField(blank=True)

    def __str__(self):
        return self.username

    @property
    def is_expired(self):
        return self.expires_at is not None and timezone.now() >= self.expires_at


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    username = models.CharField(max_length=32)
    action = models.CharField(max_length=20)
    succeeded = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
