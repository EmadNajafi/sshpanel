"""Force administrators off the publicly documented installer password."""

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

# The installer offers this password by default and the README publishes it.
DEFAULT_PASSWORDS = frozenset({"123456"})
SESSION_FLAG = "sshvpn_password_change_required"
CHANGE_MESSAGE = "Change the default administrator password before using the panel."


def is_default_password(password):
    return password in DEFAULT_PASSWORDS


class PanelLoginView(auth_views.LoginView):
    template_name = "login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if is_default_password(form.cleaned_data.get("password")):
            self.request.session[SESSION_FLAG] = True
            messages.warning(self.request, CHANGE_MESSAGE)
            return redirect("settings_admin")
        return response


class RequirePasswordChangeMiddleware:
    """Allow only the password form and sign-out while the default password is in use."""

    ALLOWED_NAMES = ("settings_admin", "change_admin_password", "logout", "set_panel_language")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.session.get(SESSION_FLAG) and request.user.is_authenticated:
            allowed = {reverse(name) for name in self.ALLOWED_NAMES}
            if request.path not in allowed:
                if not any(request.user.check_password(value) for value in DEFAULT_PASSWORDS):
                    # Changed elsewhere, for example through the Telegram bot.
                    del request.session[SESSION_FLAG]
                elif request.path_info.startswith("/api/"):
                    return JsonResponse({"error": CHANGE_MESSAGE}, status=403)
                else:
                    messages.warning(request, CHANGE_MESSAGE)
                    return redirect("settings_admin")
        return self.get_response(request)
