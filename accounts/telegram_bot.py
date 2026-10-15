"""Private-chat administration for the panel's Telegram bot."""

from datetime import timedelta
import logging
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import time
import uuid

from django.db import IntegrityError, close_old_connections
from django.utils import timezone

from .backups import BACKUP_DIR, BACKUP_NAME, UPLOAD_DIR, BackupError, call_backup, list_backups
from .forms import BulkCreateAccountForm, CreateAccountForm, EditAccountForm
from .models import AuditEvent, TelegramBotConfig, VpnAccount
from .secrets import decrypt_bot_token, decrypt_password, encrypt_password
from .services import ProvisionError, call_helper, get_ssh_port
from .system_metrics import get_system_metrics
from .telegram_api import DOCUMENT_CHUNK_SIZE, TelegramAPI, TelegramError
from .views import (
    account_summary, format_bytes, generated_password, get_account_usage,
    provision_account, sync_account_activation, traffic_bytes, unique_referral_code,
)


log = logging.getLogger(__name__)

MAIN_MENU = [
    [{"text": "وضعیت سرور", "callback_data": "menu:status"},
     {"text": "کاربران", "callback_data": "menu:users"}],
    [{"text": "بکاپ‌ها", "callback_data": "menu:backups"},
     {"text": "تنظیمات", "callback_data": "menu:settings"}],
    [{"text": "راهنمای دستورها", "callback_data": "menu:help"}],
]

HELP = """دستورهای ربات (مقادیر دارای فاصله را داخل کوتیشن بگذار):
/status — وضعیت سرور و کاربران
/users [جستجو] — فهرست و جستجوی کاربر
/user NAME — جزئیات، رمز و IP اتصال
/create username=NAME password=PASS days=30 limit=1 traffic=0 referral=TEXT first=0
/bulkcreate count=2 prefix=user start=1000 password=auto mode=digits length=8 days=30 limit=1 traffic=0 referral=TEXT first=0
/edit NAME days=30 limit=0 traffic=0 password=NEW code=TEXT referral=TEXT
/password NAME NEW — تغییر رمز کاربر
/enable NAME، /disable NAME، /extend NAME 30|60|90، /resettraffic NAME، /delete NAME
/bulk enable|disable|extend|resettraffic|delete NAME1,NAME2 [30|60|90]
/audit [COUNT] — گزارش فعالیت
/backups، /backup، /download FILE، /restore FILE، /removebackup FILE
برای ریستور از فایل، فایل ‎.tar.gz‎ تا ۲۰ مگابایت را به گفتگوی خصوصی ربات بفرست.
/schedule on|off HH:MM — ساعت تهران
/settings — آدرس پنل و پورت‌ها
/sshport PORT، /sshconfirm، /sshcancel
/web PATH PORT — تغییر مسیر و پورت وب
/adminpassword OLD NEW — تغییر رمز مدیر
/id — شناسهٔ تلگرام شما
برای حذف، ریستور، تغییر پورت و برخی عملیات گروهی تأیید جدا نمایش داده می‌شود."""


class BotCommandError(Exception):
    pass


def audit(config, username, action, succeeded):
    AuditEvent.objects.create(actor=config.owner, username=username, action=action[:20], succeeded=succeeded)


def parse_fields(tokens, allowed, required=()):
    fields = {}
    for token in tokens:
        if "=" not in token:
            raise BotCommandError("هر مقدار را به شکل name=value وارد کن. /help")
        key, value = token.split("=", 1)
        if key not in allowed or key in fields:
            raise BotCommandError(f"فیلد نامعتبر یا تکراری: {key}")
        fields[key] = value
    if set(required) - set(fields):
        raise BotCommandError("فیلد لازم وارد نشده: " + ", ".join(sorted(set(required) - set(fields))))
    return fields


def form_data_or_error(form):
    if not form.is_valid():
        raise BotCommandError("; ".join(
            str(error) for errors in form.errors.values() for error in errors
        )[:600])
    return form.cleaned_data


def account_or_error(username):
    try:
        return VpnAccount.objects.get(username=username)
    except VpnAccount.DoesNotExist:
        raise BotCommandError("این کاربر پیدا نشد.") from None


def require_args(args, count, usage):
    if len(args) != count:
        raise BotCommandError(f"ساختار دستور: {usage}")


