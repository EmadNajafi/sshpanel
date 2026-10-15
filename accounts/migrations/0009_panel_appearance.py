from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0008_telegram_bot_config")]

    operations = [
        migrations.CreateModel(
            name="PanelAppearance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language", models.CharField(choices=[("fa", "فارسی"), ("en", "English")], default="fa", max_length=2)),
            ],
        ),
    ]
