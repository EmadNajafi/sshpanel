from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_account_limits")]
    operations = [migrations.AddField(
        model_name="vpnaccount", name="password_ciphertext", field=models.TextField(blank=True),
    )]