def backup_path(name):
    if BACKUP_NAME.fullmatch(name):
        path = BACKUP_DIR / name
    elif re.fullmatch(r"restore-[0-9a-f]{32}\.tar\.gz", name):
        path = UPLOAD_DIR / name
    else:
        raise BotCommandError("نام فایل بکاپ معتبر نیست.")
    if not path.is_file() or path.is_symlink():
        raise BotCommandError("فایل بکاپ پیدا نشد.")
    return path


def send_backup(api, chat_id, name):
    if not BACKUP_NAME.fullmatch(name):
        raise BotCommandError("فقط بکاپ‌های ذخیره‌شده را می‌توان دانلود کرد.")
    path = backup_path(name)
    size = path.stat().st_size
    if size <= DOCUMENT_CHUNK_SIZE:
        api.send_document(chat_id, path, caption="بکاپ کامل پنل — این فایل شامل رمزها و کلیدهای پنل است.")
        return
    parts = math.ceil(size / DOCUMENT_CHUNK_SIZE)
    api.send_message(chat_id, f"بکاپ {name} بزرگ است و در {parts} بخش ارسال می‌شود. پس از دانلود، بخش‌ها را به ترتیب به هم وصل کن.")
    for index in range(parts):
        start = index * DOCUMENT_CHUNK_SIZE
        length = min(DOCUMENT_CHUNK_SIZE, size - start)
        api.send_document(
            chat_id, path, start=start, length=length,
            filename=f"{name}.part{index + 1:03d}",
            caption=f"{name} — بخش {index + 1} از {parts}",
        )
    api.send_message(chat_id, f"برای بازسازی: cat {name}.part* > {name}")


def panel_https_enabled():
    # Re-read the live config: this worker is not restarted by every web change.
    # Fail closed if the file is unavailable, even if the process has stale env.
    try:
        for line in Path("/etc/sshvpn/panel.env").read_text().splitlines():
            if line.startswith("PANEL_TLS_ENABLED="):
                return line.partition("=")[2].strip().strip('"\'') == "1"
    except OSError:
        pass
    return False


def read_panel_address():
    # The bot process survives web-address changes, so read the live environment file.
    values = {}
    try:
        for line in Path("/etc/sshvpn/panel.env").read_text().splitlines():
            if line.startswith(("PANEL_DOMAIN=", "PANEL_WEB_PATH=", "PANEL_TLS_ENABLED=", "PANEL_HTTP_PORT=", "PANEL_HTTPS_PORT=")):
                key, value = line.split("=", 1)
                values[key] = value.strip('"')
    except OSError:
        values = os.environ
    tls = values.get("PANEL_TLS_ENABLED") == "1"
    port = values.get("PANEL_HTTPS_PORT" if tls else "PANEL_HTTP_PORT", "443" if tls else "80")
    suffix = "" if port == ("443" if tls else "80") else f":{port}"
    return f"{'https' if tls else 'http'}://{values.get('PANEL_DOMAIN', 'localhost')}{suffix}{values.get('PANEL_WEB_PATH', '')}/"


def status_text():
    sync_account_activation()
    accounts = list(VpnAccount.objects.all())
    usage = get_account_usage()
    counts = account_summary(accounts, usage, timezone.now())
    metrics = get_system_metrics()
    resource = []
    for key, label in (("cpu", "CPU"), ("memory", "RAM"), ("disk", "Disk")):
        value = metrics.get(key, {}).get("percent")
        resource.append(f"{label}: {value if value is not None else '—'}%")
    return (
        f"آدرس پنل: {read_panel_address()}\n"
        f"پورت SSH: {get_ssh_port()}\n"
        f"کاربران: {counts['total']} | آنلاین: {counts['online'] if counts['online'] is not None else '—'}"
        f" | غیرفعال: {counts['inactive']}\n" + " | ".join(resource)
    )


def users_text(query=""):
    accounts = VpnAccount.objects.order_by("created_at", "pk")
    if query:
        from django.db.models import Q
        accounts = accounts.filter(
            Q(username__icontains=query) | Q(referral_note__icontains=query) | Q(referral_code__icontains=query)
        )
    total = accounts.count()
    names = list(accounts.values_list("username", flat=True)[:30])
    suffix = f"\nو {total - 30} کاربر دیگر؛ جستجو را محدودتر کن." if total > 30 else ""
    return f"کاربران ({total}):\n" + ("\n".join(names) if names else "موردی پیدا نشد.") + suffix


