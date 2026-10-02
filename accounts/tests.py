from unittest.mock import patch
from io import StringIO
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import get_script_prefix, reverse, set_script_prefix
from django.utils import timezone

from .models import AuditEvent, VpnAccount
from .secrets import decrypt_password
from .services import ProvisionError
from .system_metrics import _cpu_metric, get_system_metrics
from .views import format_bytes


class AccountViewsTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user("owner", password="correct-long-password", is_staff=True)
        self.client.force_login(self.staff)

    def bulk_post(self, data):
        session = self.client.session
        session["bulk_create_token"] = "test-batch-token"
        session.save()
        return self.client.post(reverse("bulk_create_accounts"), {**data, "batch_token": "test-batch-token"})

    @patch("accounts.views.call_helper")
    def test_dashboard_lists_new_single_and_bulk_users_last(self, helper):
        VpnAccount.objects.create(username="z_existing", created_by=self.staff)
        self.client.post(reverse("create_account"), {
            "username": "a_single", "password": "abc", "valid_days": "7", "max_connections": "1",
        })
        response = self.bulk_post({
            "count": "2", "prefix": "b_", "start_number": "1000", "password": "shared",
            "password_mode": "digits", "password_length": "8", "max_connections": "1", "valid_days": "7",
        })
        self.assertEqual(response.status_code, 200)
        page = self.client.get(reverse("dashboard"))
        self.assertEqual([account.pk for account in page.context["accounts"]],
                         list(VpnAccount.objects.order_by("created_at", "pk").values_list("pk", flat=True)))
        self.assertEqual(page.context["accounts"][0].username, "z_existing")
        self.assertEqual(page.context["accounts"][1].username, "a_single")

    def test_dashboard_searches_username_and_referral_fields(self):
        owner = VpnAccount.objects.create(username="Owner", created_by=self.staff, referral_code="Spring-Campaign")
        VpnAccount.objects.create(username="member", created_by=self.staff, referred_by=owner)
        VpnAccount.objects.create(username="bulk_user", created_by=self.staff, referral_note="مشتری ویژه")

        def matches(query):
            page = self.client.get(reverse("dashboard"), {"q": query})
            self.assertEqual(page.context["account_total"], 3)
            return [account.username for account in page.context["accounts"] if account.matches_search]

        self.assertEqual(matches("BULK_USER"), ["bulk_user"])
        self.assertEqual(matches("مشتری"), ["bulk_user"])
        self.assertEqual(matches("spring-campaign"), ["Owner", "member"])
        self.assertEqual(matches("no-match"), [])

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
    def test_referral_and_first_connection_validity(self, helper):
        referrer_code = "My Personal Code ✨ " + "x" * 5000
        referrer = VpnAccount.objects.create(username="referrer", created_by=self.staff, referral_code=referrer_code)
        own_code = "New user code / " + "y" * 5000
        response = self.client.post(reverse("create_account"), {
            "username": "newuser", "password": "abc", "valid_days": "30", "max_connections": "2",
            "referral_code": own_code, "referred_by_code": referrer_code, "start_on_first_connection": "on",
        })
        self.assertEqual(response.status_code, 302)
        account = VpnAccount.objects.get(username="newuser")
        self.assertIsNone(account.expires_at)
        self.assertEqual(account.valid_days, 30)
        self.assertEqual(account.referred_by, referrer)
        self.assertEqual(account.referral_code, own_code)
        self.assertEqual(len(account.referral_code_hash), 64)
        helper.assert_called_once_with("create", "newuser", "abc", valid_days=30, max_connections=2)
        helper.reset_mock()
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "1 referrals")
        self.assertContains(page, "30 days after first connection")

    @patch("accounts.views.call_helper")
    def test_custom_referral_code_is_unique_without_length_limit(self, helper):
        code = "Personal Name " + "z" * 5000
        VpnAccount.objects.create(username="owner1", created_by=self.staff, referral_code=code)
        response = self.client.post(reverse("create_account"), {
            "username": "owner2", "password": "abc", "valid_days": "7", "max_connections": "1",
            "referral_code": code.upper(),
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(VpnAccount.objects.filter(username="owner2").exists())
        helper.assert_not_called()

    @patch("accounts.views.call_helper")
    def test_edit_sets_and_clears_custom_referral_code(self, helper):
        account = VpnAccount.objects.create(username="alice", created_by=self.staff)
        code = "My own code " + "a" * 5000
        self.client.post(reverse("edit_account", args=[account.pk]), {"referral_code": code})
        account.refresh_from_db()
        self.assertEqual(account.referral_code, code)
        self.assertEqual(len(account.referral_code_hash), 64)
        self.client.post(reverse("edit_account", args=[account.pk]), {"referral_code": ""})
        account.refresh_from_db()
        self.assertEqual(account.referral_code, "")
        self.assertIsNone(account.referral_code_hash)
        helper.assert_not_called()

    @patch("accounts.views.call_helper")
    def test_first_connection_activation_syncs_from_ssh_policy(self, helper):
        account = VpnAccount.objects.create(username="alice", created_by=self.staff, valid_days=7)
        started = int(timezone.now().timestamp())
        helper.return_value = '{"alice": {"activated_at": %d, "expires_at": %d}}' % (started, started + 7 * 86400)
        from .views import sync_account_activation
        sync_account_activation()
        account.refresh_from_db()
        self.assertEqual(int(account.activated_at.timestamp()), started)
        self.assertEqual(int(account.expires_at.timestamp()), started + 7 * 86400)

    @patch("accounts.views.call_helper")
    def test_bulk_creation_generates_distinct_users_and_passwords(self, helper):
        response = self.bulk_post({
            "count": "3", "prefix": "vpn_", "start_number": "1000", "password": "",
            "password_mode": "digits", "password_length": "8", "max_connections": "2",
            "traffic_gb": "2.5", "valid_days": "30", "start_on_first_connection": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(VpnAccount.objects.count(), 3)
        self.assertEqual(helper.call_count, 3)
        passwords = []
        for account in VpnAccount.objects.all():
            self.assertTrue(account.username.startswith("vpn_"))
            self.assertGreaterEqual(int(account.username[4:]), 1000)
            self.assertEqual(account.valid_days, 30)
            self.assertIsNone(account.expires_at)
            self.assertEqual(account.traffic_limit_bytes, int(2.5 * 1024 ** 3))
            self.assertEqual(account.referral_code, "")
            self.assertIsNone(account.referral_code_hash)
            self.assertEqual(account.referral_note, "")
            passwords.append(decrypt_password(account.password_ciphertext))
        self.assertEqual(len(set(passwords)), 3)
        self.assertTrue(all(len(password) == 8 and password.isdigit() for password in passwords))
        self.assertContains(response, "3 of 3 users created")

    @patch("accounts.views.call_helper")
    def test_bulk_saves_shared_free_text_referral_without_account_links(self, helper):
        referral_note = "معرف دلخواه " + "x" * 5000 + "\nبرچسب دوم ✨"
        response = self.bulk_post({
            "count": "3", "prefix": "vpn_", "start_number": "1000", "password": "shared",
            "password_mode": "digits", "password_length": "8", "max_connections": "1",
            "valid_days": "7", "referral_note": referral_note,
        })
        self.assertEqual(response.status_code, 200)
        accounts = list(VpnAccount.objects.order_by("pk"))
        self.assertEqual([account.referral_note for account in accounts], [referral_note] * 3)
        self.assertTrue(all(account.referred_by_id is None and account.referral_code == "" for account in accounts))
        self.assertEqual(helper.call_count, 3)
        self.assertContains(response, "معرف دلخواه")

    @patch("accounts.views.call_helper")
    def test_bulk_accepts_one_referral_text_for_multiple_users(self, helper):
        response = self.bulk_post({
            "count": "2", "prefix": "vpn_", "start_number": "1000", "password": "shared",
            "password_mode": "digits", "password_length": "8", "max_connections": "1",
            "valid_days": "7", "referral_note": "one text for both",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(VpnAccount.objects.values_list("referral_note", flat=True)),
                         ["one text for both"] * 2)
        self.assertEqual(helper.call_count, 2)

    @patch("accounts.views.call_helper")
    def test_bulk_creation_reports_partial_failure_and_keeps_credentials(self, helper):
        helper.side_effect = ["", ProvisionError("Linux account unavailable")]
        response = self.bulk_post({
            "count": "2", "prefix": "user", "start_number": "1000", "password": "shared",
            "password_mode": "mixed", "password_length": "8", "max_connections": "1",
            "valid_days": "7",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(VpnAccount.objects.count(), 1)
        self.assertContains(response, "1 of 2 users created")
        self.assertContains(response, "Linux account unavailable")
        self.assertContains(response, "shared")

    @patch("accounts.views.call_helper")
    def test_bulk_rejects_invalid_prefix_before_provisioning(self, helper):
        response = self.bulk_post({
            "count": "2", "prefix": "Bad!", "start_number": "1", "password": "",
            "password_mode": "digits", "password_length": "8", "max_connections": "1", "valid_days": "7",
        })
        self.assertEqual(response.status_code, 302)
        helper.assert_not_called()

    @patch("accounts.views.call_helper")
    def test_bulk_post_cannot_be_replayed(self, helper):
        data = {"count": "1", "prefix": "user", "start_number": "1000", "password": "fixed",
                "password_mode": "digits", "password_length": "8", "max_connections": "1", "valid_days": "7"}
        self.bulk_post(data)
        self.assertEqual(VpnAccount.objects.count(), 1)
        again = self.client.post(reverse("bulk_create_accounts"), {**data, "batch_token": "test-batch-token"})
        self.assertEqual(again.status_code, 302)
        self.assertEqual(VpnAccount.objects.count(), 1)
        self.assertEqual(helper.call_count, 1)

    @patch("accounts.views.call_helper")
    def test_bulk_creation_requires_staff_and_post(self, helper):
        self.assertEqual(self.client.get(reverse("bulk_create_accounts")).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("bulk_create_accounts"), {"count": "1"}).status_code, 302)
        helper.assert_not_called()

    @patch("accounts.views.call_helper")
    def test_bulk_actions_apply_only_to_selected_users(self, helper):
        first = VpnAccount.objects.create(username="first", created_by=self.staff, max_connections=2)
        second = VpnAccount.objects.create(username="second", created_by=self.staff, max_connections=2)
        untouched = VpnAccount.objects.create(username="untouched", created_by=self.staff)
        selected = [str(first.pk), str(second.pk)]
        action_url = reverse("bulk_account_action")

        self.client.post(action_url, {"action": "disable", "account_ids": selected})
        first.refresh_from_db()
        second.refresh_from_db()
        untouched.refresh_from_db()
        self.assertFalse(first.enabled)
        self.assertFalse(second.enabled)
        self.assertTrue(untouched.enabled)
        self.assertEqual(helper.call_count, 2)

        self.client.post(action_url, {"action": "enable", "account_ids": selected})
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.enabled and second.enabled)
        self.client.post(action_url, {"action": "usage-reset", "account_ids": selected})
        self.assertEqual(helper.call_args_list[-2].args, ("usage-reset", "first"))
        self.assertEqual(helper.call_args_list[-1].args, ("usage-reset", "second"))

        with patch("accounts.views.sync_account_activation"):
            self.client.post(action_url, {"action": "extend", "days": "30", "account_ids": selected})
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertGreater(first.expires_at, timezone.now() + timedelta(days=29))
        self.assertGreater(second.expires_at, timezone.now() + timedelta(days=29))
        self.client.post(action_url, {"action": "delete", "account_ids": selected})
        self.assertEqual(list(VpnAccount.objects.values_list("username", flat=True)), ["untouched"])

    @patch("accounts.views.call_helper")
    def test_bulk_action_rejects_invalid_selection_before_mutation(self, helper):
        account = VpnAccount.objects.create(username="first", created_by=self.staff)
        response = self.client.post(reverse("bulk_account_action"), {
            "action": "disable", "account_ids": [str(account.pk), "999999"],
        })
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertTrue(account.enabled)
        helper.assert_not_called()

    @patch("accounts.views.call_helper")
    def test_bulk_action_requires_staff_post_and_valid_extension(self, helper):
        account = VpnAccount.objects.create(username="first", created_by=self.staff)
        url = reverse("bulk_account_action")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {"action": "extend", "days": "13", "account_ids": [str(account.pk)]})
        account.refresh_from_db()
        self.assertIsNone(account.expires_at)
        helper.assert_not_called()
        self.client.logout()
        self.assertEqual(self.client.post(url, {"action": "delete", "account_ids": [str(account.pk)]}).status_code, 302)
        self.assertTrue(VpnAccount.objects.filter(pk=account.pk).exists())

    @patch("accounts.views.call_helper")
    def test_bulk_action_reports_partial_failure(self, helper):
        first = VpnAccount.objects.create(username="first", created_by=self.staff)
        second = VpnAccount.objects.create(username="second", created_by=self.staff)
        helper.side_effect = ["", ProvisionError("unavailable")]
        response = self.client.post(reverse("bulk_account_action"), {
            "action": "disable", "account_ids": [str(first.pk), str(second.pk)],
        })
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.enabled)
        self.assertTrue(second.enabled)
        self.assertIn("1 failed", " ".join(str(message) for message in response.wsgi_request._messages))

    @patch("accounts.views.call_helper")
    def test_traffic_limit_can_be_changed_and_cleared(self, helper):
        account = VpnAccount.objects.create(username="alice", created_by=self.staff)
        self.client.post(reverse("edit_account", args=[account.pk]), {"traffic_gb": "1.5"})
        account.refresh_from_db()
        self.assertEqual(account.traffic_limit_bytes, int(1.5 * 1024 ** 3))
        self.assertEqual(helper.call_args.kwargs["traffic_limit_bytes"], int(1.5 * 1024 ** 3))
        self.client.post(reverse("edit_account", args=[account.pk]), {"traffic_gb": "0"})
        account.refresh_from_db()
        self.assertIsNone(account.traffic_limit_bytes)
        self.assertEqual(helper.call_args.kwargs["traffic_limit_bytes"], 0)

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
    def test_quick_extend_adds_to_future_expiry_or_starts_from_now(self, helper):
        future = timezone.now() + timedelta(days=5)
        account = VpnAccount.objects.create(username="alice", created_by=self.staff, expires_at=future, max_connections=2)
        self.client.post(reverse("extend_account", args=[account.pk]), {"days": "30"})
        account.refresh_from_db()
        self.assertEqual(account.expires_at, future + timedelta(days=30))
        self.assertEqual(helper.call_args.args, ("update", "alice"))
        self.assertEqual(helper.call_args.kwargs["max_connections"], 2)
        self.assertAlmostEqual(helper.call_args.kwargs["expires_at"], account.expires_at.timestamp(), delta=1)

        account.expires_at = timezone.now() - timedelta(days=1)
        account.save(update_fields=["expires_at"])
        before = timezone.now()
        self.client.post(reverse("extend_account", args=[account.pk]), {"days": "60"})
        account.refresh_from_db()
        self.assertGreaterEqual(account.expires_at, before + timedelta(days=60))
        self.assertLessEqual(account.expires_at, timezone.now() + timedelta(days=60))

    @patch("accounts.views.call_helper")
    def test_quick_extend_rejects_other_days_and_preserves_expiry_on_failure(self, helper):
        future = timezone.now() + timedelta(days=5)
        account = VpnAccount.objects.create(username="alice", created_by=self.staff, expires_at=future)
        for days in ("0", "31", "abc", ""):
            self.client.post(reverse("extend_account", args=[account.pk]), {"days": days})
        helper.assert_not_called()
        helper.side_effect = ProvisionError("failed")
        self.client.post(reverse("extend_account", args=[account.pk]), {"days": "90"})
        account.refresh_from_db()
        self.assertEqual(account.expires_at, future)
        self.assertTrue(AuditEvent.objects.filter(username="alice", action="extend", succeeded=False).exists())

    @patch("accounts.views.call_helper")
    def test_traffic_reset_uses_privileged_helper(self, helper):
        account = VpnAccount.objects.create(username="alice", created_by=self.staff)
        self.assertEqual(self.client.post(reverse("reset_account_traffic", args=[account.pk])).status_code, 302)
        helper.assert_called_once_with("usage-reset", "alice")
        self.assertTrue(AuditEvent.objects.filter(username="alice", action="usage-reset", succeeded=True).exists())

    @patch("accounts.views.call_helper")
    def test_new_actions_require_staff_and_post(self, helper):
        account = VpnAccount.objects.create(username="alice", created_by=self.staff)
        for route in ("extend_account", "reset_account_traffic"):
            self.assertEqual(self.client.get(reverse(route, args=[account.pk])).status_code, 405)
        self.client.logout()
        for route in ("extend_account", "reset_account_traffic"):
            self.assertEqual(self.client.post(reverse(route, args=[account.pk])).status_code, 302)
        helper.assert_not_called()

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

    @patch("accounts.views.call_helper", return_value='{"url": "http://localhost:18080/private-42/"}')
    def test_stage_web_address_shows_new_url_without_changing_current_request(self, helper):
        response = self.client.post(reverse("stage_web_address"), {"path": "/private-42", "port": "18080"})
        self.assertContains(response, "http://localhost:18080/private-42/settings/")
        helper.assert_called_once_with("web-stage", web_path="/private-42", web_port=18080)

    @patch("accounts.views.call_helper", return_value='{"url": "http://localhost:18080/private-42/"}')
    def test_web_change_requires_staff_and_post(self, helper):
        self.assertEqual(self.client.get(reverse("stage_web_address")).status_code, 405)
        self.assertEqual(self.client.get(reverse("confirm_web_address")).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("stage_web_address"), {"path": "/private-42", "port": "18080"}).status_code, 302)
        self.assertEqual(self.client.post(reverse("confirm_web_address")).status_code, 302)
        helper.assert_not_called()

    @override_settings(TLS_ENABLED=True, HTTPS_PORT="8443", ALLOWED_HOSTS=["example.com"])
    @patch("accounts.views.call_helper", return_value="null")
    def test_settings_shows_https_address_and_port_control(self, helper):
        page = self.client.get(reverse("panel_settings"), HTTP_HOST="example.com:8443", secure=True)
        self.assertContains(page, "https://example.com:8443/")
        self.assertContains(page, "HTTPS port")
        self.assertContains(page, 'value="8443"')

    @override_settings(FORCE_SCRIPT_NAME="/private-42", STATIC_URL="/private-42/static/")
    def test_prefixed_panel_links_and_login(self):
        original_prefix = get_script_prefix()
        set_script_prefix("/private-42")  # Django's test client skips WSGIHandler.__call__.
        try:
            page = self.client.get("/", SCRIPT_NAME="/private-42")
        finally:
            set_script_prefix(original_prefix)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '/private-42/static/panel.css')
        self.assertContains(page, 'href="/private-42/settings/"')

    @patch("accounts.views.call_helper")
    def test_dashboard_and_api_show_online_connections_and_transfer(self, helper):
        account = VpnAccount.objects.create(username="alice", created_by=self.staff)
        helper.return_value = '{"alice": {"connections": 2, "ips": ["198.51.100.25", "2001:db8::1"], "upload_bytes": 2048, "download_bytes": 3072}}'
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, 'data-online="1"')
        self.assertContains(page, "Online now")
        self.assertContains(page, "Inactive users")
        self.assertContains(page, 'id="server-resources-root"')
        self.assertContains(page, 'id="sessions-dialog"')
        self.assertNotContains(page, "198.51.100.25")
        self.assertContains(page, "5.0 KiB")
        response = self.client.get(reverse("account_usage"))
        self.assertEqual(response.json()["accounts"]["alice"]["upload"], "2.0 KiB")
        self.assertEqual(response.json()["accounts"]["alice"]["ips"], ["198.51.100.25", "2001:db8::1"])
        self.assertEqual(response.json()["summary"], {"total": 1, "online": 1, "inactive": 0})
        self.assertIn("no-store", response["Cache-Control"])
        self.client.logout()
        self.assertEqual(self.client.get(reverse("account_usage")).status_code, 302)

    def test_usage_units(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1024), "1.0 KiB")

    @patch("accounts.views.get_system_metrics", return_value={})
    @patch("accounts.views.get_account_usage", return_value={})
    def test_dashboard_shows_remaining_days_and_keeps_exact_expiry_for_copy(self, usage, metrics):
        future = timezone.now() + timedelta(days=2, hours=3)
        VpnAccount.objects.create(username="future", created_by=self.staff, expires_at=future)
        VpnAccount.objects.create(username="expired", created_by=self.staff,
                                  expires_at=timezone.now() - timedelta(minutes=1))
        VpnAccount.objects.create(username="disabled", created_by=self.staff, enabled=False)
        VpnAccount.objects.create(username="unlimited", created_by=self.staff)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "Days left")
        self.assertContains(page, 'id="bulk-create-dialog"')
        self.assertContains(page, "3 days")
        self.assertContains(page, "0 days")
        self.assertContains(page, "Unlimited")
        self.assertContains(page, 'data-inactive="2"')
        self.assertContains(page, f'data-expires-at="{future.isoformat()}"')
        self.assertNotContains(page, '<td class="nowrap" data-expiry-cell>')

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
        page = self.client.get(reverse("backup_settings"))
        self.assertContains(page, "Backup and restore")
        self.assertContains(page, "Backups contain sensitive account data")
        self.assertNotContains(page, "Backup passphrase")
        self.assertNotContains(page, 'name="passphrase"')
        self.client.logout()
        self.assertEqual(self.client.get(reverse("backup_settings")).status_code, 302)
        self.assertEqual(self.client.post(reverse("create_backup")).status_code, 302)
        listing.assert_called_once()

    @patch("accounts.views.call_backup", return_value={"name": "sshpanel-20261001T000000Z-12345678.tar.gz"})
    def test_create_backup_needs_no_passphrase(self, helper):
        self.assertEqual(self.client.post(reverse("create_backup")).status_code, 302)
        helper.assert_called_once_with("create")

    def test_backup_download_requires_staff_and_uses_attachment(self):
        name = "sshpanel-20261001T000000Z-12345678.tar.gz"
        with TemporaryDirectory() as directory, patch("accounts.views.BACKUP_DIR", Path(directory)):
            (Path(directory) / name).write_bytes(b"backup test data")
            response = self.client.get(reverse("download_backup", args=[name]))
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(b"".join(response.streaming_content), b"backup test data")
            self.client.logout()
            self.assertEqual(self.client.get(reverse("download_backup", args=[name])).status_code, 302)

    @patch("accounts.views.call_backup", return_value={"accounts": 2})
    def test_restore_requires_confirmation_and_calls_service(self, helper):
        with TemporaryDirectory() as directory, patch("accounts.backups.UPLOAD_DIR", Path(directory)), patch("accounts.views.UPLOAD_DIR", Path(directory)):
            data = {"backup_file": SimpleUploadedFile("backup.tar.gz", b"archive")}
            self.client.post(reverse("restore_backup"), data)
            helper.assert_not_called()
            data["backup_file"] = SimpleUploadedFile("backup.tar.gz", b"archive")
            data["confirm_replace"] = "yes"
            response = self.client.post(reverse("restore_backup"), data)
            self.assertContains(response, "2 VPN accounts")
            self.assertEqual(helper.call_args.args, ("restore",))
            self.assertEqual(set(helper.call_args.kwargs), {"name"})

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
        self.assertContains(response, 'data-language-toggle')
        self.assertContains(response, 'i18n.js')
        self.assertContains(response, 'data-ssh-port="22"')
        self.assertNotContains(response, 'class="port-card"')
        self.assertNotContains(response, 'class="panel create-panel"')
        self.client.cookies["django_language"] = "fa"
        self.assertEqual(self.client.get(reverse("dashboard"))["Content-Language"], "fa")
        del self.client.cookies["django_language"]
        response = self.client.get(reverse("system_metrics"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["memory"]["percent"], 40)
        self.assertIn("no-store", response["Cache-Control"])

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
