from django.conf import settings
from django.db import models
from django.utils import timezone
import hashlib
import unicodedata


def normalized_referral_code(value):
    code = unicodedata.normalize("NFC", value or "").strip()
    if "\x00" in code:
        raise ValueError("A referral code cannot contain NUL.")
    return code


def referral_code_digest(value):
    code = normalized_referral_code(value)
    return hashlib.sha256(code.casefold().encode("utf-8")).hexdigest() if code else None


class VpnAccount(models.Model):
    username = models.CharField(max_length=32, unique=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    expires_at = models.DateTimeField(null=True, blank=True)
    valid_days = models.PositiveIntegerField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    referral_code = models.TextField(blank=True, default="")
    referral_code_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    referral_note = models.TextField(blank=True, default="")
    referred_by = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="referrals")
    max_connections = models.PositiveIntegerField(null=True, blank=True)
    traffic_limit_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    password_ciphertext = models.TextField(blank=True)

    def __str__(self):
        return self.username

    def save(self, *args, **kwargs):
        self.referral_code = normalized_referral_code(self.referral_code)
        self.referral_code_hash = referral_code_digest(self.referral_code)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "referral_code" in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"referral_code_hash"}
        super().save(*args, **kwargs)

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