def user_text(username):
    sync_account_activation()
    account = account_or_error(username)
    usage = (get_account_usage() or {}).get(username, {})
    expiry = (timezone.localtime(account.expires_at).strftime("%Y-%m-%d %H:%M")
              if account.expires_at else "نامحدود")
    if account.valid_days and not account.activated_at:
        expiry = f"{account.valid_days} روز پس از اولین اتصال"
    return (
        f"کاربر: {username}\nرمز: {decrypt_password(account.password_ciphertext) or 'ناموجود'}\n"
        f"وضعیت: {'فعال' if account.enabled else 'غیرفعال'}\n"
        f"انقضا: {expiry}\nاتصال هم‌زمان: {account.max_connections or 'نامحدود'}\n"
        f"ترافیک: {usage.get('total', '—')} / "
        f"{format_bytes(account.traffic_limit_bytes) if account.traffic_limit_bytes else 'نامحدود'}\n"
        f"معرف: {account.referral_note or account.referral_code or '—'}\n"
        f"آنلاین: {usage.get('connections', 0)}\n"
        f"IP: {', '.join(usage.get('ips', [])) or '—'}\n"
        f"هاست: {os.environ.get('PANEL_DOMAIN', '—')} | پورت SSH: {get_ssh_port()}"
    )


def create_user(config, args):
    fields = parse_fields(args, {"username", "password", "days", "limit", "traffic", "referral", "first"},
                          {"username", "password", "days"})
    data = form_data_or_error(CreateAccountForm({
        "username": fields["username"], "password": fields["password"],
        "valid_days": fields["days"], "max_connections": fields.get("limit", "0"),
        "traffic_gb": "" if fields.get("traffic", "0") == "0" else fields["traffic"],
        "referral_note": fields.get("referral", ""),
        "start_on_first_connection": "on" if fields.get("first") == "1" else "",
    }))
    if fields.get("first", "0") not in ("0", "1"):
        raise BotCommandError("first باید 0 یا 1 باشد.")
    if VpnAccount.objects.filter(username=data["username"]).exists():
        raise BotCommandError("این نام کاربری از قبل وجود دارد.")
    try:
        provision_account(
            config.owner, data["username"], data["password"], data["valid_days"],
            data["max_connections"], None, data["start_on_first_connection"],
            traffic_bytes(data["traffic_gb"]), referral_note=data["referral_note"],
        )
    except ProvisionError:
        audit(config, data["username"], "create", False)
        raise
    audit(config, data["username"], "create", True)
    return f"کاربر {data['username']} ساخته شد.\n" + user_text(data["username"])


def bulk_create_users(config, args):
    fields = parse_fields(
        args, {"count", "prefix", "start", "password", "mode", "length", "days", "limit", "traffic", "referral", "first"},
        {"count", "prefix", "start", "days"},
    )
    first = fields.get("first", "0")
    if first not in ("0", "1"):
        raise BotCommandError("first باید 0 یا 1 باشد.")
    password = fields.get("password", "auto")
    data = form_data_or_error(BulkCreateAccountForm({
        "count": fields["count"], "prefix": fields["prefix"], "start_number": fields["start"],
        "password": "" if password == "auto" else password,
        "password_mode": fields.get("mode", "digits"), "password_length": fields.get("length", "8"),
        "valid_days": fields["days"], "max_connections": fields.get("limit", "0"),
        "traffic_gb": "" if fields.get("traffic", "0") == "0" else fields["traffic"],
        "referral_note": fields.get("referral", ""),
        "start_on_first_connection": "on" if first == "1" else "",
    }))
    numbers = secrets.SystemRandom().sample(
        range(data["start_number"], data["start_number"] + 1_000_000), data["count"]
    )
    usernames = [f"{data['prefix']}{number}" for number in numbers]
    if any(len(name) > 32 for name in usernames):
        raise BotCommandError("نام کاربری تولیدشده از ۳۲ نویسه بلندتر می‌شود.")
    if VpnAccount.objects.filter(username__in=usernames).exists():
        raise BotCommandError("یکی از نام‌های تصادفی از قبل وجود دارد؛ دوباره تلاش کن.")
    created = []
    used_passwords = set()
    for username in usernames:
        password = data["password"]
        if not password:
            for _ in range(100):
                password = generated_password(data["password_mode"], data["password_length"])
                if password not in used_passwords:
                    break
            else:
                break
            used_passwords.add(password)
        try:
            provision_account(
                config.owner, username, password, data["valid_days"], data["max_connections"],
                None, data["start_on_first_connection"], traffic_bytes(data["traffic_gb"]),
                referral_note=data["referral_note"],
            )
        except ProvisionError as exc:
            audit(config, username, "create", False)
            return f"{len(created)} کاربر ساخته شد؛ ساخت {username} ناموفق بود: {exc}\n" + "\n".join(created)
        audit(config, username, "create", True)
        created.append(f"{username} | {password}")
    return f"{len(created)} کاربر ساخته شد (نام کاربری | رمز):\n" + "\n".join(created)


