from django.db import migrations, models
import django.db.models.deletion
import secrets


def assign_referral_codes(apps, schema_editor):
    account_model = apps.get_model("accounts", "VpnAccount")
    for account in account_model.objects.filter(referral_code__isnull=True):
        code = secrets.token_hex(6).upper()
        while account_model.objects.filter(referral_code=code).exists():
            code = secrets.token_hex(6).upper()
        account_model.objects.filter(pk=account.pk).update(referral_code=code)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_vpnaccount_password_ciphertext")]

    operations = [
        migrations.AddField(model_name="vpnaccount", name="valid_days", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="vpnaccount", name="activated_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="vpnaccount", name="referral_code", field=models.CharField(blank=True, max_length=12, null=True, unique=True)),
        migrations.AddField(model_name="vpnaccount", name="referred_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="referrals", to="accounts.vpnaccount")),
        migrations.RunPython(assign_referral_codes, migrations.RunPython.noop),
    ]
