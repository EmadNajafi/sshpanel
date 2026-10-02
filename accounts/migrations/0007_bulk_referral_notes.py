from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0006_custom_referral_codes")]

    operations = [
        migrations.AddField(
            model_name="vpnaccount",
            name="referral_note",
            field=models.TextField(blank=True, default=""),
        ),
    ]