def edit_user(config, username, args):
    account = account_or_error(username)
    fields = parse_fields(args, {"days", "limit", "traffic", "password", "code", "referral"})
    if not fields:
        raise BotCommandError("حداقل یک فیلد برای تغییر وارد کن.")
    mapped = {"days": "valid_days", "limit": "max_connections", "traffic": "traffic_gb",
              "code": "referral_code"}
    mapped = {mapped.get(key, key): value
              for key, value in fields.items() if key != "referral"}
    data = form_data_or_error(EditAccountForm(mapped))
    if "referral" in fields and "\x00" in fields["referral"]:
        raise BotCommandError("متن معرف نباید شامل NUL باشد.")
    sync_account_activation()
    account.refresh_from_db()
    days = data["valid_days"]
    pending = account.valid_days is not None and account.activated_at is None
    expires_at = timezone.now() + timedelta(days=days) if days is not None and not pending else account.expires_at
    limit = account.max_connections if data["max_connections"] is None else data["max_connections"] or None
    traffic = account.traffic_limit_bytes
    if data["traffic_gb"] is not None:
        traffic = traffic_bytes(data["traffic_gb"]) or None
    password = data["password"] or None
    code = (unique_referral_code(data["referral_code"], exclude_pk=account.pk)
            if "code" in fields else account.referral_code)
    linux_changed = (
        limit != account.max_connections or expires_at != account.expires_at or
        traffic != account.traffic_limit_bytes or
        (pending and days is not None and days != account.valid_days) or password is not None
    )
    if linux_changed:
        try:
            call_helper(
                "update", username, password,
                expires_at=int(expires_at.timestamp()) if days is not None and not pending else None,
                valid_days=days if days is not None and pending else None,
                max_connections=limit or 0, enabled=account.enabled,
                traffic_limit_bytes=(traffic or 0) if data["traffic_gb"] is not None else None,
            )
        except ProvisionError:
            audit(config, username, "update", False)
            raise
    account.expires_at = expires_at
    account.max_connections = limit
    account.traffic_limit_bytes = traffic
    account.referral_code = code
    update_fields = ["expires_at", "max_connections", "traffic_limit_bytes", "referral_code"]
    if pending and days is not None:
        account.valid_days = days
        update_fields.append("valid_days")
    if "referral" in fields:
        account.referral_note = fields["referral"]
        update_fields.append("referral_note")
    if password is not None:
        account.password_ciphertext = encrypt_password(password)
        update_fields.append("password_ciphertext")
    try:
        account.save(update_fields=update_fields)
    except IntegrityError:
        audit(config, username, "update", False)
        raise BotCommandError("این کد معرف قبلاً استفاده شده است.") from None
    audit(config, username, "update", True)
    return f"کاربر {username} ویرایش شد.\n" + user_text(username)


