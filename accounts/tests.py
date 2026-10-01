from unittest.mock import patch
from io import StringIO
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from .models import AuditEvent, VpnAccount
from .secrets import decrypt_password
from .system_metrics import _cpu_metric, get_system_metrics
from .views import format_bytes


class AccountViewsTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user("owner", password="correct-long-password", is_staff=True)
        self.client.force_login(self.staff)

    @patch("accounts.views.call_helper")
    def test_create_and_disable_account(self, helper):
        response = self.client.post(reverse("create_account"), {
            "username": "alice", "password": "abc", "valid_days": "7", "max_connections": "2",
        })
        self.assertEqual(response.status_code, 302)
        account = VpnAccount.objects.get(username="alice")
        helper.assert_called_once()
        args, kwargs = helper.call_args
        self.assertEqual(args, ("create", "alice", "abc"))
        self.assertEqual(kwargs["max_connections"], 2)
        self.assertAlmostEqual(kwargs["expires_at"], account.expires_at.timestamp(), delta=1)
        self.assertEqual(account.max_connections, 2)
        self.assertNotEqual("abc", account.password_ciphertext)
        self.assertEqual(decrypt_password(account.password_ciphertext), "abc")
        self.client.post(reverse("disable_account", args=[account.pk]))
        account.refresh_from_db()
        self.assertFalse(account.enabled)
        self.assertEqual(AuditEvent.objects.filter(succeeded=True).count(), 2)

    @patch("accounts.views.call_helper")
    def test_bad_username_does_not_reach_helper(self, helper):
        self.client.post(reverse("create_account"), {
            "username": "bad/name", "password": "abc", "valid_days": "1", "max_connections": "1",
        })
        helper.assert_not_called()
        self.assertFalse(VpnAccount.objects.exists())

    @patch("accounts.views.call_helper")
    def test_short_password_can_be_reset(self, helper):
        account = VpnAccount.objects.create(username="alice", created_by=self.staff)
        response = self.client.post(reverse("reset_password", args=[account.pk]), {"password": "a"})
        self.assertEqual(response.status_code, 302)
        helper.assert_called_once_with("password", "alice", "a")

    @patch("accounts.views.call_helper")
    def test_edit_account_updates_limits_and_password(self, helper):
        old_expiry = timezone.now() + timedelta(days=2)
        account = VpnAccount.objects.create(
            username="alice", created_by=self.staff, expires_at=old_expiry,
            max_connections=2,
        )
        response = self.client.post(reverse("edit_account", args=[account.pk]), {
            "password": "a", "valid_days": "10", "max_connections": "4",
        })
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertEqual(account.max_connections, 4)
        self.assertGreater(account.expires_at, old_expiry)
        self.assertEqual(decrypt_password(account.password_ciphertext), "a")
        args, kwargs = helper.call_args
        self.assertEqual(args, ("update", "alice", "a"))
        self.assertEqual(kwargs["max_connections"], 4)
        self.assertEqual(kwargs["enabled"], True)
        self.assertAlmostEqual(kwargs["expires_at"], account.expires_at.timestamp(), delta=1)
        self.assertTrue(AuditEvent.objects.filter(action="update", succeeded=True).exists())

    @patch("accounts.views.call_helper")
    def test_edit_account_keeps_expiry_when_valid_days_blank(self, helper):
        old_expiry = timezone.now() + timedelta(days=2)
        account = VpnAccount.objects.create(
            username="alice", created_by=self.staff, expires_at=old_expiry,
            max_connections=2,
        )
        self.client.post(reverse("edit_account", args=[account.pk]), {
            "password": "", "valid_days": "", "max_connections": "3",
        })
        account.refresh_from_db()
        self.assertEqual(account.expires_at, old_expiry)
        self.assertEqual(account.max_connections, 3)
        self.assertIsNone(helper.call_args.kwargs["expires_at"])

    @patch("accounts.views.call_helper")
    def test_invalid_edit_does_not_reach_helper(self, helper):
        account = VpnAccount.objects.create(username="alice", created_by=self.staff, max_connections=2)
        self.client.post(reverse("edit_account", args=[account.pk]), {
            "password": "bad\npassword", "valid_days": "-2", "max_connections": "0",
        })
        helper.assert_not_called()
        account.refresh_from_db()
        self.assertEqual(account.max_connections, 2)

    @patch("accounts.views.call_helper")
    def test_non_staff_cannot_create_account(self, helper):
        user = get_user_model().objects.create_user("viewer", password="correct-long-password")
        self.client.force_login(user)
        response = self.client.post(reverse("create_account"), {"username": "vpn_alice", "password": "strong-password-123"})
        self.assertEqual(response.status_code, 302)
        helper.assert_not_called()

    def test_password_reveal_requires_staff_and_is_not_cached(self):
        account = VpnAccount.objects.create(username="alice", created_by=self.staff)
        response = self.client.get(reverse("account_password", args=[account.pk]))
        self.assertIsNone(response.json()["password"])
        self.assertIn("no-store", response["Cache-Control"])
        from .secrets import encrypt_password
        account.password_ciphertext = encrypt_password("secret-test")
        account.save(update_fields=["password_ciphertext"])
        self.assertEqual(self.client.get(reverse("account_password", args=[account.pk])).json()["password"], "secret-test")
        self.client.logout()
        self.assertEqual(self.client.get(reverse("account_password", args=[account.pk])).status_code, 302)

    def test_admin_can_change_own_password_and_keep_session(self):
        response = self.client.post(reverse("change_admin_password"), {
            "old_password": "correct-long-password",
            "new_password1": "new-admin-password-long",
            "new_password2": "new-admin-password-long",
        })
        self.assertEqual(response.status_code, 302)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password("new-admin-password-long"))
        self.assertEqual(self.client.get(reverse("panel_settings")).status_code, 200)

    @patch("accounts.views.call_helper", return_value='{"old": 5656, "new": 6677}')
    def test_stage_port_validates_input(self, helper):
        self.client.post(reverse("stage_ssh_port"), {"port": "0"})
        helper.assert_not_called()
        self.client.post(reverse("stage_ssh_port"), {"port": "6677"})
        helper.assert_called_once_with("port-stage", port=6677)

    @patch("accounts.views.call_helper")
    def test_dashboard_and_api_show_online_connections_and_transfer(self, helper):
        account = VpnAccount.objects.create(username="alice", created_by=self.staff)
        helper.return_value = '{"alice": {"connections": 2, "ips": ["198.51.100.25", "2001:db8::1"], "upload_bytes": 2048, "download_bytes": 3072}}'
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "2 online")
        self.assertContains(page, 'id="sessions-dialog"')
        self.assertNotContains(page, "198.51.100.25")
        self.assertContains(page, "5.0 KiB")
        response = self.client.get(reverse("account_usage"))
        self.assertEqual(response.json()["accounts"]["alice"]["upload"], "2.0 KiB")
        self.assertEqual(response.json()["accounts"]["alice"]["ips"], ["198.51.100.25", "2001:db8::1"])
        self.assertIn("no-store", response["Cache-Control"])
        self.client.logout()
        self.assertEqual(self.client.get(reverse("account_usage")).status_code, 302)

    def test_usage_units(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1024), "1.0 KiB")

    @patch("accounts.views.get_system_metrics")
    @patch("accounts.views.get_account_usage", return_value={})
    def test_audit_log_is_in_menu_and_not_dashboard(self, usage, metrics):
        metrics.return_value = {}
        AuditEvent.objects.create(actor=self.staff, username="alice", action="create", succeeded=True)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, 'href="/audit-log/"')
        self.assertNotContains(dashboard, "Recent activity")
        audit = self.client.get(reverse("audit_log"))
        self.assertContains(audit, "alice")
        self.assertContains(audit, 'aria-current="page"')
        self.assertEqual(audit.context["page"].paginator.count, 1)

        self.client.logout()
        self.assertEqual(self.client.get(reverse("audit_log")).status_code, 302)
        viewer = get_user_model().objects.create_user("viewer", password="correct-long-password")
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("audit_log")).status_code, 302)

    def test_audit_log_paginates(self):
        AuditEvent.objects.bulk_create([
            AuditEvent(actor=self.staff, username=f"user{i}", action="create", succeeded=True)
            for i in range(26)
        ])
        first = self.client.get(reverse("audit_log"))
        second = self.client.get(reverse("audit_log") + "?page=2")
        self.assertEqual(len(first.context["page"]), 25)
        self.assertEqual(len(second.context["page"]), 1)
        self.assertContains(first, 'href="?page=2"')

    @patch("accounts.views.list_backups", return_value=[])
    def test_backup_settings_requires_staff(self, listing):
        self.assertContains(self.client.get(reverse("backup_settings")), "Backup and restore")
        self.client.logout()
        self.assertEqual(self.client.get(reverse("backup_settings")).status_code, 302)
        self.assertEqual(self.client.post(reverse("create_backup"), {"passphrase": "A" * 16}).status_code, 302)
        listing.assert_called_once()

    @patch("accounts.views.call_backup", return_value={"name": "sshpanel-20261001T000000Z-12345678.svpb"})
    def test_create_backup_needs_matching_passphrase(self, helper):
        self.client.post(reverse("create_backup"), {"passphrase": "A" * 16, "passphrase_confirm": "B" * 16})
        helper.assert_not_called()
        self.client.post(reverse("create_backup"), {"passphrase": "A" * 16, "passphrase_confirm": "A" * 16})
        helper.assert_called_once_with("create", passphrase="A" * 16)

    def test_backup_download_requires_staff_and_uses_attachment(self):
        name = "sshpanel-20261001T000000Z-12345678.svpb"
        with TemporaryDirectory() as directory, patch("accounts.views.BACKUP_DIR", Path(directory)):
            (Path(directory) / name).write_bytes(b"encrypted test data")
            response = self.client.get(reverse("download_backup", args=[name]))
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(b"".join(response.streaming_content), b"encrypted test data")
            self.client.logout()
            self.assertEqual(self.client.get(reverse("download_backup", args=[name])).status_code, 302)

    @patch("accounts.views.call_backup", return_value={"accounts": 2})
    def test_restore_requires_confirmation_and_calls_service(self, helper):
        with TemporaryDirectory() as directory, patch("accounts.backups.UPLOAD_DIR", Path(directory)), patch("accounts.views.UPLOAD_DIR", Path(directory)):
            data = {"backup_file": SimpleUploadedFile("backup.svpb", b"encrypted"), "passphrase": "A" * 16}
            self.client.post(reverse("restore_backup"), data)
            helper.assert_not_called()
            data["backup_file"] = SimpleUploadedFile("backup.svpb", b"encrypted")
            data["confirm_replace"] = "yes"
            response = self.client.post(reverse("restore_backup"), data)
            self.assertContains(response, "2 VPN accounts")
            self.assertEqual(helper.call_args.args, ("restore",))

    @patch("accounts.views.call_helper")
    def test_cross_site_post_is_rejected(self, helper):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(reverse("create_account"), {"username": "vpn_alice", "password": "strong-password-123"})
        self.assertEqual(response.status_code, 403)
        helper.assert_not_called()

    @patch("accounts.views.get_system_metrics")
    def test_dashboard_and_metrics_are_staff_only(self, metrics):
        metrics.return_value = {
            "cpu": {"percent": 20, "detail": "2 CPU cores"},
            "memory": {"percent": 40, "detail": "1.0 GiB of 2.0 GiB"},
            "disk": {"percent": 60, "detail": "6.0 GiB of 10.0 GiB"},
        }
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Server resource usage")
        self.assertContains(response, "20%")
        self.assertContains(response, 'id="create-account-dialog"')
        self.assertContains(response, 'id="edit-account-dialog"')
        self.assertNotContains(response, 'class="panel create-panel"')
        response = self.client.get(reverse("system_metrics"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["memory"]["percent"], 40)

        self.client.logout()
        self.assertEqual(self.client.get(reverse("system_metrics")).status_code, 302)
        viewer = get_user_model().objects.create_user("viewer", password="correct-long-password")
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("system_metrics")).status_code, 302)


