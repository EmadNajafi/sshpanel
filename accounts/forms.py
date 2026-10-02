import re
from django import forms


USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class CreateAccountForm(forms.Form):
    username = forms.CharField(max_length=32, help_text="SSH login name, e.g. ali or vpn_ali")
    password = forms.CharField(widget=forms.PasswordInput, max_length=256, strip=False)
    valid_days = forms.IntegerField(min_value=1, max_value=36500, label="Active days")
    max_connections = forms.IntegerField(min_value=1, max_value=10000, label="Simultaneous connections")
    referral_code = forms.CharField(required=False, max_length=12, label="Referral code")
    start_on_first_connection = forms.BooleanField(required=False, label="Start validity on first connection")

    def clean_username(self):
        value = self.cleaned_data["username"]
        if not USERNAME_RE.fullmatch(value):
            raise forms.ValidationError("Use 1–32 lowercase letters, digits, underscores or hyphens; start with a letter or underscore.")
        return value

    def clean_password(self):
        value = self.cleaned_data["password"]
        if any(character in value for character in "\r\n\x00"):
            raise forms.ValidationError("The password cannot contain a line break or NUL character.")
        return value


class PasswordForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, max_length=256, strip=False)

    def clean_password(self):
        value = self.cleaned_data["password"]
        if any(character in value for character in "\r\n\x00"):
            raise forms.ValidationError("The password cannot contain a line break or NUL character.")
        return value


class EditAccountForm(forms.Form):
    password = forms.CharField(
        required=False, max_length=256, strip=False, widget=forms.PasswordInput,
        label="New password", help_text="Leave blank to keep the current password.",
    )
    valid_days = forms.IntegerField(
        required=False, min_value=1, max_value=36500,
        label="New validity (days)",
        help_text="Leave blank to keep the current expiry. Enter days to count from now.",
    )
    max_connections = forms.IntegerField(
        required=False, min_value=1, max_value=10000, label="Simultaneous connections",
        help_text="Leave blank to keep the current limit.",
    )

    def clean_password(self):
        value = self.cleaned_data["password"]
        if any(character in value for character in "\r\n\x00"):
            raise forms.ValidationError("The password cannot contain a line break or NUL character.")
        return value