def account_action(config, action, username, days=None):
    account = account_or_error(username)
    try:
        if action in ("enable", "disable"):
            enabled = action == "enable"
            if account.enabled == enabled:
                return "وضعیت کاربر از قبل همین بود."
            if enabled and account.is_expired:
                raise BotCommandError("ابتدا اعتبار کاربر منقضی‌شده را تمدید کن.")
            call_helper(action, username)
            account.enabled = enabled
            account.save(update_fields=["enabled"])
        elif action == "extend":
            if days not in (30, 60, 90):
                raise BotCommandError("تمدید فقط ۳۰، ۶۰ یا ۹۰ روز است.")
            sync_account_activation()
            account.refresh_from_db()
            now = timezone.now()
            if account.valid_days is not None and account.activated_at is None:
                total_days = account.valid_days + days
                if total_days > 36500:
                    raise BotCommandError("اعتبار از سقف مجاز فراتر می‌رود.")
                call_helper("update", username, valid_days=total_days,
                            max_connections=account.max_connections, enabled=account.enabled)
                account.valid_days = total_days
                account.save(update_fields=["valid_days"])
            else:
                expires_at = max(account.expires_at or now, now) + timedelta(days=days)
                call_helper("update", username, expires_at=int(expires_at.timestamp()),
                            max_connections=account.max_connections, enabled=account.enabled)
                account.expires_at = expires_at
                account.save(update_fields=["expires_at"])
        elif action == "resettraffic":
            call_helper("usage-reset", username)
        elif action == "delete":
            call_helper("delete", username)
            account.delete()
        else:
            raise BotCommandError("عملیات نامعتبر است.")
    except (ProvisionError, BotCommandError):
        audit(config, username, action, False)
        raise
    audit(config, username, action, True)
    return f"عملیات {action} برای {username} انجام شد."


def change_user_password(config, username, password):
    account = account_or_error(username)
    form_data_or_error(EditAccountForm({"password": password}))
    try:
        call_helper("password", username, password)
    except ProvisionError:
        audit(config, username, "password", False)
        raise
    account.password_ciphertext = encrypt_password(password)
    account.save(update_fields=["password_ciphertext"])
    audit(config, username, "password", True)
    return f"رمز {username} تغییر کرد."


def user_keyboard(username):
    if len(username.encode("utf-8")) > 32:
        return MAIN_MENU
    return [
        [{"text": "فعال", "callback_data": f"act:enable:{username}"},
         {"text": "غیرفعال", "callback_data": f"act:disable:{username}"}],
        [{"text": "+۳۰ روز", "callback_data": f"act:extend30:{username}"},
         {"text": "ریست ترافیک", "callback_data": f"act:resettraffic:{username}"}],
        [{"text": "حذف", "callback_data": f"act:delete:{username}"}],
    ]


