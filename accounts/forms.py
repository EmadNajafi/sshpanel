import re
from django import forms


USERNAME_RE = re.compile(r"^vpn_[a-z][a-z0-9_]{2,26}$")


class CreateAccountForm(forms.Form):
    username = forms.CharField(max_length=32, help_text="Must start with vpn_, e.g. vpn_alice")
    password = forms.CharField(widget=forms.PasswordInput, min_length=12, strip=False)

    def clean_username(self):
        value = self.cleaned_data["username"]
        if not USERNAME_RE.fullmatch(value):
            raise forms.ValidationError("Use vpn_ followed by 3–27 lowercase letters, digits or underscores; first character must be a letter.")
        return value


class PasswordForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, min_length=12, strip=False)
