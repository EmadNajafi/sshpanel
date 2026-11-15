from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0010_vpnaccount_sort_order")]

    operations = [
        migrations.AddField(
            model_name="auditevent",
            name="telegram_user_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