def execute(config, api, chat_id, command_line):
    try:
        parts = shlex.split(command_line)
    except ValueError:
        raise BotCommandError("کوتیشن‌ها را بررسی کن.") from None
    if not parts:
        return "از /help یا منوی پایین استفاده کن.", MAIN_MENU
    command = parts[0].split("@", 1)[0].lower().lstrip("/")
    args = parts[1:]
    if command in ("start", "help"):
        return HELP, MAIN_MENU
    if command == "id":
        return f"شناسهٔ تلگرام: {chat_id}", MAIN_MENU
    if command == "status":
        return status_text(), MAIN_MENU
    if command == "users":
        return users_text(" ".join(args)), MAIN_MENU
    if command == "user":
        require_args(args, 1, "/user NAME")
        return user_text(args[0]), user_keyboard(args[0])
    if command == "create":
        return create_user(config, args), MAIN_MENU
    if command == "bulkcreate":
        return bulk_create_users(config, args), MAIN_MENU
    if command == "edit":
        if len(args) < 2:
            raise BotCommandError("ساختار دستور: /edit NAME field=value")
        return edit_user(config, args[0], args[1:]), user_keyboard(args[0])
    if command == "password":
        require_args(args, 2, "/password NAME NEW")
        return change_user_password(config, *args), user_keyboard(args[0])
    if command in ("enable", "disable", "resettraffic", "delete"):
        require_args(args, 1, f"/{command} NAME")
        return account_action(config, command, args[0]), user_keyboard(args[0]) if command != "delete" else MAIN_MENU
    if command == "extend":
        require_args(args, 2, "/extend NAME 30|60|90")
        try:
            days = int(args[1])
        except ValueError:
            raise BotCommandError("روز تمدید باید ۳۰، ۶۰ یا ۹۰ باشد.") from None
        return account_action(config, command, args[0], days), user_keyboard(args[0])
    if command == "bulk":
        if len(args) not in (2, 3) or args[0] not in ("enable", "disable", "extend", "resettraffic", "delete"):
            raise BotCommandError("ساختار دستور: /bulk ACTION NAME1,NAME2 [30|60|90]")
        names = list(dict.fromkeys(name.strip() for name in args[1].split(",")))
        if not names or len(names) > 100 or any(not name for name in names):
            raise BotCommandError("بین ۱ تا ۱۰۰ نام کاربری وارد کن.")
        days = None
        if args[0] == "extend":
            if len(args) != 3:
                raise BotCommandError("برای تمدید، ۳۰، ۶۰ یا ۹۰ روز وارد کن.")
            try:
                days = int(args[2])
            except ValueError:
                raise BotCommandError("روز تمدید نامعتبر است.") from None
        elif len(args) == 3:
            raise BotCommandError("روز فقط برای تمدید استفاده می‌شود.")
        failures = []
        success = 0
        for username in names:
            try:
                account_action(config, args[0], username, days)
                success += 1
            except (BotCommandError, ProvisionError) as exc:
                failures.append(f"{username}: {exc}")
        return f"انجام‌شده: {success} | ناموفق: {len(failures)}\n" + "\n".join(failures[:20]), MAIN_MENU
    if command == "audit":
        if len(args) > 1:
            raise BotCommandError("ساختار دستور: /audit [COUNT]")
        try:
            count = min(30, max(1, int(args[0]))) if args else 10
        except ValueError:
            raise BotCommandError("تعداد نامعتبر است.") from None
        events = AuditEvent.objects.select_related("actor").order_by("-created_at")[:count]
        return "گزارش فعالیت:\n" + "\n".join(
            f"{timezone.localtime(item.created_at):%m-%d %H:%M} {item.username} {item.action}"
            f" {'✓' if item.succeeded else '✗'}" for item in events
        ), MAIN_MENU
    if command == "backups":
        backups = list_backups()[:20]
        return "بکاپ‌های موجود:\n" + (
            "\n".join(f"{item['name']} ({format_bytes(item['size'])})" for item in backups)
            if backups else "هنوز بکاپی وجود ندارد."
        ), MAIN_MENU
    if command == "backup":
        result = call_backup("create")
        send_backup(api, chat_id, result["name"])
        return f"بکاپ {result['name']} ساخته و ارسال شد.", MAIN_MENU
    if command == "download":
        require_args(args, 1, "/download FILE")
        send_backup(api, chat_id, args[0])
        return "ارسال بکاپ تمام شد.", MAIN_MENU
    if command == "restore":
        require_args(args, 1, "/restore FILE")
        source = backup_path(args[0])
        result = call_backup("restore", name=args[0], actor_id=config.owner_id)
        if source.parent == UPLOAD_DIR:
            source.unlink(missing_ok=True)
        return f"ریستور انجام شد: {result.get('accounts', 0)} کاربر. دوباره وارد پنل شو.", MAIN_MENU
    if command == "removebackup":
        require_args(args, 1, "/removebackup FILE")
        if not BACKUP_NAME.fullmatch(args[0]):
            raise BotCommandError("فقط بکاپ ذخیره‌شده را می‌توان حذف کرد.")
        backup_path(args[0])
        call_backup("remove", name=args[0])
        return "فایل بکاپ حذف شد.", MAIN_MENU
    if command == "schedule":
        require_args(args, 2, "/schedule on|off HH:MM")
        if args[0] not in ("on", "off"):
            raise BotCommandError("وضعیت باید on یا off باشد.")
        result = call_backup("schedule-set", enabled=args[0] == "on", time=args[1])
        return f"بکاپ خودکار: {'روشن' if result['enabled'] else 'خاموش'}، ساعت {result['time']} تهران", MAIN_MENU
    if command == "settings":
        schedule = call_backup("schedule-status")
        port = get_ssh_port()
        try:
            pending = call_helper("port-status")
        except ProvisionError:
            pending = "ناموجود"
        return (
            status_text() + f"\nبکاپ خودکار: {'روشن' if schedule['enabled'] else 'خاموش'} "
            f"ساعت {schedule['time']} تهران\nوضعیت تغییر پورت SSH: {pending}\n"
            f"ربات: {'فعال' if config.enabled else 'غیرفعال'}"
        ), MAIN_MENU
    if command == "sshport":
        require_args(args, 1, "/sshport PORT")
        try:
            port = int(args[0])
        except ValueError:
            raise BotCommandError("پورت معتبر وارد کن.") from None
        if not 1 <= port <= 65535:
            raise BotCommandError("پورت باید بین ۱ و ۶۵۵۳۵ باشد.")
        call_helper("port-stage", port=port)
        audit(config, "server", "port-stage", True)
        return f"پورت {port} فعال شد. اتصال مدیریتی را تست کن؛ سپس /sshconfirm بزن.", MAIN_MENU
    if command == "sshconfirm":
        result = call_helper("port-finalize")
        audit(config, "server", "port-finalize", True)
        return f"تغییر پورت SSH نهایی شد: {result}", MAIN_MENU
    if command == "sshcancel":
        call_helper("port-cancel")
        audit(config, "server", "port-cancel", True)
        return "تغییر پورت SSH لغو شد.", MAIN_MENU
    if command == "web":
        require_args(args, 2, "/web /PRIVATE-PATH PORT")
        try:
            port = int(args[1])
        except ValueError:
            raise BotCommandError("پورت معتبر وارد کن.") from None
        result = call_helper("web-stage", web_path=args[0], web_port=port)
        audit(config, "server", "web-stage", True)
        return f"نشانی وب تغییر کرد: {result}", MAIN_MENU
    if command == "adminpassword":
        require_args(args, 2, "/adminpassword OLD NEW")
        actor = config.owner
        if not actor.check_password(args[0]) or not args[1]:
            raise BotCommandError("رمز فعلی نادرست است یا رمز جدید خالی است.")
        actor.set_password(args[1])
        actor.save(update_fields=["password"])
        audit(config, "administrator", "password", True)
        return "رمز مدیر پنل تغییر کرد.", MAIN_MENU
    raise BotCommandError("دستور شناخته نشد. /help")


