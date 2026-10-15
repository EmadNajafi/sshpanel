from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_panel_appearance")]

    operations = [
        migrations.AddField(
            model_name="vpnaccount",
            name="sort_order",
            field=models.PositiveBigIntegerField(blank=True, db_index=True, null=True),
        ),
    ]