class SystemMetricsTests(TestCase):
    @patch("accounts.system_metrics.time.sleep")
    @patch("accounts.system_metrics._cpu_times", side_effect=[(100, 80), (200, 100)])
    def test_cpu_usage_from_two_samples(self, samples, pause):
        self.assertEqual(_cpu_metric()["percent"], 80)
        pause.assert_called_once()

    @patch("accounts.system_metrics.time.sleep")
    @patch("accounts.system_metrics._cpu_times", side_effect=[(100, 100), (200, 200)])
    def test_idle_cpu_is_shown_as_a_number(self, samples, pause):
        metric = _cpu_metric()
        self.assertEqual(metric["percent"], 0.0)
        self.assertIsInstance(metric["percent"], float)

    @patch("accounts.system_metrics._cpu_metric", side_effect=OSError("proc missing"))
    @patch("accounts.system_metrics._memory_metric", return_value={"percent": 40, "detail": "Memory"})
    @patch("accounts.system_metrics._disk_metric", return_value={"percent": 60, "detail": "Disk"})
    def test_unavailable_metric_does_not_break_dashboard(self, disk, memory, cpu):
        metrics = get_system_metrics()
        self.assertIsNone(metrics["cpu"]["percent"])
        self.assertEqual(metrics["memory"]["percent"], 40)


class InitialAdminTests(TestCase):
    def test_creates_admin_from_stdin_without_storing_plaintext(self):
        with patch("sys.stdin", StringIO("emad\nlong-secret-password-123\n")):
            call_command("initial_admin", stdout=StringIO())
        admin = get_user_model().objects.get(username="emad")
        self.assertTrue(admin.is_staff and admin.is_superuser)
        self.assertTrue(admin.check_password("long-secret-password-123"))
        self.assertNotIn("long-secret-password-123", admin.password)

    def test_rejects_short_admin_password(self):
        with patch("sys.stdin", StringIO("emad\nshort\n")):
            with self.assertRaises(CommandError):
                call_command("initial_admin", stdout=StringIO())
        self.assertFalse(get_user_model().objects.filter(username="emad").exists())
