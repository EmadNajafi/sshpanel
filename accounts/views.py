from datetime import datetime, timedelta, timezone as datetime_timezone
import json
import math
import secrets
import string

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import user_passes_test
from django.conf import settings
from django.db import IntegrityError
from django.http import FileResponse, Http404, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.cache import never_cache
from django.utils import timezone

from .forms import BulkCreateAccountForm, CreateAccountForm, EditAccountForm, PasswordForm
from .backups import BACKUP_DIR, BACKUP_NAME, UPLOAD_DIR, BackupError, call_backup, list_backups, save_upload
from .models import AuditEvent, VpnAccount, normalized_referral_code, referral_code_digest
from .services import ProvisionError, call_helper, get_ssh_port
from .secrets import decrypt_password, encrypt_password
from .system_metrics import get_system_metrics


staff_required = user_passes_test(lambda user: user.is_active and user.is_staff, login_url="login")


def format_bytes(value):
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024


def get_account_usage():
    try:
        raw = json.loads(call_helper("usage-status"))
        return {username: {
            "connections": item["connections"],
            "ips": item.get("ips", []),
            "upload": format_bytes(item["upload_bytes"]),
            "download": format_bytes(item["download_bytes"]),
            "total": format_bytes(item["upload_bytes"] + item["download_bytes"]),
            "total_bytes": item["upload_bytes"] + item["download_bytes"],
        } for username, item in raw.items()}
    except (ProvisionError, ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return None


def sync_account_activation():
    """The PAM hook owns the first successful connection timestamp."""
    pending = VpnAccount.objects.filter(valid_days__isnull=False, activated_at__isnull=True)
    if not pending.exists():
        return
    try:
        policies = json.loads(call_helper("policy-status"))
    except (ProvisionError, ValueError, TypeError):
        return
    for account in pending:
        policy = policies.get(account.username)
        if not isinstance(policy, dict) or not isinstance(policy.get("activated_at"), int) or not isinstance(policy.get("expires_at"), int):
            continue
        account.activated_at = datetime.fromtimestamp(policy["activated_at"], tz=datetime_timezone.utc)
        account.expires_at = datetime.fromtimestamp(policy["expires_at"], tz=datetime_timezone.utc)
        account.save(update_fields=["activated_at", "expires_at"])


def account_summary(accounts, usage, now):
    inactive = sum(not account.enabled or (account.expires_at is not None and account.expires_at <= now) or
                   (usage is not None and account.traffic_limit_bytes is not None and
                    usage.get(account.username, {}).get("total_bytes", 0) >= account.traffic_limit_bytes)
                   for account in accounts)
    return {
        "total": len(accounts),
        "online": (sum(usage.get(account.username, {}).get("connections", 0) > 0 for account in accounts)
                   if usage is not None else None),
        "inactive": inactive,
    }


@never_cache
@staff_required
def dashboard(request):
    bulk_token = secrets.token_urlsafe(24)
    request.session["bulk_create_token"] = bulk_token
    sync_account_activation()
    accounts = list(VpnAccount.objects.select_related("created_by", "referred_by").order_by("created_at", "pk"))
    usage = get_account_usage()
    now = timezone.now()
    summary = account_summary(accounts, usage, now)
    referrals = {}
    for account in accounts:
        if account.referred_by_id:
            referrals.setdefault(account.referred_by_id, []).append(account)
    for account in accounts:
        account.usage = usage.get(account.username) if usage is not None else None
        account.traffic_limit_display = (format_bytes(account.traffic_limit_bytes)
                                         if account.traffic_limit_bytes is not None else None)
        account.traffic_exhausted = (account.traffic_limit_bytes is not None and account.usage is not None and
                                     account.usage.get("total_bytes", 0) >= account.traffic_limit_bytes)
        account.display_password = decrypt_password(account.password_ciphertext)
        account.referral_users = referrals.get(account.pk, [])
        account.days_remaining = (max(0, math.ceil((account.expires_at - now).total_seconds() / 86400))
                                  if account.expires_at is not None else None)
    search_query = request.GET.get("q", "").strip()
    needle = search_query.casefold()
    for account in accounts:
        account.matches_search = not needle or any(needle in value.casefold() for value in (
            account.username,
            account.referral_code,
            account.referral_note,
            account.referred_by.referral_code if account.referred_by else "",
            account.referred_by.username if account.referred_by else "",
        ))
    return render(request, "dashboard.html", {
        "accounts": accounts,
        "search_query": search_query,
        "search_result_count": sum(account.matches_search for account in accounts),
        "account_total": summary["total"],
        "account_online": summary["online"],
        "account_inactive": summary["inactive"],
        "metrics": get_system_metrics(),
        "create_form": CreateAccountForm(),
        "bulk_create_form": BulkCreateAccountForm(),
        "bulk_create_token": bulk_token,
        "vpn_ssh_port": get_ssh_port(),
    })


@staff_required
def audit_log(request):
    events = AuditEvent.objects.select_related("actor")
    page = Paginator(events, 25).get_page(request.GET.get("page"))
    return render(request, "audit_log.html", {"page": page})


@staff_required
def system_metrics(request):
    response = JsonResponse(get_system_metrics())
    response["Cache-Control"] = "no-store, private"
    return response


@staff_required
@require_GET
def account_usage(request):
    sync_account_activation()
    usage = get_account_usage()
    accounts = list(VpnAccount.objects.only("username", "enabled", "expires_at", "valid_days", "activated_at", "traffic_limit_bytes"))
    response = JsonResponse({"accounts": usage, "available": usage is not None,
                             "activated": [account.username for account in accounts if account.activated_at is not None],
                             "summary": account_summary(accounts, usage, timezone.now())})
    response["Cache-Control"] = "no-store, private"
    return response


def record(request, username, action, succeeded):
    AuditEvent.objects.create(actor=request.user, username=username, action=action, succeeded=succeeded)


def traffic_bytes(gigabytes):
    return int(gigabytes * 1024 ** 3) if gigabytes is not None else None


def referral_for(code):
    try:
        digest = referral_code_digest(code)
    except ValueError as exc:
        raise ProvisionError(str(exc)) from exc
    if digest is None:
        return None
    referral = VpnAccount.objects.filter(referral_code_hash=digest).first()
    if referral is None:
        raise ProvisionError("Referral code was not found.")
    return referral


def unique_referral_code(code, *, exclude_pk=None):
    try:
        code = normalized_referral_code(code)
    except ValueError as exc:
        raise ProvisionError(str(exc)) from exc
    digest = referral_code_digest(code)
    if digest is not None:
        matches = VpnAccount.objects.filter(referral_code_hash=digest)
        if exclude_pk is not None:
            matches = matches.exclude(pk=exclude_pk)
        if matches.exists():
            raise ProvisionError("This referral code is already in use.")
    return code


def provision_account(actor, username, password, valid_days, max_connections,
                      referral, first_connection, traffic_limit_bytes, own_referral_code="", referral_note=""):
    own_referral_code = unique_referral_code(own_referral_code)
    expires_at = None if first_connection else timezone.now() + timedelta(days=valid_days)
    stored_max_connections = max_connections or None
    options = {"max_connections": max_connections}
    if first_connection:
        options["valid_days"] = valid_days
    else:
        options["expires_at"] = int(expires_at.timestamp())
    if traffic_limit_bytes is not None:
        options["traffic_limit_bytes"] = traffic_limit_bytes
    call_helper("create", username, password, **options)
    try:
        return VpnAccount.objects.create(
            username=username, created_by=actor, expires_at=expires_at,
            valid_days=valid_days if first_connection else None,
            max_connections=stored_max_connections, traffic_limit_bytes=traffic_limit_bytes,
            referral_code=own_referral_code, referred_by=referral, referral_note=referral_note,
            password_ciphertext=encrypt_password(password),
        )
    except Exception as exc:
        try:
            call_helper("delete", username)
        except ProvisionError as cleanup_error:
            raise ProvisionError(f"Could not save {username}; its Linux account needs manual cleanup: {cleanup_error}") from exc
        raise ProvisionError(f"Could not save {username}.") from exc


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
        provision_account(
            request.user, username, form.cleaned_data["password"], form.cleaned_data["valid_days"],
            form.cleaned_data["max_connections"], referral_for(form.cleaned_data["referred_by_code"]),
            form.cleaned_data["start_on_first_connection"], traffic_bytes(form.cleaned_data["traffic_gb"]),
            form.cleaned_data["referral_code"],
        )
    except ProvisionError as exc:
        record(request, username, "create", False)
        messages.error(request, str(exc))
    else:
        record(request, username, "create", True)
        messages.success(request, f"Created {username}.")
    return redirect("dashboard")


def generated_password(mode, length):
    if mode == "digits":
        return "".join(secrets.choice(string.digits) for _ in range(length))
    letters = string.ascii_letters
    characters = [secrets.choice(letters), secrets.choice(string.digits)]
    characters.extend(secrets.choice(letters + string.digits) for _ in range(length - 2))
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


@staff_required
@require_POST
def bulk_create_accounts(request):
    submitted_token = request.POST.get("batch_token", "")
    saved_token = request.session.get("bulk_create_token", "")
    if not saved_token or not secrets.compare_digest(submitted_token, saved_token):
        messages.error(request, "This bulk request has already been processed. Open the form again.")
        return redirect("dashboard")
    request.session.pop("bulk_create_token", None)
    request.session.save()
    form = BulkCreateAccountForm(request.POST)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return redirect("dashboard")
    data = form.cleaned_data
    referral_note = data["referral_note"]
    numbers = secrets.SystemRandom().sample(range(data["start_number"], data["start_number"] + 1_000_000),
                                             data["count"])
    usernames = [f"{data['prefix']}{number}" for number in numbers]
    collisions = set(VpnAccount.objects.filter(username__in=usernames).values_list("username", flat=True))
    if collisions:
        messages.error(request, "These generated names already exist: " + ", ".join(sorted(collisions)))
        return redirect("dashboard")
    created = []
    used_passwords = set()
    failure = None
    for username in usernames:
        password = data["password"]
        if not password:
            for _ in range(100):
                password = generated_password(data["password_mode"], data["password_length"])
                if password not in used_passwords:
                    break
            else:
                failure = "Could not generate enough unique passwords. Increase the password length."
                break
            used_passwords.add(password)
        try:
            account = provision_account(
                request.user, username, password, data["valid_days"], data["max_connections"],
                None, data["start_on_first_connection"], traffic_bytes(data["traffic_gb"]),
                referral_note=referral_note,
            )
        except ProvisionError as exc:
            record(request, username, "create", False)
            failure = f"Stopped at {username}: {exc}"
            break
        record(request, username, "create", True)
        created.append({"username": username, "password": password, "referral_note": account.referral_note,
                        "expires_at": account.expires_at, "valid_days": data["valid_days"],
                        "first_connection": data["start_on_first_connection"]})
    response = render(request, "bulk_create_result.html", {
        "created": created, "requested": data["count"], "failure": failure,
        "vpn_ssh_port": get_ssh_port(), "panel_host": settings.ALLOWED_HOSTS[0],
        "traffic_gb": data["traffic_gb"], "max_connections": data["max_connections"],
    })
    response["Cache-Control"] = "no-store, private"
    return response


@staff_required
@require_POST
def edit_account(request, pk):
    sync_account_activation()
    account = get_object_or_404(VpnAccount, pk=pk)
    form = EditAccountForm(request.POST)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return redirect("dashboard")

    days = form.cleaned_data["valid_days"]
    pending = account.valid_days is not None and account.activated_at is None
    expires_at = (timezone.now() + timedelta(days=days) if days is not None and not pending
                  else account.expires_at)
    max_connections = form.cleaned_data["max_connections"]
    if max_connections is None:
        max_connections = account.max_connections
    elif max_connections == 0:
        max_connections = None
    password = form.cleaned_data["password"] or None
    traffic_gb = form.cleaned_data["traffic_gb"]
    traffic_limit_bytes = account.traffic_limit_bytes if traffic_gb is None else traffic_bytes(traffic_gb) or None
    try:
        own_code = unique_referral_code(
            form.cleaned_data["referral_code"] if "referral_code" in request.POST else account.referral_code,
            exclude_pk=account.pk,
        )
    except ProvisionError as exc:
        messages.error(request, str(exc))
        return redirect("dashboard")
    referral_changed = own_code != account.referral_code
    linux_changed = (max_connections != account.max_connections or expires_at != account.expires_at or
                     traffic_limit_bytes != account.traffic_limit_bytes or
                     (days is not None and pending and days != account.valid_days) or password is not None)
    if not linux_changed and not referral_changed:
        messages.info(request, f"No changes to {account.username}.")
        return redirect("dashboard")
    try:
        if linux_changed:
            call_helper(
                "update", account.username, password,
                expires_at=int(expires_at.timestamp()) if days is not None and not pending else None,
                valid_days=days if days is not None and pending else None,
                max_connections=(max_connections if max_connections is not None else 0), enabled=account.enabled,
                traffic_limit_bytes=(traffic_limit_bytes if traffic_limit_bytes is not None else 0)
                                    if traffic_gb is not None else None,
            )
    except ProvisionError as exc:
        record(request, account.username, "update", False)
        messages.error(request, str(exc))
    else:
        account.expires_at = expires_at
        if pending and days is not None:
            account.valid_days = days
        account.max_connections = max_connections
        account.traffic_limit_bytes = traffic_limit_bytes
        account.referral_code = own_code
        fields = ["expires_at", "max_connections", "valid_days", "traffic_limit_bytes"]
        if referral_changed:
            fields.append("referral_code")
        if password is not None:
            account.password_ciphertext = encrypt_password(password)
            fields.append("password_ciphertext")
        try:
            account.save(update_fields=fields)
        except IntegrityError:
            record(request, account.username, "update", False)
            messages.error(request, "This referral code is already in use.")
            return redirect("dashboard")
        record(request, account.username, "update", True)
        messages.success(request, f"Updated {account.username}.")
    return redirect("dashboard")


@staff_required
@require_POST
def extend_account(request, pk):
    sync_account_activation()
    account = get_object_or_404(VpnAccount, pk=pk)
    try:
        days = int(request.POST.get("days", ""))
        if days not in (30, 60, 90):
            raise ValueError("Choose 30, 60, or 90 days.")
        now = timezone.now()
        pending = account.valid_days is not None and account.activated_at is None
        if pending:
            valid_days = account.valid_days + days
            if valid_days > 36500:
                raise ValueError("Validity cannot exceed 36500 days.")
            call_helper("update", account.username, valid_days=valid_days,
                        max_connections=account.max_connections, enabled=account.enabled)
        else:
            expires_at = max(account.expires_at or now, now) + timedelta(days=days)
            call_helper("update", account.username,
                        expires_at=int(expires_at.timestamp()),
                        max_connections=account.max_connections, enabled=account.enabled)
    except (ValueError, ProvisionError) as exc:
        record(request, account.username, "extend", False)
        messages.error(request, str(exc))
    else:
        if pending:
            account.valid_days = valid_days
            account.save(update_fields=["valid_days"])
        else:
            account.expires_at = expires_at
            account.save(update_fields=["expires_at"])
        record(request, account.username, "extend", True)
        messages.success(request, f"Added {days} days to {account.username}.")
    return redirect("dashboard")


@staff_required
@require_POST
def reset_account_traffic(request, pk):
    account = get_object_or_404(VpnAccount, pk=pk)
    try:
        call_helper("usage-reset", account.username)
    except ProvisionError as exc:
        record(request, account.username, "usage-reset", False)
        messages.error(request, str(exc))
    else:
        record(request, account.username, "usage-reset", True)
        messages.success(request, f"Traffic reset for {account.username}.")
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
def bulk_account_action(request):
    action = request.POST.get("action", "")
    if action not in {"enable", "disable", "extend", "usage-reset", "delete"}:
        messages.error(request, "Choose a valid bulk action.")
        return redirect("dashboard")
    raw_ids = request.POST.getlist("account_ids")
    if not raw_ids or any(not value.isdecimal() for value in raw_ids):
        messages.error(request, "Select at least one valid user.")
        return redirect("dashboard")
    account_ids = list(dict.fromkeys(int(value) for value in raw_ids))
    found = {account.pk: account for account in VpnAccount.objects.filter(pk__in=account_ids)}
    if len(found) != len(account_ids):
        messages.error(request, "The selection includes a user that no longer exists. Refresh the list and try again.")
        return redirect("dashboard")
    days = None
    if action == "extend":
        try:
            days = int(request.POST.get("days", ""))
        except ValueError:
            days = None
        if days not in (30, 60, 90):
            messages.error(request, "Choose 30, 60, or 90 days.")
            return redirect("dashboard")
        sync_account_activation()
        found = {account.pk: account for account in VpnAccount.objects.filter(pk__in=account_ids)}
        if len(found) != len(account_ids):
            messages.error(request, "The selection changed while processing. Refresh the list and try again.")
            return redirect("dashboard")

    succeeded = 0
    skipped = 0
    failures = []
    for pk in account_ids:
        account = found[pk]
        try:
            if action in ("enable", "disable"):
                enabled = action == "enable"
                if account.enabled == enabled:
                    skipped += 1
                    continue
                if enabled and account.is_expired:
                    raise ValueError("Extend this expired user before enabling it.")
                call_helper(action, account.username)
                account.enabled = enabled
                account.save(update_fields=["enabled"])
            elif action == "extend":
                now = timezone.now()
                pending = account.valid_days is not None and account.activated_at is None
                if pending:
                    valid_days = account.valid_days + days
                    if valid_days > 36500:
                        raise ValueError("Validity cannot exceed 36500 days.")
                    call_helper("update", account.username, valid_days=valid_days,
                                max_connections=account.max_connections, enabled=account.enabled)
                    account.valid_days = valid_days
                    account.save(update_fields=["valid_days"])
                else:
                    expires_at = max(account.expires_at or now, now) + timedelta(days=days)
                    call_helper("update", account.username, expires_at=int(expires_at.timestamp()),
                                max_connections=account.max_connections, enabled=account.enabled)
                    account.expires_at = expires_at
                    account.save(update_fields=["expires_at"])
            elif action == "usage-reset":
                call_helper("usage-reset", account.username)
            else:
                call_helper("delete", account.username)
                account.delete()
        except (ProvisionError, ValueError) as exc:
            record(request, account.username, action, False)
            failures.append(f"{account.username}: {exc}")
        else:
            record(request, account.username, action, True)
            succeeded += 1
    if succeeded or skipped:
        messages.success(request, f"Bulk {action}: {succeeded} completed, {skipped} already in the requested state.")
    if failures:
        messages.error(request, f"{len(failures)} failed. " + "; ".join(failures[:5]))
    return redirect("dashboard")


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
        account.password_ciphertext = encrypt_password(form.cleaned_data["password"])
        account.save(update_fields=["password_ciphertext"])
        record(request, account.username, "password", True)
        messages.success(request, f"Changed password for {account.username}.")
    return redirect("dashboard")


@staff_required
@require_GET
def account_password(request, pk):
    account = get_object_or_404(VpnAccount, pk=pk)
    response = JsonResponse({"password": decrypt_password(account.password_ciphertext)})
    response["Cache-Control"] = "no-store, private"
    return response


@staff_required
def panel_settings(request):
    try:
        pending_port = json.loads(call_helper("port-status"))
    except (ProvisionError, ValueError):
        pending_port = None
    try:
        pending_web = json.loads(call_helper("web-status"))
    except (ProvisionError, ValueError, TypeError):
        pending_web = None
    web_path = settings.WEB_PATH
    web_port = settings.HTTPS_PORT if settings.TLS_ENABLED else settings.HTTP_PORT
    web_scheme = "https" if settings.TLS_ENABLED else "http"
    web_port_suffix = "" if web_port == ("443" if settings.TLS_ENABLED else "80") else f":{web_port}"
    return render(request, "settings.html", {
        "password_form": PasswordChangeForm(request.user),
        "ssh_port": get_ssh_port(),
        "pending_port": pending_port,
        "pending_web": pending_web,
        "panel_web_path": web_path or "/",
        "panel_web_port": web_port,
        "panel_web_url": f"{web_scheme}://{settings.ALLOWED_HOSTS[0]}{web_port_suffix}{web_path}/",
        "tls_enabled": settings.TLS_ENABLED,
    })


@staff_required
@require_POST
def stage_web_address(request):
    try:
        port = int(request.POST.get("port", ""))
        result = json.loads(call_helper("web-stage", web_path=request.POST.get("path", ""), web_port=port))
    except (ValueError, ProvisionError) as exc:
        record(request, "server", "web-stage", False)
        messages.error(request, str(exc))
        return redirect("panel_settings")
    record(request, "server", "web-stage", True)
    return render(request, "web_change_pending.html", {"new_url": result["url"]})


@staff_required
@require_POST
def confirm_web_address(request):
    try:
        call_helper("web-confirm")
    except ProvisionError as exc:
        record(request, "server", "web-confirm", False)
        messages.error(request, str(exc))
    else:
        record(request, "server", "web-confirm", True)
        messages.success(request, "The new panel address is confirmed.")
    return redirect("panel_settings")


@staff_required
@require_GET
def backup_settings(request):
    try:
        backups = list_backups()
    except OSError:
        backups = []
        messages.error(request, "Backups could not be listed.")
    return render(request, "backups.html", {"backups": backups, "tls_enabled": settings.TLS_ENABLED})


@staff_required
@require_POST
def create_backup(request):
    try:
        result = call_backup("create")
    except BackupError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Backup created: {result['name']}. Download it and keep it private.")
    return redirect("backup_settings")


@staff_required
@require_GET
def download_backup(request, name):
    if not BACKUP_NAME.fullmatch(name):
        raise Http404
    path = BACKUP_DIR / name
    if not path.is_file() or path.is_symlink():
        raise Http404
    try:
        response = FileResponse(path.open("rb"), as_attachment=True, filename=name, content_type="application/octet-stream")
    except OSError as exc:
        raise Http404 from exc
    response["Cache-Control"] = "no-store, private"
    return response


@staff_required
@require_POST
def restore_backup(request):
    upload = request.FILES.get("backup_file")
    if not upload or request.POST.get("confirm_replace") != "yes":
        messages.error(request, "Choose a backup file and confirm the restore.")
        return redirect("backup_settings")
    name = None
    try:
        name = save_upload(upload)
        result = call_backup("restore", name=name)
    except (BackupError, OSError) as exc:
        messages.error(request, str(exc))
        return redirect("backup_settings")
    finally:
        if name:
            try:
                (UPLOAD_DIR / name).unlink(missing_ok=True)
            except OSError:
                pass
    # The restored administrator accounts and signing key take effect after
    # the background service restart scheduled by the privileged helper.
    return render(request, "restore_complete.html", {"restored_accounts": result["accounts"]})


@staff_required
@require_POST
def import_users_backup(request):
    upload = request.FILES.get("backup_file")
    if not upload or request.POST.get("confirm_import") != "yes":
        messages.error(request, "Choose a users-only archive and confirm the import.")
        return redirect("backup_settings")
    name = None
    try:
        name = save_upload(upload)
        result = call_backup("import-users", name=name, actor_id=request.user.pk)
    except (BackupError, OSError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Imported {result['accounts']} users. Recovery backup: {result['recovery_backup']}.")
    finally:
        if name:
            try:
                (UPLOAD_DIR / name).unlink(missing_ok=True)
            except OSError:
                pass
    return redirect("backup_settings")


@staff_required
@require_POST
def change_admin_password(request):
    form = PasswordChangeForm(request.user, request.POST)
    if form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Administrator password changed.")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
    return redirect("panel_settings")


@staff_required
@require_POST
def stage_ssh_port(request):
    try:
        port = int(request.POST.get("port", ""))
        if not 1 <= port <= 65535:
            raise ValueError("Enter a valid TCP port (1–65535).")
        call_helper("port-stage", port=port)
    except (ValueError, ProvisionError) as exc:
        record(request, "server", "port-stage", False)
        messages.error(request, str(exc))
    else:
        record(request, "server", "port-stage", True)
        messages.success(request, f"Port {port} is listening. Test a new SSH connection before closing the old port.")
    return redirect("panel_settings")


@staff_required
@require_POST
def finalize_ssh_port(request):
    try:
        result = json.loads(call_helper("port-finalize"))
    except (ProvisionError, ValueError) as exc:
        record(request, "server", "port-finalize", False)
        messages.error(request, str(exc))
    else:
        record(request, "server", "port-finalize", True)
        messages.success(request, f"SSH now uses port {result['new']}. The old port is closed.")
    return redirect("panel_settings")


@staff_required
@require_POST
def cancel_ssh_port(request):
    try:
        call_helper("port-cancel")
    except ProvisionError as exc:
        record(request, "server", "port-cancel", False)
        messages.error(request, str(exc))
    else:
        record(request, "server", "port-cancel", True)
        messages.success(request, "Port change canceled; the original SSH port is active.")
    return redirect("panel_settings")
