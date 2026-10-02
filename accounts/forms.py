import re
from django import forms
from decimal import Decimal


USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def valid_referral_text(value):
    if "\x00" in value:
        raise forms.ValidationError("A referral code cannot contain NUL.")
    return value


class CreateAccountForm(forms.Form):
    username = forms.CharField(max_length=32, help_text="SSH login name, e.g. ali or vpn_ali")
    password = forms.CharField(widget=forms.PasswordInput, max_length=256, strip=False)
    valid_days = forms.IntegerField(min_value=1, max_value=36500, label="Active days")
    max_connections = forms.IntegerField(min_value=0, max_value=10000, initial=0,
                                         label="Simultaneous connections", help_text="0 means unlimited.")
    traffic_gb = forms.DecimalField(required=False, min_value=Decimal("0.001"), max_value=Decimal("100000"),
                                    max_digits=9, decimal_places=3, label="Traffic limit (GiB)",
                                    help_text="Leave blank for unlimited traffic.")
    referral_code = forms.CharField(required=False, label="This user's referral code",
                                    help_text="Choose any unique code. Leave blank to set it later.")
    referred_by_code = forms.CharField(required=False, label="Introduced by referral code")
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

    def clean_referral_code(self):
        return valid_referral_text(self.cleaned_data["referral_code"])

    def clean_referred_by_code(self):
        return valid_referral_text(self.cleaned_data["referred_by_code"])


class BulkCreateAccountForm(forms.Form):
    count = forms.IntegerField(min_value=1, max_value=100, label="Number of users")
    prefix = forms.CharField(max_length=20, initial="user", label="Username prefix")
    start_number = forms.IntegerField(min_value=0, max_value=999999999, initial=1000, label="Minimum number")
    password = forms.CharField(required=False, max_length=256, strip=False, widget=forms.PasswordInput,
                               label="Fixed password", help_text="Optional. Leave blank to generate a unique password per user.")
    password_mode = forms.ChoiceField(choices=(("digits", "Numbers"), ("mixed", "Letters and numbers")),
                                      initial="digits", widget=forms.RadioSelect, label="Generated password")
    password_length = forms.IntegerField(min_value=4, max_value=64, initial=8, label="Password length")
    max_connections = forms.IntegerField(min_value=0, max_value=10000, initial=0,
                                         label="Simultaneous connections", help_text="0 means unlimited.")
    traffic_gb = forms.DecimalField(required=False, min_value=Decimal("0.001"), max_value=Decimal("100000"),
                                    max_digits=9, decimal_places=3, label="Traffic limit (GiB)",
                                    help_text="Leave blank for unlimited traffic.")
    referral_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                    label="Referral text for all users",
                                    help_text="Optional. The same text is saved for every user in this batch.")
    valid_days = forms.IntegerField(min_value=1, max_value=36500, label="Active days")
    start_on_first_connection = forms.BooleanField(required=False, label="Start validity on first connection")

    def clean_prefix(self):
        value = self.cleaned_data["prefix"]
        if not USERNAME_RE.fullmatch(value):
            raise forms.ValidationError("Use lowercase letters, digits, underscores or hyphens; start with a letter or underscore.")
        return value

    def clean_password(self):
        value = self.cleaned_data["password"]
        if any(character in value for character in "\r\n\x00"):
            raise forms.ValidationError("The password cannot contain a line break or NUL character.")
        return value

    def clean_referral_note(self):
        return valid_referral_text(self.cleaned_data["referral_note"])


class PasswordForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, max_length=256, strip=False)

    def clean_password(self):
        value = self.cleaned_data["password"]
        if any(character in value for character in "\r\n\x00"):
            raise forms.ValidationError("The password cannot contain a line break or NUL character.")
        return value


class EditAccountForm(forms.Form):
    referral_code = forms.CharField(required=False, label="This user's referral code",
                                    help_text="Set your own code, or clear the field to remove it.")
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
        required=False, min_value=0, max_value=10000, label="Simultaneous connections",
        help_text="0 means unlimited. Leave blank to keep the current limit.",
    )
    traffic_gb = forms.DecimalField(
        required=False, min_value=Decimal("0"), max_value=Decimal("100000"),
        max_digits=9, decimal_places=3, label="Traffic limit (GiB)",
        help_text="Leave blank to keep the current limit; use 0 for unlimited.",
    )

    def clean_password(self):
        value = self.cleaned_data["password"]
        if any(character in value for character in "\r\n\x00"):
            raise forms.ValidationError("The password cannot contain a line break or NUL character.")
        return value

    def clean_referral_code(self):
        return valid_referral_text(self.cleaned_data["referral_code"])