def needs_confirmation(command_line):
    try:
        parts = shlex.split(command_line)
    except ValueError:
        return False
    if not parts:
        return False
    command = parts[0].split("@", 1)[0].lower().lstrip("/")
    return (
        command in {"delete", "resettraffic", "bulkcreate", "restore", "removebackup",
                    "web", "sshport", "sshconfirm", "adminpassword"}
        or command == "bulk" and len(parts) > 1
    )


def dispatch(config, api, chat_id, user_id, command_line, pending, *, confirmed=False):
    if needs_confirmation(command_line) and not confirmed:
        nonce = secrets.token_hex(6)
        pending[user_id] = (nonce, time.monotonic() + 120, command_line)
        command = command_line.split(maxsplit=1)[0]
        api.send_message(chat_id, f"اجرای {command} را تأیید می‌کنی؟", [[
            {"text": "تأیید", "callback_data": f"ok:{nonce}"},
            {"text": "لغو", "callback_data": f"cancel:{nonce}"},
        ]])
        return
    try:
        result, keyboard = execute(config, api, chat_id, command_line)
    except (BotCommandError, ProvisionError, BackupError, TelegramError, OSError, ValueError) as exc:
        api.send_message(chat_id, f"خطا: {exc}", MAIN_MENU)
    else:
        api.send_message(chat_id, result, keyboard)


def handle_update(config, api, update, pending):
    if not panel_https_enabled():
        return
    callback = update.get("callback_query")
    message = (callback or {}).get("message") if callback else update.get("message")
    sender = (callback or {}).get("from") if callback else (message or {}).get("from")
    chat = (message or {}).get("chat") or {}
    user_id = (sender or {}).get("id")
    chat_id = chat.get("id")
    if not isinstance(user_id, int) or chat.get("type") != "private" or chat_id != user_id:
        return
    if callback:
        try:
            api.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
        except TelegramError:
            pass
    if not config.enabled:
        return
    allowed = user_id in config.allowed_user_ids and config.owner.is_active and config.owner.is_staff
    if not allowed:
        if not callback and (message.get("text") or "").split(maxsplit=1)[0] == "/id":
            api.send_message(chat_id, f"شناسهٔ تلگرام شما: {user_id}")
        return
    if callback:
        data = callback.get("data", "")
        if data.startswith("menu:"):
            command = data[5:]
            if command in {"status", "users", "backups", "settings", "help"}:
                dispatch(config, api, chat_id, user_id, f"/{command}", pending)
        elif data.startswith("act:"):
            _, action, username = (data.split(":", 2) + ["", ""])[:3]
            if action in {"enable", "disable", "resettraffic", "delete"}:
                dispatch(config, api, chat_id, user_id, f"/{action} {shlex.quote(username)}", pending)
            elif action == "extend30":
                dispatch(config, api, chat_id, user_id, f"/extend {shlex.quote(username)} 30", pending)
        elif data.startswith(("ok:", "cancel:")):
            operation, nonce = data.split(":", 1)
            saved = pending.get(user_id)
            if not saved or saved[0] != nonce or saved[1] < time.monotonic():
                api.send_message(chat_id, "این درخواست منقضی شده است.")
                return
            pending.pop(user_id, None)
            if operation == "cancel":
                api.send_message(chat_id, "لغو شد.", MAIN_MENU)
            else:
                dispatch(config, api, chat_id, user_id, saved[2], pending, confirmed=True)
        return
    document = message.get("document")
    if isinstance(document, dict):
        if not str(document.get("file_name", "")).endswith(".tar.gz"):
            api.send_message(chat_id, "فقط فایل بکاپ ‎.tar.gz‎ پذیرفته می‌شود.")
            return
        name = f"restore-{uuid.uuid4().hex}.tar.gz"
        UPLOAD_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = UPLOAD_DIR / name
        try:
            api.download_file(document["file_id"], destination)
            destination.chmod(0o600)
        except (KeyError, OSError, TelegramError) as exc:
            destination.unlink(missing_ok=True)
            api.send_message(chat_id, f"دریافت فایل ناموفق بود: {exc}")
            return
        api.send_message(chat_id, f"فایل دریافت شد. برای بازیابی: /restore {name}\nاین عملیات تأیید جدا می‌خواهد.")
        return
    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        api.send_message(chat_id, "برای دیدن دستورها /help را بزن.", MAIN_MENU)
        return
    dispatch(config, api, chat_id, user_id, text, pending)


