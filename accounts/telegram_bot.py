"""Private-chat administration for the panel's Telegram bot."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation
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
from django.db.models import F, Q
from django.utils import timezone

from .backups import BACKUP_DIR, BACKUP_NAME, UPLOAD_DIR, BackupError, call_backup, list_backups
from .forms import BulkCreateAccountForm, CreateAccountForm, EditAccountForm, USERNAME_RE
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
    [{"text": "👥 کاربران", "callback_data": "menu:users:0"},
     {"text": "➕ ساخت کاربر", "callback_data": "flow:create"}],
    [{"text": "👥 ساخت گروهی", "callback_data": "flow:bulkcreate"},
     {"text": "📊 وضعیت سرور", "callback_data": "menu:status"}],
    [{"text": "💾 بکاپ‌ها", "callback_data": "menu:backups:0"},
     {"text": "⚙️ تنظیمات", "callback_data": "menu:settings"}],
    [{"text": "🧾 گزارش فعالیت", "callback_data": "menu:audit"},
     {"text": "❔ راهنما", "callback_data": "menu:help"}],
]

HOME_TEXT = "پنل مدیریت SSH VPN\nاز دکمه‌ها برای مدیریت کاربران و سرور استفاده کن. برای شروع دوباره /start را بزن."
BACK_BUTTON = [{"text": "⌂ منوی اصلی", "callback_data": "menu:home"}]
USER_PAGE_SIZE = 10
BACKUP_PAGE_SIZE = 8

WIZARD_FIELDS = {
    "create": (
        ("username", "نام کاربری انگلیسی را بفرست (مثل ali_1).", None),
        ("password", "رمز کاربر را بفرست.", None),
        ("days", "مدت اعتبار را به روز بفرست (۱ تا ۳۶۵۰۰).", None),
        ("limit", "سقف اتصال هم‌زمان را بفرست؛ ۰ یعنی نامحدود.", "0"),
        ("traffic", "سقف ترافیک به گیگابایت را بفرست؛ ۰ یعنی نامحدود.", "0"),
        ("referral", "متن معرف را بفرست؛ می‌توانی خالی بگذاری.", ""),
        ("first", "اعتبار از اولین اتصال شروع شود؟", "0"),
    ),
    "bulkcreate": (
        ("count", "تعداد کاربران را بفرست (۱ تا ۱۰۰).", None),
        ("prefix", "پیشوند نام کاربری را بفرست.", "user"),
        ("start", "حداقل شمارهٔ تصادفی را بفرست.", "1000"),
        ("days", "مدت اعتبار را به روز بفرست.", "30"),
        ("password", "رمز ثابت همهٔ کاربران را بفرست؛ برای رمزهای تصادفی رد کن.", "auto"),
        ("limit", "سقف اتصال هم‌زمان را بفرست؛ ۰ یعنی نامحدود.", "0"),
        ("traffic", "سقف ترافیک به گیگابایت را بفرست؛ ۰ یعنی نامحدود.", "0"),
        ("referral", "متن معرف مشترک را بفرست.", ""),
        ("first", "اعتبار از اولین اتصال شروع شود؟", "0"),
    ),
}

INPUT_PROMPTS = {
    "search": "نام کاربری یا متن معرف را بفرست.",
    "edit": "فیلدهای دلخواه را به شکل days=30 limit=0 traffic=0 referral=TEXT بفرست.",
    "password": "رمز جدید این کاربر را بفرست.",
    "sshport": "پورت جدید SSH را بفرست. تغییر نهایی به تأیید جدا نیاز دارد.",
    "web": "مسیر و پورت جدید وب را بفرست؛ نمونه: /my-panel 8443",
    "schedule": "وضعیت و ساعت بکاپ را بفرست؛ نمونه: on 03:00 یا off 03:00",
    "adminpassword": "رمز فعلی و رمز جدید مدیر را با یک فاصله بفرست.",
}

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
    metrics = get_system_metrics(cpu_sample_delay=0.1)
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
    accounts = VpnAccount.objects.order_by(F("sort_order").asc(nulls_last=True), "created_at", "pk")
    if query:
        accounts = accounts.filter(
            Q(username__icontains=query) | Q(referral_note__icontains=query) | Q(referral_code__icontains=query)
        )
    total = accounts.count()
    names = list(accounts.values_list("username", flat=True)[:30])
    suffix = f"\nو {total - 30} کاربر دیگر؛ جستجو را محدودتر کن." if total > 30 else ""
    return f"کاربران ({total}):\n" + ("\n".join(names) if names else "موردی پیدا نشد.") + suffix


def users_page(query="", page=0):
    accounts = VpnAccount.objects.order_by(F("sort_order").asc(nulls_last=True), "created_at", "pk")
    if query:
        accounts = accounts.filter(
            Q(username__icontains=query) | Q(referral_note__icontains=query) | Q(referral_code__icontains=query)
        )
    total = accounts.count()
    last_page = max(0, (total - 1) // USER_PAGE_SIZE)
    page = min(max(0, page), last_page)
    entries = list(accounts.values("username", "enabled", "expires_at")[
        page * USER_PAGE_SIZE:(page + 1) * USER_PAGE_SIZE
    ])
    now = timezone.now()
    buttons = []
    for entry in entries:
        inactive = not entry["enabled"] or entry["expires_at"] is not None and entry["expires_at"] <= now
        buttons.append({"text": f"{'🔴' if inactive else '👤'} {entry['username']}",
                        "callback_data": f"u:{entry['username']}"})
    keyboard = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    if not query and total > USER_PAGE_SIZE:
        navigation = []
        if page:
            navigation.append({"text": "◀️ قبلی", "callback_data": f"menu:users:{page - 1}"})
        if page < last_page:
            navigation.append({"text": "بعدی ▶️", "callback_data": f"menu:users:{page + 1}"})
        keyboard.append(navigation)
    keyboard.extend([
        [{"text": "🔎 جستجو", "callback_data": "prompt:search"},
         {"text": "➕ ساخت کاربر", "callback_data": "flow:create"}],
        [{"text": "👥 ساخت گروهی", "callback_data": "flow:bulkcreate"}, *BACK_BUTTON],
    ])
    title = f"👥 کاربران: {total}"
    if query:
        title += f"\nجستجو: {query[:80]}"
    else:
        title += f" • صفحهٔ {page + 1} از {last_page + 1}"
    return title + "\nبرای دیدن جزئیات و عملیات، روی نام کاربر بزن.", keyboard


def backups_page(page=0):
    backups = list_backups()
    total = len(backups)
    last_page = max(0, (total - 1) // BACKUP_PAGE_SIZE)
    page = min(max(0, page), last_page)
    keyboard = []
    for item in backups[page * BACKUP_PAGE_SIZE:(page + 1) * BACKUP_PAGE_SIZE]:
        label = f"{'⏱' if item['automatic'] else '💾'} {timezone.localtime(item['created']):%m/%d %H:%M} · {format_bytes(item['size'])}"
        keyboard.append([{"text": label, "callback_data": f"b:{item['name']}"}])
    if total > BACKUP_PAGE_SIZE:
        navigation = []
        if page:
            navigation.append({"text": "◀️ قبلی", "callback_data": f"menu:backups:{page - 1}"})
        if page < last_page:
            navigation.append({"text": "بعدی ▶️", "callback_data": f"menu:backups:{page + 1}"})
        keyboard.append(navigation)
    keyboard.extend([
        [{"text": "➕ ساخت بکاپ", "callback_data": "bk:create"},
         {"text": "🕒 زمان‌بندی", "callback_data": "prompt:schedule"}],
        BACK_BUTTON,
    ])
    return f"💾 بکاپ‌ها: {total}\n{'فایل موردنظر را انتخاب کن.' if total else 'هنوز بکاپی وجود ندارد.'}", keyboard


def backup_detail(name):
    path = backup_path(name)
    size = format_bytes(path.stat().st_size)
    return f"💾 {name}\nحجم: {size}\nاین فایل شامل همهٔ اطلاعات و رمزهای پنل است.", [
        [{"text": "⬇️ دانلود", "callback_data": f"bk:dl:{name}"}],
        [{"text": "♻️ ریستور", "callback_data": f"bk:restore:{name}"},
         {"text": "🗑 حذف", "callback_data": f"bk:rm:{name}"}],
        [{"text": "◀️ بکاپ‌ها", "callback_data": "menu:backups:0"}, *BACK_BUTTON],
    ]


def respond(api, chat_id, text, keyboard=None, *, message_id=None):
    if isinstance(message_id, int) and len(str(text)) <= 4000:
        try:
            api.edit_message(chat_id, message_id, text, keyboard)
            return
        except TelegramError as exc:
            if "message is not modified" in str(exc).lower():
                return
    api.send_message(chat_id, text, keyboard)


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
        [{"text": "✅ فعال", "callback_data": f"act:enable:{username}"},
         {"text": "⏸ غیرفعال", "callback_data": f"act:disable:{username}"}],
        [{"text": "+۳۰ روز", "callback_data": f"act:extend30:{username}"},
         {"text": "+۶۰ روز", "callback_data": f"act:extend60:{username}"},
         {"text": "+۹۰ روز", "callback_data": f"act:extend90:{username}"}],
        [{"text": "✏️ ویرایش", "callback_data": f"prompt:edit:{username}"},
         {"text": "🔑 تغییر رمز", "callback_data": f"prompt:password:{username}"}],
        [{"text": "🔄 ریست ترافیک", "callback_data": f"act:resettraffic:{username}"},
         {"text": "🗑 حذف", "callback_data": f"act:delete:{username}"}],
        [{"text": "◀️ کاربران", "callback_data": "menu:users:0"}, *BACK_BUTTON],
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
    if command == "start":
        return HOME_TEXT, MAIN_MENU
    if command == "help":
        return HELP, [BACK_BUTTON]
    if command == "id":
        return f"شناسهٔ تلگرام: {chat_id}", MAIN_MENU
    if command == "status":
        return "📊 وضعیت سرور\n" + status_text(), [
            [{"text": "🔄 بروزرسانی", "callback_data": "menu:status"},
             {"text": "👥 کاربران", "callback_data": "menu:users:0"}], BACK_BUTTON,
        ]
    if command == "users":
        return users_page(" ".join(args))
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
        return backups_page()
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
        ), [
            [{"text": "🌐 آدرس وب", "callback_data": "prompt:web"},
             {"text": "🔌 پورت SSH", "callback_data": "prompt:sshport"}],
            [{"text": "🕒 زمان بکاپ", "callback_data": "prompt:schedule"},
             {"text": "🔐 رمز مدیر", "callback_data": "prompt:adminpassword"}],
            [{"text": "✅ تأیید پورت SSH", "callback_data": "cmd:sshconfirm"},
             {"text": "↩️ لغو تغییر پورت", "callback_data": "cmd:sshcancel"}],
            BACK_BUTTON,
        ]
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


def wizard_prompt(api, chat_id, user_id, pending, *, message_id=None):
    state = pending[("flow", user_id)]
    fields = WIZARD_FIELDS[state["kind"]]
    key, instruction, default = fields[state["index"]]
    keyboard = []
    if key == "first":
        keyboard.append([{"text": "✅ بله", "callback_data": "flow:value:1"},
                         {"text": "❌ خیر", "callback_data": "flow:value:0"}])
    elif default is not None:
        label = "رد کردن" if default == "" else f"پیش‌فرض: {default}"
        keyboard.append([{"text": label, "callback_data": "flow:skip"}])
    keyboard.append([{"text": "✖️ لغو", "callback_data": "flow:cancel"}])
    title = "ساخت کاربر" if state["kind"] == "create" else "ساخت گروهی"
    respond(api, chat_id, f"{title} · مرحلهٔ {state['index'] + 1} از {len(fields)}\n{instruction}",
            keyboard, message_id=message_id)


def start_wizard(api, chat_id, user_id, pending, kind, *, message_id=None):
    pending.pop(user_id, None)
    pending.pop(("input", user_id), None)
    pending[("flow", user_id)] = {"kind": kind, "index": 0, "values": {},
                                  "expires": time.monotonic() + 600}
    wizard_prompt(api, chat_id, user_id, pending, message_id=message_id)


def validate_wizard_value(kind, key, value):
    if key in ("username", "prefix"):
        if not USERNAME_RE.fullmatch(value) or key == "prefix" and len(value) > 20:
            raise BotCommandError("فقط حروف کوچک انگلیسی، عدد، خط تیره یا زیرخط؛ شروع با حرف یا زیرخط.")
    elif key == "password":
        if not value or len(value) > 256 or any(char in value for char in "\r\n\x00"):
            raise BotCommandError("رمز باید بین ۱ تا ۲۵۶ نویسه و بدون خط جدید باشد.")
    elif key in ("count", "start", "days", "limit"):
        limits = {"count": (1, 100), "start": (0, 999999999),
                  "days": (1, 36500), "limit": (0, 10000)}
        lower, upper = limits[key]
        if not value.isascii() or not value.isdecimal() or not lower <= int(value) <= upper:
            raise BotCommandError(f"برای این مرحله عددی بین {lower} و {upper} بفرست.")
    elif key == "traffic":
        try:
            traffic = Decimal(value)
        except InvalidOperation:
            raise BotCommandError("ترافیک را به گیگابایت و به صورت عدد بفرست.") from None
        if not traffic.is_finite() or traffic != 0 and not Decimal("0.001") <= traffic <= Decimal("100000"):
            raise BotCommandError("ترافیک باید ۰ (نامحدود) یا بین ۰٫۰۰۱ تا ۱۰۰۰۰۰ گیگابایت باشد.")
    elif key == "first" and value not in ("0", "1"):
        raise BotCommandError("برای شروع از اولین اتصال، ۱ یا ۰ را بفرست.")
    elif key == "referral" and "\x00" in value:
        raise BotCommandError("متن معرف معتبر نیست.")


def wizard_value(api, chat_id, user_id, pending, value, *, skip=False, message_id=None):
    state = pending.get(("flow", user_id))
    if not state or state["expires"] < time.monotonic():
        pending.pop(("flow", user_id), None)
        respond(api, chat_id, "فرم منقضی شد. دوباره از منو شروع کن.", MAIN_MENU, message_id=message_id)
        return
    fields = WIZARD_FIELDS[state["kind"]]
    key, _, default = fields[state["index"]]
    if skip:
        if default is None:
            wizard_prompt(api, chat_id, user_id, pending, message_id=message_id)
            return
        value = default
    try:
        validate_wizard_value(state["kind"], key, value)
    except BotCommandError as exc:
        respond(api, chat_id, f"{exc}\nدوباره مقدار را بفرست.",
                [[{"text": "✖️ لغو", "callback_data": "flow:cancel"}]], message_id=message_id)
        return
    state["values"][key] = value
    state["index"] += 1
    state["expires"] = time.monotonic() + 600
    if state["index"] < len(fields):
        wizard_prompt(api, chat_id, user_id, pending, message_id=message_id)
        return
    command = "/" + state["kind"] + " " + " ".join(
        shlex.quote(f"{field}={field_value}") for field, field_value in state["values"].items()
    )
    nonce = secrets.token_hex(6)
    pending[user_id] = (nonce, time.monotonic() + 120, command)
    pending.pop(("flow", user_id), None)
    values = state["values"]
    summary = (f"{'👥 ساخت گروهی' if state['kind'] == 'bulkcreate' else '👤 ساخت کاربر'}\n"
               f"{'تعداد: ' + values['count'] + ' · پیشوند: ' + values['prefix'] if state['kind'] == 'bulkcreate' else 'نام: ' + values['username']}\n"
               f"اعتبار: {values['days']} روز · اتصال: {values['limit']} · ترافیک: {values['traffic']} گیگابایت\n"
               f"معرف: {values['referral'][:80] or '—'}\n"
               f"شروع از اولین اتصال: {'بله' if values['first'] == '1' else 'خیر'}\n"
               "رمز در این پیش‌نمایش نمایش داده نمی‌شود. ساخت را تأیید می‌کنی؟")
    respond(api, chat_id, summary, [[
        {"text": "✅ ساخت", "callback_data": f"ok:{nonce}"},
        {"text": "✖️ لغو", "callback_data": f"cancel:{nonce}"},
    ]], message_id=message_id)


def start_input(api, chat_id, user_id, pending, kind, username="", *, message_id=None):
    pending.pop(user_id, None)
    pending.pop(("flow", user_id), None)
    pending[("input", user_id)] = (kind, username, time.monotonic() + 300)
    respond(api, chat_id, INPUT_PROMPTS[kind],
            [[{"text": "✖️ لغو", "callback_data": "flow:cancel"}]], message_id=message_id)


def consume_input(config, api, chat_id, user_id, pending, value):
    kind, username, expires = pending.pop(("input", user_id))
    if expires < time.monotonic():
        api.send_message(chat_id, "درخواست منقضی شد. دوباره از منو شروع کن.", MAIN_MENU)
        return
    if not value.strip():
        api.send_message(chat_id, "مقدار خالی است. دوباره از منو شروع کن.", MAIN_MENU)
        return
    if kind == "search":
        text, keyboard = users_page(value.strip())
        api.send_message(chat_id, text, keyboard)
        return
    commands = {
        "edit": f"/edit {shlex.quote(username)} {value}",
        "password": f"/password {shlex.quote(username)} {shlex.quote(value)}",
        "sshport": f"/sshport {value}",
        "web": f"/web {value}",
        "schedule": f"/schedule {value}",
        "adminpassword": f"/adminpassword {value}",
    }
    dispatch(config, api, chat_id, user_id, commands[kind], pending)


def dispatch(config, api, chat_id, user_id, command_line, pending, *, confirmed=False, message_id=None):
    if needs_confirmation(command_line) and not confirmed:
        nonce = secrets.token_hex(6)
        pending[user_id] = (nonce, time.monotonic() + 120, command_line)
        command = command_line.split(maxsplit=1)[0]
        respond(api, chat_id, f"اجرای {command} را تأیید می‌کنی؟", [[
            {"text": "تأیید", "callback_data": f"ok:{nonce}"},
            {"text": "لغو", "callback_data": f"cancel:{nonce}"},
        ]], message_id=message_id)
        return
    try:
        result, keyboard = execute(config, api, chat_id, command_line)
    except (BotCommandError, ProvisionError, BackupError, TelegramError, OSError, ValueError) as exc:
        respond(api, chat_id, f"خطا: {exc}", MAIN_MENU, message_id=message_id)
    else:
        respond(api, chat_id, result, keyboard, message_id=message_id)


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
        message_id = message.get("message_id")
        if data.startswith("menu:"):
            command = data[5:]
            if command == "home":
                respond(api, chat_id, HOME_TEXT, MAIN_MENU, message_id=message_id)
            elif command.startswith("users:") and command[6:].isdecimal():
                body, keyboard = users_page(page=int(command[6:]))
                respond(api, chat_id, body, keyboard, message_id=message_id)
            elif command.startswith("backups:") and command[8:].isdecimal():
                body, keyboard = backups_page(page=int(command[8:]))
                respond(api, chat_id, body, keyboard, message_id=message_id)
            elif command in {"status", "users", "backups", "settings", "help", "audit"}:
                dispatch(config, api, chat_id, user_id, f"/{command}", pending, message_id=message_id)
        elif data.startswith("u:"):
            dispatch(config, api, chat_id, user_id, f"/user {shlex.quote(data[2:])}",
                     pending, message_id=message_id)
        elif data.startswith("b:"):
            try:
                body, keyboard = backup_detail(data[2:])
            except BotCommandError as exc:
                respond(api, chat_id, f"خطا: {exc}", MAIN_MENU, message_id=message_id)
            else:
                respond(api, chat_id, body, keyboard, message_id=message_id)
        elif data == "bk:create":
            dispatch(config, api, chat_id, user_id, "/backup", pending, message_id=message_id)
        elif data.startswith("bk:dl:"):
            dispatch(config, api, chat_id, user_id, f"/download {shlex.quote(data[6:])}",
                     pending, message_id=message_id)
        elif data.startswith("bk:restore:"):
            dispatch(config, api, chat_id, user_id, f"/restore {shlex.quote(data[11:])}",
                     pending, message_id=message_id)
        elif data.startswith("bk:rm:"):
            dispatch(config, api, chat_id, user_id, f"/removebackup {shlex.quote(data[6:])}",
                     pending, message_id=message_id)
        elif data in {"flow:create", "flow:bulkcreate"}:
            start_wizard(api, chat_id, user_id, pending, data[5:], message_id=message_id)
        elif data == "flow:skip":
            wizard_value(api, chat_id, user_id, pending, "", skip=True, message_id=message_id)
        elif data in {"flow:value:0", "flow:value:1"}:
            wizard_value(api, chat_id, user_id, pending, data[-1], message_id=message_id)
        elif data == "flow:cancel":
            pending.pop(user_id, None)
            pending.pop(("flow", user_id), None)
            pending.pop(("input", user_id), None)
            respond(api, chat_id, HOME_TEXT, MAIN_MENU, message_id=message_id)
        elif data.startswith("prompt:"):
            _, kind, *rest = data.split(":", 2)
            if kind in INPUT_PROMPTS:
                start_input(api, chat_id, user_id, pending, kind, rest[0] if rest else "",
                            message_id=message_id)
        elif data in {"cmd:sshconfirm", "cmd:sshcancel"}:
            dispatch(config, api, chat_id, user_id, "/" + data[4:], pending,
                     message_id=message_id)
        elif data.startswith("act:"):
            _, action, username = (data.split(":", 2) + ["", ""])[:3]
            if action in {"enable", "disable", "resettraffic", "delete"}:
                dispatch(config, api, chat_id, user_id, f"/{action} {shlex.quote(username)}",
                         pending, message_id=message_id)
            elif action in {"extend30", "extend60", "extend90"}:
                dispatch(config, api, chat_id, user_id,
                         f"/extend {shlex.quote(username)} {action[6:]}", pending,
                         message_id=message_id)
        elif data.startswith(("ok:", "cancel:")):
            operation, nonce = data.split(":", 1)
            saved = pending.get(user_id)
            if not saved or saved[0] != nonce or saved[1] < time.monotonic():
                respond(api, chat_id, "این درخواست منقضی شده است.", MAIN_MENU,
                        message_id=message_id)
                return
            pending.pop(user_id, None)
            if operation == "cancel":
                respond(api, chat_id, "لغو شد.", MAIN_MENU, message_id=message_id)
            else:
                dispatch(config, api, chat_id, user_id, saved[2], pending,
                         confirmed=True, message_id=message_id)
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
    text = message.get("text") or ""
    if text.strip() == "/cancel":
        pending.pop(user_id, None)
        pending.pop(("flow", user_id), None)
        pending.pop(("input", user_id), None)
        api.send_message(chat_id, HOME_TEXT, MAIN_MENU)
        return
    if not text.lstrip().startswith("/") and ("flow", user_id) in pending:
        wizard_value(api, chat_id, user_id, pending, text)
        return
    if not text.lstrip().startswith("/") and ("input", user_id) in pending:
        consume_input(config, api, chat_id, user_id, pending, text)
        return
    if not text.lstrip().startswith("/"):
        api.send_message(chat_id, HOME_TEXT, MAIN_MENU)
        return
    pending.pop(("flow", user_id), None)
    pending.pop(("input", user_id), None)
    dispatch(config, api, chat_id, user_id, text.strip(), pending)


def run_polling():
    pending = {}
    configured_token = None
    webhook_removed = False
    api = None
    while True:
        close_old_connections()
        if not panel_https_enabled():
            if api is not None:
                api.close()
                api = None
            configured_token = None
            webhook_removed = False
            pending.clear()
            time.sleep(5)
            continue
        config = TelegramBotConfig.objects.select_related("owner").filter(pk=1).first()
        token = decrypt_bot_token(config.token_ciphertext) if config and config.enabled else None
        if not token:
            if api is not None:
                api.close()
                api = None
            configured_token = None
            webhook_removed = False
            pending.clear()
            time.sleep(5)
            continue
        if token != configured_token:
            if api is not None:
                api.close()
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
