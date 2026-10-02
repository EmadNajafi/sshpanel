from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_referrals_and_activation")]

    operations = [
        migrations.AddField(
            model_name="vpnaccount", name="traffic_limit_bytes",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
