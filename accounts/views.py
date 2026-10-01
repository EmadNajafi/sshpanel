from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.conf import settings
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone

from .forms import CreateAccountForm, EditAccountForm, PasswordForm
from .models import AuditEvent, VpnAccount
from .services import ProvisionError, call_helper
from .system_metrics import get_system_metrics


staff_required = user_passes_test(lambda user: user.is_active and user.is_staff, login_url="login")


@staff_required
def dashboard(request):
    accounts = list(VpnAccount.objects.select_related("created_by").order_by("username"))
    return render(request, "dashboard.html", {
        "accounts": accounts,
        "account_total": len(accounts),
        "account_active": sum(account.enabled and not account.is_expired for account in accounts),
        "metrics": get_system_metrics(),
        "events": AuditEvent.objects.select_related("actor")[:20],
        "create_form": CreateAccountForm(),
        "vpn_ssh_port": settings.VPN_SSH_PORT,
    })


@staff_required
def system_metrics(request):
    return JsonResponse(get_system_metrics())


def record(request, username, action, succeeded):
    AuditEvent.objects.create(actor=request.user, username=username, action=action, succeeded=succeeded)


@staff_required
@require_POST
def create_account(request):
    form = CreateAccountForm(request.POST)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return redirect("dashboard")
    username = form.cleaned_data["username"]
    if VpnAccount.objects.filter(username=username).exists():
        messages.error(request, "This account already exists.")
        return redirect("dashboard")
    try:
        expires_at = timezone.now() + timedelta(days=form.cleaned_data["valid_days"])
        max_connections = form.cleaned_data["max_connections"]
        call_helper(
            "create", username, form.cleaned_data["password"],
            expires_at=int(expires_at.timestamp()), max_connections=max_connections,
        )
        try:
            VpnAccount.objects.create(
                username=username, created_by=request.user,
                expires_at=expires_at, max_connections=max_connections,
            )
        except IntegrityError:
            call_helper("delete", username)
            raise ProvisionError("The account could not be saved.")
    except ProvisionError as exc:
        record(request, username, "create", False)
        messages.error(request, str(exc))
    else:
        record(request, username, "create", True)
        messages.success(request, f"Created {username}.")
    return redirect("dashboard")


@staff_required
@require_POST
def edit_account(request, pk):
    account = get_object_or_404(VpnAccount, pk=pk)
    form = EditAccountForm(request.POST)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return redirect("dashboard")

    days = form.cleaned_data["valid_days"]
    expires_at = timezone.now() + timedelta(days=days) if days is not None else account.expires_at
    max_connections = form.cleaned_data["max_connections"]
    if max_connections is None:
        max_connections = account.max_connections
    password = form.cleaned_data["password"] or None
    if max_connections == account.max_connections and expires_at == account.expires_at and password is None:
        messages.info(request, f"No changes to {account.username}.")
        return redirect("dashboard")
    try:
        call_helper(
            "update", account.username, password,
            expires_at=int(expires_at.timestamp()) if days is not None else None,
            max_connections=max_connections, enabled=account.enabled,
        )
    except ProvisionError as exc:
        record(request, account.username, "update", False)
        messages.error(request, str(exc))
    else:
        account.expires_at = expires_at
        account.max_connections = max_connections
        account.save(update_fields=["expires_at", "max_connections"])
        record(request, account.username, "update", True)
        messages.success(request, f"Updated {account.username}.")
    return redirect("dashboard")


def account_action(request, pk, action, enabled=None):
    account = get_object_or_404(VpnAccount, pk=pk)
    try:
        call_helper(action, account.username)
    except ProvisionError as exc:
        record(request, account.username, action, False)
        messages.error(request, str(exc))
    else:
        if enabled is None:
            account.delete()
        else:
            account.enabled = enabled
            account.save(update_fields=["enabled"])
        record(request, account.username, action, True)
        messages.success(request, f"{action.capitalize()}d {account.username}.")
    return redirect("dashboard")


@staff_required
@require_POST
def disable_account(request, pk):
    return account_action(request, pk, "disable", False)


@staff_required
@require_POST
def enable_account(request, pk):
    return account_action(request, pk, "enable", True)


@staff_required
@require_POST
def delete_account(request, pk):
    return account_action(request, pk, "delete")


@staff_required
@require_POST
def reset_password(request, pk):
    account = get_object_or_404(VpnAccount, pk=pk)
    form = PasswordForm(request.POST)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return redirect("dashboard")
    try:
        call_helper("password", account.username, form.cleaned_data["password"])
    except ProvisionError as exc:
        record(request, account.username, "password", False)
        messages.error(request, str(exc))
    else:
        record(request, account.username, "password", True)
        messages.success(request, f"Changed password for {account.username}.")
    return redirect("dashboard")