def run_polling():
    pending = {}
    configured_token = None
    webhook_removed = False
    while True:
        close_old_connections()
        if not panel_https_enabled():
            configured_token = None
            webhook_removed = False
            pending.clear()
            time.sleep(5)
            continue
        config = TelegramBotConfig.objects.select_related("owner").filter(pk=1).first()
        token = decrypt_bot_token(config.token_ciphertext) if config and config.enabled else None
        if not token:
            configured_token = None
            webhook_removed = False
            pending.clear()
            time.sleep(5)
            continue
        if token != configured_token:
            configured_token = token
            webhook_removed = False
            pending.clear()
        api = TelegramAPI(token)
        try:
            if not webhook_removed:
                api.call("deleteWebhook", {"drop_pending_updates": False})
                webhook_removed = True
            updates = api.call(
                "getUpdates",
                {"offset": config.last_update_id + 1, "timeout": 20,
                 "allowed_updates": ["message", "callback_query"]},
                timeout=30,
            )
            if not isinstance(updates, list):
                raise TelegramError("Telegram returned an invalid update list.")
            for update in updates:
                if not panel_https_enabled():
                    break
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")
                if type(update_id) is not int or update_id <= config.last_update_id:
                    continue
                current = TelegramBotConfig.objects.filter(pk=1, enabled=True, token_ciphertext=config.token_ciphertext)
                if not current.update(last_update_id=update_id):
                    break
                config.last_update_id = update_id
                config = TelegramBotConfig.objects.select_related("owner").get(pk=1)
                try:
                    handle_update(config, api, update, pending)
                except Exception:
                    log.exception("Telegram update %s failed", update_id)
        except TelegramError as exc:
            log.warning("Telegram polling failed: %s", exc)
            time.sleep(5)


def send_latest_automatic_backup():
    if not panel_https_enabled():
        return "Telegram delivery requires HTTPS."
    config = TelegramBotConfig.objects.select_related("owner").filter(pk=1, enabled=True).first()
    if not config or not config.allowed_user_ids or not config.owner.is_active or not config.owner.is_staff:
        return "Telegram delivery is disabled."
    token = decrypt_bot_token(config.token_ciphertext)
    if not token:
        return "Telegram token is unavailable."
    latest = next((item for item in list_backups() if item["automatic"]), None)
    if not latest or latest["name"] == config.last_backup_name:
        return "No new automatic backup."
    api = TelegramAPI(token)
    failures = []
    for chat_id in config.allowed_user_ids:
        if not panel_https_enabled():
            return "Telegram delivery requires HTTPS."
        try:
            send_backup(api, chat_id, latest["name"])
        except (TelegramError, OSError) as exc:
            failures.append(f"{chat_id}: {exc}")
    if failures:
        raise TelegramError("Backup delivery failed for " + "; ".join(failures))
    config.last_backup_name = latest["name"]
    config.save(update_fields=["last_backup_name"])
    return f"Sent {latest['name']} to {len(config.allowed_user_ids)} Telegram administrator(s)."
