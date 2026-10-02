(() => {
  const language = document.documentElement.lang === "fa" ? "fa" : "en";
  const toggle = document.querySelector("[data-language-toggle]");
  if (toggle) {
    toggle.textContent = language === "fa" ? "English" : "فارسی";
    toggle.setAttribute("aria-label", language === "fa" ? "تغییر زبان به انگلیسی" : "Switch to Persian");
    toggle.addEventListener("click", () => {
      const nextLanguage = language === "fa" ? "en" : "fa";
      try { localStorage.setItem("sshvpn-language", nextLanguage); } catch (_) { /* Storage may be unavailable. */ }
      document.cookie = `django_language=${nextLanguage}; Path=/; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
      window.location.reload();
    });
  }

  const fa = {
    "Control panel": "پنل مدیریت", "SSH VPN PANEL": "پنل SSH VPN", "Administrator": "مدیر", "Administrator account": "حساب مدیر",
    "Dashboard": "داشبورد", "Overview": "داشبورد", "Users and server overview": "کاربران و وضعیت سرور",
    "Audit log": "گزارش فعالیت", "Account activity history": "تاریخچهٔ فعالیت حساب‌ها",
    "Settings": "تنظیمات", "Access, port, and backups": "دسترسی، پورت و پشتیبان‌گیری",
    "Dark mode": "حالت تاریک", "Light mode": "حالت روشن", "Menu": "منو",
    "Navigation": "ناوبری", "WORKSPACE": "بخش‌های پنل", "SERVER RESOURCES": "منابع سرور",
    "Sign out": "خروج", "SSH VPN control panel": "پنل مدیریت SSH VPN",
    "Sign in": "ورود", "Welcome back": "خوش آمدید", "ADMIN ACCESS": "ورود مدیر",
    "Sign in to manage VPN accounts and monitor your server.": "برای مدیریت حساب‌های VPN و پایش سرور وارد شوید.",
    "Invalid username or password.": "نام کاربری یا رمز عبور نادرست است.",
    "Username": "نام کاربری", "Password": "رمز عبور", "Old password": "رمز عبور فعلی",
    "New password confirmation": "تأیید رمز عبور جدید",
    "SERVER OVERVIEW": "نمای کلی سرور", "Manage SSH tunnel accounts and see who's connected right now.": "حساب‌های تونل SSH را مدیریت کنید و اتصال‌های فعال را ببینید.",
    "Total users": "کل کاربران", "All VPN accounts": "همهٔ حساب‌های VPN",
    "Online now": "آنلاین در لحظه", "Connected users, updated live": "کاربران متصل، با به‌روزرسانی زنده",
    "Inactive users": "کاربران غیرفعال", "Disabled or expired": "غیرفعال یا منقضی‌شده",
    "Disabled, expired or out of traffic": "غیرفعال، منقضی یا بدون ترافیک",
    "YOUR USERS": "کاربران شما", "VPN accounts": "حساب‌های VPN",
    "New user": "کاربر جدید", "Bulk users": "ساخت گروهی",
    "Search users": "جستجوی کاربران", "Username or referral": "نام کاربری یا رفرال",
    "Search": "جستجو", "Clear": "پاک کردن", "Choose action": "انتخاب عملیات",
    "Action": "عملیات", "Apply to selected": "اعمال روی انتخاب‌شده‌ها",
    "enable": "فعال‌سازی", "disable": "غیرفعال‌سازی", "extend": "تمدید",
    "usage-reset": "ریست ترافیک", "delete": "حذف",
    "Clear selection": "لغو انتخاب", "Enable": "فعال‌سازی", "Disable": "غیرفعال‌سازی",
    "Extend validity": "تمدید اعتبار", "Reset traffic": "ریست ترافیک", "Delete": "حذف",
    "Days": "روز", "days": "روز", "Account": "کاربر", "Referral": "رفرال", "Status": "وضعیت",
    "Online": "آنلاین", "Data usage": "مصرف داده", "Days left": "روزهای باقی‌مانده",
    "Limit": "محدودیت", "Actions": "عملیات", "Show": "نمایش", "Hide": "پنهان‌سازی",
    "Referral text": "متن رفرال", "Not set": "ثبت‌نشده", "View users": "مشاهدهٔ کاربران",
    "Active": "فعال", "Disabled": "غیرفعال", "Expired": "منقضی‌شده",
    "Traffic exhausted": "ترافیک تمام شده", "Awaiting first connection": "در انتظار اولین اتصال",
    "Offline": "آفلاین", "Unlimited": "نامحدود", "Unavailable": "ناموجود",
    "No users match this search.": "کاربری با این جستجو پیدا نشد.",
    "No accounts yet. Use the + New user button to create one.": "هنوز کاربری ساخته نشده است. از دکمهٔ «کاربر جدید» استفاده کنید.",
    "QUICK RENEWAL": "تمدید سریع", "Extend": "تمدید",
    "Days are added to the current expiry. Expired accounts start from today.": "روزها به تاریخ انقضای فعلی اضافه می‌شوند. حساب منقضی از امروز محاسبه می‌شود.",
    "BULK VPN USERS": "ساخت گروهی کاربران", "Create users in bulk": "ساخت گروهی کاربران",
    "Each username gets a unique random number at or above the minimum. Up to 100 users per batch.": "برای هر نام کاربری یک شمارهٔ تصادفی و یکتا از حداقل تعیین‌شده به بالا ساخته می‌شود. هر نوبت حداکثر ۱۰۰ کاربر.",
    "Number of users": "تعداد کاربران", "Username prefix": "پیشوند نام کاربری",
    "Minimum number": "حداقل شماره", "Fixed password (optional)": "رمز ثابت (اختیاری)",
    "Generated password": "رمز تولیدشده", "Password length": "طول رمز",
    "Simultaneous connections": "اتصال‌های هم‌زمان", "Traffic limit (GiB)": "سقف ترافیک (گیگابایت)",
    "Blank means unlimited.": "خالی یعنی نامحدود.", "0 means unlimited.": "صفر یعنی نامحدود.", "0 means unlimited. Leave blank to keep the current limit.": "صفر یعنی نامحدود؛ خالی یعنی حفظ محدودیت فعلی.", "Active days": "روزهای اعتبار",
    "Referral text for all users (optional)": "متن رفرال برای همهٔ کاربران (اختیاری)",
    "This same text is saved with every user created in the batch. It does not link to another account.": "همین متن برای همهٔ کاربران این گروه ذخیره می‌شود و به حساب دیگری پیوند ندارد.",
    "Start validity on first connection": "شروع اعتبار از اولین اتصال",
    "Cancel": "انصراف", "Create users": "ساخت کاربران",
    "CONNECTION DETAILS": "اطلاعات اتصال", "Copy manually": "کپی دستی",
    "Automatic clipboard access is unavailable in this browser. Select and copy the details below.": "دسترسی خودکار به کلیپ‌بورد در این مرورگر ممکن نیست. متن زیر را انتخاب و کپی کنید.",
    "LIVE CONNECTIONS": "اتصال‌های فعال", "IP addresses": "نشانی‌های IP",
    "Addresses of currently connected SSH VPN clients.": "نشانی IP کاربران SSH VPN که اکنون متصل‌اند.",
    "NEW VPN USER": "کاربر VPN جدید", "Create account": "ساخت کاربر",
    "SSH tunnel and SOCKS forwarding only. Shell and SFTP are blocked.": "فقط تونل SSH و SOCKS مجاز است؛ شِل و SFTP مسدود هستند.",
    "ACCOUNT SETTINGS": "تنظیمات کاربر", "Edit": "ویرایش",
    "This user's referral code": "کد معرف این کاربر",
    "Any unique length. Clear to remove the code.": "کد یکتای دلخواه؛ برای حذف، فیلد را خالی کنید.",
    "New password": "رمز عبور جدید", "Leave blank to keep the current password.": "برای حفظ رمز فعلی خالی بگذارید.",
    "New validity (days)": "اعتبار جدید (روز)",
    "Leave blank to keep the current expiry. Enter days to count from now.": "برای حفظ تاریخ فعلی خالی بگذارید؛ با وارد کردن تعداد روز، اعتبار از امروز محاسبه می‌شود.",
    "Leave blank to keep the current limit.": "برای حفظ محدودیت فعلی خالی بگذارید.",
    "Blank keeps the current limit; 0 means unlimited.": "خالی یعنی حفظ محدودیت فعلی؛ صفر یعنی نامحدود.",
    "Save changes": "ذخیرهٔ تغییرات",
    "Username stays the same. Current expiry:": "نام کاربری تغییر نمی‌کند. انقضای فعلی:",
    "Choose your own code. Leave blank to set it later.": "کد دلخواه را وارد کنید یا برای تعیین بعدی خالی بگذارید.",
    "Choose any unique code. Leave blank to set it later.": "کد یکتا وارد کنید یا برای تعیین بعدی خالی بگذارید.",
    "Introduced by referral code": "کد معرفِ معرفی‌کننده",
    "SSH login name, e.g. ali or vpn_ali": "نام ورود SSH؛ برای نمونه ali یا vpn_ali",
    "Optional. Leave blank to generate a unique password per user.": "اختیاری؛ برای ساخت رمز یکتا برای هر کاربر خالی بگذارید.",
    "Optional. The same text is saved for every user in this batch.": "اختیاری؛ همین متن برای همهٔ کاربران گروه ذخیره می‌شود.",
    "Leave blank for unlimited traffic.": "برای ترافیک نامحدود خالی بگذارید.",
    "Numbers": "عدد", "Letters and numbers": "حروف و عدد",
    "CREATED ACCOUNTS": "کاربران ساخته‌شده", "Creation result": "نتیجهٔ ساخت",
    "Connection details": "اطلاعات اتصال", "Copy all": "کپی همه",
    "Download CSV": "دانلود CSV", "Expiry": "تاریخ انقضا",
    "Connections": "اتصال‌ها", "Traffic": "ترافیک", "Back to users": "بازگشت به کاربران",
    "The CSV contains passwords. Keep the file private and delete copies you no longer need.": "فایل CSV شامل رمزهاست؛ آن را خصوصی نگه دارید و نسخه‌های اضافی را پاک کنید.",
    "CONTROL PANEL": "پنل مدیریت", "Manage server access, the panel address, your account, and data recovery.": "دسترسی سرور، نشانی پنل، حساب مدیر و بازیابی داده را مدیریت کنید.",
    "← Back to dashboard": "بازگشت به داشبورد ←", "PREFERENCES": "تنظیمات",
    "SSH access": "دسترسی SSH", "Web address": "نشانی وب", "Backup & recovery": "پشتیبان‌گیری و بازیابی",
    "Panel services are managed on this server.": "سرویس‌های پنل روی همین سرور مدیریت می‌شوند.",
    "SERVER CONNECTION": "اتصال سرور", "One SSH port serves administrators and VPN users.": "مدیران و کاربران VPN از یک پورت SSH استفاده می‌کنند.",
    "CURRENT PORT": "پورت فعلی", "UDPGW TCP PORT": "پورت TCP درگاه UDP", "Via SSH tunnel": "از طریق تونل SSH", "Port change in progress": "تغییر پورت در حال انجام",
    "Cancel change": "لغو تغییر", "Change SSH port": "تغییر پورت SSH",
    "The current port stays open until you test and confirm the new one.": "پورت فعلی تا زمان آزمایش و تأیید پورت جدید باز می‌ماند.",
    "New port": "پورت جدید", "Start change": "شروع تغییر",
    "PANEL ACCESS": "دسترسی پنل", "Choose the URL path and": "مسیر نشانی و پورت",
    "port used to open this panel.": "برای باز کردن این پنل را انتخاب کنید.",
    "CURRENT ADDRESS": "نشانی فعلی", "Address change pending": "تغییر نشانی در انتظار تأیید",
    "Confirm this address": "تأیید این نشانی", "Private path": "مسیر خصوصی",
    "One segment, such as /my-panel. Use / to open the panel at the root.": "یک بخش مانند ‎/my-panel‎؛ برای باز کردن پنل در ریشه از / استفاده کنید.",
    "Allow the new port in your server and provider firewalls.": "پورت جدید را در فایروال سرور و ارائه‌دهنده باز کنید.",
    "Port 80 stays available for certificate renewal.": "پورت ۸۰ برای تمدید گواهی باز می‌ماند.",
    "Open the new URL and confirm it within five minutes; otherwise the old address is restored automatically.": "نشانی جدید را باز کنید و تا پنج دقیقه تأیید کنید؛ وگرنه نشانی قبلی خودکار برمی‌گردد.",
    "Change web address": "تغییر نشانی وب", "ACCOUNT SECURITY": "امنیت حساب",
    "Administrator password": "رمز مدیر", "Update the password used to sign in to this panel.": "رمز ورود به پنل را تغییر دهید.",
    "Current password": "رمز فعلی", "Confirm new password": "تأیید رمز جدید",
    "Any nonempty password is accepted.": "هر رمز غیرخالی پذیرفته می‌شود.",
    "Update password": "تغییر رمز", "DATA & RECOVERY": "داده و بازیابی",
    "Backup and restore": "پشتیبان‌گیری و بازیابی",
    "Keep a complete copy of panel data for migration or recovery.": "برای انتقال یا بازیابی، نسخه‌ای کامل از داده‌های پنل نگه دارید.",
    "Panel backup": "بکاپ پنل",
    "Export the database, VPN accounts, limits, traffic totals, and administrator data.": "پایگاه داده، حساب‌های VPN، محدودیت‌ها، مصرف ترافیک و دادهٔ مدیر را صادر کنید.",
    "Manage backups": "مدیریت بکاپ‌ها", "SETTINGS / DATA & RECOVERY": "تنظیمات / داده و بازیابی",
    "Keep a complete copy of the panel or move it to another Ubuntu 24.04 server.": "نسخه‌ای کامل از پنل نگه دارید یا آن را به سرور دیگری با Ubuntu 24.04 منتقل کنید.",
    "← Back to settings": "بازگشت به تنظیمات ←",
    "Backups contain sensitive account data and are not encrypted. This panel is using HTTP; use an administrator SSH tunnel or enable HTTPS before downloading or uploading a backup over an untrusted network.": "بکاپ شامل اطلاعات حساس حساب‌هاست و رمزگذاری نشده است. این پنل از HTTP استفاده می‌کند؛ برای انتقال بکاپ در شبکهٔ نامطمئن از تونل SSH مدیر یا HTTPS استفاده کنید.",
    "EXPORT": "خروجی", "Create a backup": "ساخت بکاپ",
    "Download a full copy of this panel.": "نسخهٔ کامل پنل را دانلود کنید.",
    "Includes the full PostgreSQL database, administrators, VPN account details and password hashes, account limits, audit history, and saved traffic totals.": "شامل کل پایگاه دادهٔ PostgreSQL، مدیران، جزئیات حساب‌های VPN و هش رمزها، محدودیت‌ها، سوابق فعالیت و مصرف ترافیک است.",
    "The archive is not encrypted. Store it privately.": "فایل بکاپ رمزگذاری نشده است؛ آن را خصوصی نگه دارید.",
    "Create backup": "ساخت بکاپ", "IMPORT": "ورود داده",
    "Restore from file": "بازیابی از فایل", "Bring panel data onto an installed server.": "داده‌های پنل را به سرور نصب‌شده منتقل کنید.",
    "Restoring replaces the destination panel database and administrator accounts. Its web address, TLS mode, database connection, and SSH port stay local.": "بازیابی، پایگاه داده و حساب‌های مدیر مقصد را جایگزین می‌کند. نشانی وب، حالت TLS، اتصال پایگاه داده و پورت SSH مقصد حفظ می‌شوند.",
    "Backup file (.tar.gz)": "فایل بکاپ (‎.tar.gz‎)",
    "Replace this panel's data with the backup": "داده‌های این پنل را با بکاپ جایگزین کن",
    "Restore backup": "بازیابی بکاپ", "SAVED ON THIS SERVER": "ذخیره‌شده در این سرور",
    "USER MIGRATION": "انتقال کاربران", "Import users from Shahan": "وارد کردن کاربران شاهان",
    "Add VPN users without replacing this panel's administrator or server settings.": "کاربران VPN را بدون جایگزینی مدیر یا تنظیمات سرور وارد کنید.",
    "Upload a users-only archive converted from a Shahan SQL backup. Usernames, passwords, connection limits, expiry, status and referral text are imported. An existing username stops the import; no user is overwritten. A recovery backup is created first.": "فایل مخصوص کاربران را که از بکاپ SQL شاهان تبدیل شده بارگذاری کنید. نام کاربری، رمز، سقف اتصال، انقضا، وضعیت و متن معرف وارد می‌شوند. اگر نام کاربری تکراری باشد، ورود متوقف می‌شود و هیچ کاربری جایگزین نمی‌شود. ابتدا بکاپ بازیابی ساخته می‌شود.",
    "The archive contains plaintext VPN passwords. Keep it private and upload it over HTTPS or an administrator SSH tunnel.": "این فایل رمزهای VPN را به‌صورت متن آشکار دارد. آن را خصوصی نگه دارید و با HTTPS یا تونل SSH مدیریتی بارگذاری کنید.",
    "Users-only archive (.tar.gz)": "فایل فقط کاربران (‎.tar.gz‎)",
    "Add these users to this panel": "این کاربران را به پنل اضافه کن", "Import users": "وارد کردن کاربران",
    "Available backups": "بکاپ‌های موجود", "Download a copy to keep outside this server.": "نسخه‌ای را دانلود و بیرون از این سرور نگه دارید.",
    "File": "فایل", "Created": "زمان ساخت", "Size": "حجم", "Download": "دانلود",
    "No backups on this server yet. Create one above, then download it.": "هنوز بکاپی در سرور نیست. یکی بسازید و سپس دانلودش کنید.",
    "ACCOUNT ACTIVITY": "فعالیت حساب‌ها", "A record of account changes made through the panel.": "تاریخچهٔ تغییرات حساب‌ها از طریق پنل.",
    "← Dashboard": "داشبورد ←", "RECENT EVENTS": "رویدادهای اخیر",
    "History": "تاریخچه", "Time": "زمان", "Result": "نتیجه",
    "Success": "موفق", "Failed": "ناموفق",
    "Create": "ساخت", "Update": "ویرایش", "Usage-reset": "ریست ترافیک",
    "Web-stage": "شروع تغییر نشانی", "Web-confirm": "تأیید نشانی",
    "Port-stage": "شروع تغییر پورت", "Port-finalize": "تأیید پورت", "Port-cancel": "لغو تغییر پورت",
    "No activity has been recorded yet.": "هنوز فعالیتی ثبت نشده است.",
    "← Newer": "جدیدتر ←", "Older →": "قدیمی‌تر →",
    "Backups": "بکاپ‌ها", "Restore completed": "بازیابی کامل شد",
    "Changing web address": "تغییر نشانی وب",
    "Address change pending": "تغییر نشانی در انتظار تأیید",
    "If the new port is blocked or the page does not load, the previous address will return automatically after five minutes.": "اگر پورت جدید بسته باشد یا صفحه باز نشود، نشانی قبلی پس از پنج دقیقه خودکار برمی‌گردد.",
    "Open new address": "باز کردن نشانی جدید", "Open the new panel address": "باز کردن نشانی جدید پنل",
    "Cancel change": "لغو تغییر", "New address:": "نشانی جدید:",
    "The new address is being activated. Wait a few seconds, then open it and confirm the change in Settings within five minutes.": "نشانی جدید در حال فعال‌شدن است. چند ثانیه صبر کنید، سپس آن را باز و تا پنج دقیقه در تنظیمات تأیید کنید.",
    "RECOVERY COMPLETE": "بازیابی کامل شد", "Backup restored": "بکاپ بازیابی شد",
    "Go to sign in": "رفتن به صفحهٔ ورود",
    "Copy manually": "کپی دستی", "Loading active connections…": "در حال بارگذاری اتصال‌های فعال…",
    "No active connections now.": "اکنون اتصالی فعال نیست.",
    "Could not load active IP addresses.": "نشانی‌های IP فعال بارگذاری نشدند.",
    "Could not load password": "رمز عبور بارگذاری نشد.",
    "Could not load the password.": "رمز عبور بارگذاری نشد.",
    "Password unavailable. Set a new password first.": "رمز عبور موجود نیست؛ ابتدا رمز جدید تعیین کنید.",
    "Unavailable — set a new password": "رمز موجود نیست؛ رمز جدید تعیین کنید",
    "IP unavailable — reconnect needed": "IP در دسترس نیست؛ اتصال دوباره لازم است",
    "Select and copy the connection details.": "اطلاعات اتصال را انتخاب و کپی کنید.",
    "Usage unavailable": "اطلاعات مصرف در دسترس نیست.",
    "Waiting for server data": "در انتظار دادهٔ سرور",
    "Disk": "دیسک", "Unknown": "نامشخص", "System": "سیستم",
    "Switch to dark mode": "تغییر به حالت تاریک", "Switch to light mode": "تغییر به حالت روشن",
    "Open navigation menu": "باز کردن منو", "Close navigation menu": "بستن منو",
    "Allow the new port in your server and provider firewalls. Open the new URL and confirm it within five minutes; otherwise the old address is restored automatically.": "پورت جدید را در فایروال سرور و ارائه‌دهنده باز کنید. نشانی جدید را باز کنید و تا پنج دقیقه تأیید کنید؛ وگرنه نشانی قبلی خودکار برمی‌گردد.",
    "Allow the new port in your server and provider firewalls. Port 80 stays available for certificate renewal. Open the new URL and confirm it within five minutes; otherwise the old address is restored automatically.": "پورت جدید را در فایروال سرور و ارائه‌دهنده باز کنید. پورت ۸۰ برای تمدید گواهی باز می‌ماند. نشانی جدید را باز کنید و تا پنج دقیقه تأیید کنید؛ وگرنه نشانی قبلی خودکار برمی‌گردد.",
    "Close menu": "بستن منو", "Server resource usage": "مصرف منابع سرور",
    "Panel navigation": "ناوبری پنل", "VPN account overview": "نمای کلی حساب‌های VPN",
    "Select all visible users": "انتخاب همهٔ کاربران قابل‌نمایش",
    "Create VPN account": "ساخت کاربر VPN", "Create VPN users in bulk": "ساخت گروهی کاربران VPN",
    "Settings navigation": "ناوبری تنظیمات", "Audit log pages": "صفحه‌های گزارش فعالیت",
    "Connection details": "اطلاعات اتصال",
    "Use": "استفاده کنید از", "or a port from 1024 to 65535.": "یا پورتی از ۱۰۲۴ تا ۶۵۵۳۵.",
    "VPN accounts and the complete panel database were restored. The panel service will restart in a few seconds. Sign in with an administrator account from the backup.": "حساب‌های VPN و کل پایگاه دادهٔ پنل بازیابی شدند. سرویس پنل تا چند ثانیهٔ دیگر راه‌اندازی مجدد می‌شود. با حساب مدیر موجود در بکاپ وارد شوید.",
    "Existing rows below were created and remain active.": "کاربران زیر ساخته شده‌اند و فعال می‌مانند.",
    "The two password fields didn’t match.": "دو رمز عبور یکسان نیستند.",
    "This password is too common.": "این رمز عبور بیش از حد رایج است.",
    "This password is entirely numeric.": "رمز عبور نباید فقط عدد باشد.",
    "Restore the selected backup? Current panel data and administrator accounts will be replaced. A server-side recovery copy will be created first.": "بکاپ انتخاب‌شده بازیابی شود؟ داده‌های فعلی پنل و حساب‌های مدیر جایگزین می‌شوند. ابتدا یک نسخهٔ بازیابی روی سرور ساخته می‌شود.",
    "Import users from this archive? Existing users and administrators will be kept. A recovery backup will be created first.": "کاربران این فایل وارد شوند؟ کاربران و مدیران فعلی حفظ می‌شوند و ابتدا بکاپ بازیابی ساخته می‌شود.",
    "Host": "هاست", "SSH port": "پورت SSH", "Connection limit": "سقف اتصال",
    "Copy connection details": "کپی اطلاعات اتصال",
    "Traffic limit": "سقف ترافیک", "Referral code": "کد معرف",
    "Could not load password": "رمز عبور بارگذاری نشد.",
    "The new panel address is confirmed.": "نشانی جدید پنل تأیید شد.",
    "Backups could not be listed.": "فهرست بکاپ‌ها بارگذاری نشد.",
    "Administrator password changed.": "رمز مدیر تغییر کرد.",
    "Port change canceled; the original SSH port is active.": "تغییر پورت لغو شد؛ پورت SSH قبلی فعال است.",
    "Choose a valid bulk action.": "یک عملیات گروهی معتبر انتخاب کنید.",
    "Select at least one valid user.": "دست‌کم یک کاربر معتبر انتخاب کنید.",
    "Choose 30, 60, or 90 days.": "۳۰، ۶۰ یا ۹۰ روز را انتخاب کنید.",
    "The selection includes a user that no longer exists. Refresh the list and try again.": "در انتخاب شما کاربری هست که دیگر وجود ندارد. فهرست را تازه‌سازی و دوباره تلاش کنید.",
    "The selection changed while processing. Refresh the list and try again.": "انتخاب کاربران هنگام پردازش تغییر کرد. فهرست را تازه‌سازی و دوباره تلاش کنید.",
    "This referral code is already in use.": "این کد معرف قبلاً استفاده شده است.",
    "Referral code was not found.": "کد معرف پیدا نشد.",
    "This account already exists.": "این حساب از قبل وجود دارد.",
    "This bulk request has already been processed. Open the form again.": "این درخواست گروهی قبلاً پردازش شده است. فرم را دوباره باز کنید.",
    "Choose a backup file and confirm the restore.": "فایل بکاپ را انتخاب و بازیابی را تأیید کنید.",
    "Use 1–32 lowercase letters, digits, underscores or hyphens; start with a letter or underscore.": "از ۱ تا ۳۲ حرف کوچک انگلیسی، عدد، زیرخط یا خط تیره استفاده کنید؛ نام باید با حرف یا زیرخط شروع شود.",
    "Use lowercase letters, digits, underscores or hyphens; start with a letter or underscore.": "از حروف کوچک انگلیسی، عدد، زیرخط یا خط تیره استفاده کنید؛ نام باید با حرف یا زیرخط شروع شود.",
    "The password cannot contain a line break or NUL character.": "رمز عبور نباید خط جدید یا نویسهٔ NUL داشته باشد.",
    "A referral code cannot contain NUL.": "کد معرف نباید نویسهٔ NUL داشته باشد.",
  };

  const patterns = [
    [/^Ports (\d+) and (\d+) are active\. Open a new SSH connection from outside the server on port (\d+) before confirming\.$/, (_, oldPort, newPort, testPort) => `پورت‌های ${oldPort} و ${newPort} فعال‌اند. پیش از تأیید، از بیرون سرور اتصال SSH روی پورت ${testPort} را آزمایش کنید.`],
    [/^Choose the URL path and (HTTP|HTTPS) port used to open this panel\.$/, (_, protocol) => `مسیر نشانی و پورت ${protocol} پنل را انتخاب کنید.`],
    [/^Use (\d+) or a port from 1024 to 65535\.$/, (_, port) => `از پورت ${port} یا پورتی بین ۱۰۲۴ تا ۶۵۵۳۵ استفاده کنید.`],
    [/^(HTTP|HTTPS) port$/, (_, protocol) => `پورت ${protocol}`],
    [/^Confirm port (\d+)$/, (_, port) => `تأیید پورت ${port}`],
    [/^(\d+) selected$/, (_, count) => `${count} انتخاب‌شده`],
    [/^(\d+) matching users$/, (_, count) => `${count} کاربر پیدا شد`],
    [/^(\d+) online$/, (_, count) => `${count} آنلاین`],
    [/^(\d+) days$/, (_, count) => `${count} روز`],
    [/^(\d+) days after first connection$/, (_, count) => `${count} روز پس از اولین اتصال`],
    [/^(\d+) referrals$/, (_, count) => `${count} زیرمجموعه`],
    [/^(\d+) active$/, (_, count) => `${count} فعال`],
    [/^(\d+) of (\d+) users created\.$/, (_, done, total) => `${done} کاربر از ${total} کاربر ساخته شد.`],
    [/^(\d+) of (\d+) users created\. Save the credentials before leaving this page\.$/, (_, done, total) => `${done} کاربر از ${total} کاربر ساخته شد. پیش از خروج از این صفحه، اطلاعات ورود را ذخیره کنید.`],
    [/^Imported (\d+) users\. Recovery backup: (.+)\.$/, (_, count, backup) => `${count} کاربر وارد شد. بکاپ بازیابی: ${backup}.`],
    [/^(\d+) referrals · (\d+) active · (\d+|—) online$/, (_, total, active, online) => `${total} زیرمجموعه · ${active} فعال · ${online} آنلاین`],
    [/^Created (\d{4}-\d\d-\d\d)$/, (_, date) => `ساخته‌شده در ${date}`],
    [/^Via (.+)$/, (_, username) => `با معرفی ${username}`],
    [/^Connection (\d+)$/, (_, number) => `اتصال ${number}`],
    [/^(\d+) CPU cores · load ([\d.]+)$/, (_, cores, load) => `${cores} هستهٔ CPU · بار ${load}`],
    [/^(\d+) CPU cores$/, (_, cores) => `${cores} هستهٔ CPU`],
    [/^([\d.]+ GiB) of ([\d.]+ GiB)$/, (_, used, total) => `${used} از ${total}`],
    [/^Page (\d+) of (\d+)$/, (_, page, total) => `صفحهٔ ${page} از ${total}`],
    [/^(.*) · SSH VPN Panel$/, (_, page) => `${fa[page] || page} · پنل SSH VPN`],
    [/^(\d+) VPN accounts and the complete panel database were restored\. The panel service will restart in a few seconds\. Sign in with an administrator account from the backup\.$/, (_, count) => `${count} حساب VPN و کل پایگاه دادهٔ پنل بازیابی شدند. سرویس پنل تا چند ثانیهٔ دیگر راه‌اندازی مجدد می‌شود. با حساب مدیر موجود در بکاپ وارد شوید.`],
    [/^This password is too short\. It must contain at least (\d+) characters\.$/, (_, count) => `این رمز خیلی کوتاه است؛ باید دست‌کم ${count} کاراکتر داشته باشد.`],
    [/^(\d+) events$/, (_, count) => `${count} رویداد`],
    [/^Delete (\d+) selected users\? This cannot be undone\.$/, (_, count) => `این ${count} کاربر انتخاب‌شده حذف شوند؟ این کار برگشت‌پذیر نیست.`],
    [/^Delete (.+)\?$/, (_, username) => `کاربر ${username} حذف شود؟`],
    [/^Have you connected to the new SSH port (\d+) from outside the server\? The old port will close\.$/, (_, port) => `آیا از بیرون سرور به پورت SSH جدید ${port} وصل شده‌اید؟ پورت قبلی بسته می‌شود.`],
    [/^Reset traffic for (.+)\?$/, (_, username) => `ترافیک کاربر ${username} ریست شود؟`],
    [/^Created (.+)\.$/, (_, username) => `کاربر ${username} ساخته شد.`],
    [/^Updated (.+)\.$/, (_, username) => `کاربر ${username} به‌روز شد.`],
    [/^Added (\d+) days to (.+)\.$/, (_, days, username) => `${days} روز به اعتبار ${username} اضافه شد.`],
    [/^Traffic reset for (.+)\.$/, (_, username) => `ترافیک ${username} ریست شد.`],
    [/^Bulk (.+): (\d+) completed, (\d+) already in the requested state\.$/, (_, action, done, skipped) => `عملیات گروهی ${fa[action] || action}: ${done} انجام شد، ${skipped} از قبل در وضعیت خواسته‌شده بودند.`],
    [/^(\d+) failed\./, (_, count) => `${count} مورد ناموفق بود.`],
    [/^Connection details for (.+) copied\.$/, (_, username) => `اطلاعات اتصال ${username} کپی شد.`],
    [/^Could not save (.+)\.$/, (_, username) => `کاربر ${username} ذخیره نشد.`],
    [/^Port (\d+) is listening\. Test a new SSH connection before closing the old port\.$/, (_, port) => `پورت ${port} فعال است. پیش از بستن پورت قبلی، اتصال SSH جدید را آزمایش کنید.`],
    [/^SSH now uses port (\d+)\. The old port is closed\.$/, (_, port) => `SSH اکنون از پورت ${port} استفاده می‌کند. پورت قبلی بسته شد.`],
    [/^Select (.+)$/, (_, username) => `انتخاب ${username}`],
    [/^Show password for (.+)$/, (_, username) => `نمایش رمز ${username}`],
    [/^Reset traffic for (.+)$/, (_, username) => `ریست ترافیک ${username}`],
    [/^Enable (.+)$/, (_, username) => `فعال‌سازی ${username}`],
    [/^Disable (.+)$/, (_, username) => `غیرفعال‌سازی ${username}`],
    [/^Delete (.+)$/, (_, username) => `حذف ${username}`],
    [/^Edit (.+)$/, (_, username) => `ویرایش ${username}`],
    [/^Extend (.+)$/, (_, username) => `تمدید ${username}`],
    [/^Copy connection details for (.+)$/, (_, username) => `کپی اطلاعات اتصال ${username}`],
    [/^Actions for (.+)$/, (_, username) => `عملیات ${username}`],
    [/^Show active IP addresses for (.+)$/, (_, username) => `نمایش IPهای فعال ${username}`],
  ];

  function translated(value) {
    if (language !== "fa" || !value) return value;
    const match = /^(\s*)([\s\S]*?)(\s*)$/.exec(value);
    const original = match[2];
    let result = fa[original];
    if (result === undefined) {
      for (const [pattern, replace] of patterns) {
        if (pattern.test(original)) { result = original.replace(pattern, replace); break; }
      }
    }
    return result === undefined ? value : match[1] + result + match[3];
  }
  window.panelT = translated;
  window.panelConfirm = (message) => window.confirm(translated(message));

  if (language !== "fa") return;
  document.title = translated(document.title);
  const ignored = ".account-name, [data-password-value], [data-username], [data-password], [data-referral], [data-referral-value], [data-referral-user], [data-sessions-name], .drawer-avatar, .drawer-profile strong, code, pre, script, style, [data-language-toggle]";
  const attributes = ["aria-label", "placeholder", "title"];
  function translateNode(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      if (!node.parentElement || node.parentElement.closest(ignored)) return;
      const next = translated(node.nodeValue);
      if (next !== node.nodeValue) node.nodeValue = next;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || node.matches("script, style, [data-language-toggle]")) return;
    if (!node.matches("[data-language-toggle]")) {
      for (const name of attributes) {
        const value = node.getAttribute(name);
        if (value) {
          const next = translated(value);
          if (next !== value) node.setAttribute(name, next);
        }
      }
    }
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) translateNode(walker.currentNode);
    node.querySelectorAll("[aria-label], [placeholder], [title]").forEach((element) => {
      if (element.matches("[data-language-toggle]")) return;
      for (const name of attributes) {
        const value = element.getAttribute(name);
        if (value) {
          const next = translated(value);
          if (next !== value) element.setAttribute(name, next);
        }
      }
    });
  }
  translateNode(document.body);
  new MutationObserver((changes) => {
    for (const change of changes) {
      if (change.type === "characterData") translateNode(change.target);
      else if (change.type === "attributes") translateNode(change.target);
      else change.addedNodes.forEach(translateNode);
    }
  }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true,
    attributeFilter: attributes });
})();
