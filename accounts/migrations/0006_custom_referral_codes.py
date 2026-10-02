import hashlib
import unicodedata

from django.db import migrations, models


def index_existing_codes(apps, schema_editor):
    accounts = apps.get_model("accounts", "VpnAccount")
    for account in accounts.objects.exclude(referral_code__isnull=True).exclude(referral_code=""):
        code = unicodedata.normalize("NFC", account.referral_code).strip()
        digest = hashlib.sha256(code.casefold().encode("utf-8")).hexdigest() if code else None
        accounts.objects.filter(pk=account.pk).update(referral_code=code, referral_code_hash=digest)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_traffic_limit")]

    operations = [
        migrations.AlterField(model_name="vpnaccount", name="referral_code", field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="vpnaccount", name="referral_code_hash", field=models.CharField(blank=True, max_length=64, null=True, unique=True)),
        migrations.RunPython(index_existing_codes, migrations.RunPython.noop),
    ]
