from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="vpnaccount",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="vpnaccount",
            name="max_connections",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
