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

from .models import AuditEvent, PanelAppearance, VpnAccount
from .secrets import decrypt_password, encrypt_password
from .services import ProvisionError, get_ssh_port
from .system_metrics import _cpu_metric, get_system_metrics
from .views import format_bytes


class AccountViewsTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user("owner", password="correct-long-password", is_staff=True)
        self.client.force_login(self.staff)

    def test_login_url_redirects_existing_session_and_shows_form_after_logout(self):
        self.assertRedirects(self.client.get(reverse("login")), reverse("dashboard"))
        self.client.logout()
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)

    def test_panel_language_persists_across_devices_and_requires_staff_and_csrf(self):
        self.assertContains(Client().get(reverse("login")), '<html lang="fa" dir="rtl"')
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post(reverse("set_panel_language"), {"language": "en"}).status_code, 403)
        csrf_client.get(reverse("panel_settings"))
        token = csrf_client.cookies["csrftoken"].value
        response = csrf_client.post(reverse("set_panel_language"), {"language": "en"}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PanelAppearance.objects.get(pk=1).language, "en")
        self.assertContains(Client().get(reverse("login")), '<html lang="en" dir="ltr"')
        another_device = Client()
        another_device.force_login(self.staff)
        self.assertContains(another_device.get(reverse("panel_settings")), '<html lang="en" dir="ltr"')
        self.assertEqual(csrf_client.post(reverse("set_panel_language"), {"language": "fa"}, HTTP_X_CSRFTOKEN=token).status_code, 200)
        another_device.cookies["django_language"] = "en"
        self.assertContains(another_device.get(reverse("panel_settings")), '<html lang="fa" dir="rtl"')
        self.assertContains(Client().get(reverse("login")), '<html lang="fa" dir="rtl"')
        self.assertEqual(self.client.post(reverse("set_panel_language"), {"language": "other"}).status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("set_panel_language"), {"language": "fa"}).status_code, 302)

    @patch("accounts.services.call_helper", return_value='{"port": 6677}')
    def test_current_ssh_port_comes_from_privileged_helper(self, helper):
        self.assertEqual(get_ssh_port(), 6677)
        self.assertContains(self.client.get(reverse("dashboard")), 'data-ssh-port="6677"')
        self.assertEqual(self.client.get(reverse("settings_ssh")).context["ssh_port"], 6677)
        helper.assert_any_call("port-current")

    @patch("accounts.views.call_backup", return_value={"enabled": False, "time": "03:00"})
    @patch("accounts.views.get_ssh_port", return_value=5656)
    @patch("accounts.views.call_helper", return_value="null")
    def test_settings_open_as_separate_pages(self, helper, port, backup):
        overview = self.client.get(reverse("panel_settings"))
        self.assertContains(overview, 'class="settings-overview-grid"')
        self.assertContains(overview, f'href="{reverse("settings_ssh")}"')
        self.assertNotContains(overview, 'id="new-ssh-port"')
        self.assertNotContains(overview, 'id="panel-web-path"')
        self.assertContains(self.client.get(reverse("settings_ssh")), 'id="new-ssh-port"')
        self.assertNotContains(self.client.get(reverse("settings_ssh")), 'id="panel-web-path"')
        self.assertContains(self.client.get(reverse("settings_web")), 'id="panel-web-path"')
        self.assertNotContains(self.client.get(reverse("settings_web")), 'id="new-ssh-port"')
        self.assertContains(self.client.get(reverse("settings_admin")), 'class="settings-password-form"')
        self.assertContains(self.client.get(reverse("settings_telegram")), 'id="telegram-bot"')
        self.assertRedirects(
            self.client.post(reverse("stage_ssh_port"), {"port": "0"}),
            reverse("settings_ssh"), fetch_redirect_response=False,
        )

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

    @patch("accounts.views.get_system_metrics", return_value={})
    @patch("accounts.views.get_account_usage", return_value={})
    def test_user_order_can_move_to_edges_or_any_position_and_persists(self, usage, metrics):
        accounts = [VpnAccount.objects.create(username=name, created_by=self.staff)
                    for name in ("alpha", "bravo", "charlie", "delta")]

        def names():
            page = self.client.get(reverse("dashboard"))
            self.assertEqual(page.status_code, 200)
            return [account.username for account in page.context["accounts"]]

        def move(account, direction, position=None):
            data = {"move": direction}
            if position is not None:
                data["position"] = str(position)
            response = self.client.post(reverse("reorder_account", args=[account.pk]), data)
            self.assertEqual(response.status_code, 302)

        self.assertEqual(names(), ["alpha", "bravo", "charlie", "delta"])
        move(accounts[1], "bottom")
        self.assertEqual(names(), ["alpha", "charlie", "delta", "bravo"])
        move(accounts[3], "top")
        self.assertEqual(names(), ["delta", "alpha", "charlie", "bravo"])
        move(accounts[1], "up")
        self.assertEqual(names(), ["delta", "alpha", "bravo", "charlie"])
        move(accounts[0], "down")
        self.assertEqual(names(), ["delta", "bravo", "alpha", "charlie"])
        move(accounts[2], "position", 2)
        self.assertEqual(names(), ["delta", "charlie", "bravo", "alpha"])
        self.assertEqual(list(VpnAccount.objects.order_by("sort_order").values_list("sort_order", flat=True)),
                         [1, 2, 3, 4])
        VpnAccount.objects.create(username="echo", created_by=self.staff)
        self.assertEqual(names(), ["delta", "charlie", "bravo", "alpha", "echo"])
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, f'data-action="{reverse("reorder_account", args=[accounts[2].pk])}"')
        self.assertContains(page, 'data-list-position="2"')

    def test_reorder_rejects_invalid_positions_and_requires_staff_and_csrf(self):
        account = VpnAccount.objects.create(username="alpha", created_by=self.staff)
        VpnAccount.objects.create(username="bravo", created_by=self.staff)
        url = reverse("reorder_account", args=[account.pk])
        for data in ({"move": "unknown"}, {"move": "position", "position": "0"},
                     {"move": "position", "position": "3"}, {"move": "position", "position": "bad"}):
            self.assertEqual(self.client.post(url, data).status_code, 302)
            self.assertFalse(VpnAccount.objects.filter(sort_order__isnull=False).exists())
        self.assertEqual(self.client.post(url, {"move": "bottom", "q": "alpha", "status": "inactive"})["Location"],
                         reverse("dashboard") + "?q=alpha&status=inactive")
        self.assertEqual(Client().post(url, {"move": "top"}).status_code, 302)
        nonstaff = get_user_model().objects.create_user("viewer", password="x")
        other_client = Client()
        other_client.force_login(nonstaff)
        self.assertEqual(other_client.post(url, {"move": "top"}).status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post(url, {"move": "top"}).status_code, 403)

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

    @patch("accounts.views.get_system_metrics", return_value={})
    @patch("accounts.views.get_account_usage", return_value={
        "active": {"connections": 1, "total_bytes": 0},
        "disabled": {"connections": 0, "total_bytes": 0},
    })
    def test_summary_cards_and_rows_share_filter_status(self, usage, metrics):
        VpnAccount.objects.create(username="active", created_by=self.staff)
        VpnAccount.objects.create(username="disabled", created_by=self.staff, enabled=False)
        page = self.client.get(reverse("dashboard"))
        self.assertEqual((page.context["account_total"], page.context["account_online"],
                          page.context["account_inactive"]), (2, 1, 1))
        self.assertContains(page, 'data-summary-filter="online"')
        self.assertContains(page, 'data-summary-filter="inactive"')
        rows = {account.username: account for account in page.context["accounts"]}
        self.assertTrue(rows["active"].is_online)
        self.assertFalse(rows["active"].is_inactive)
        self.assertFalse(rows["disabled"].is_online)
        self.assertTrue(rows["disabled"].is_inactive)

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
    def test_zero_connection_limit_creates_unlimited_account(self, helper):
        response = self.client.post(reverse("create_account"), {
            "username": "unlimited", "password": "abc", "valid_days": "7", "max_connections": "0",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(VpnAccount.objects.get(username="unlimited").max_connections)
        self.assertEqual(helper.call_args.kwargs["max_connections"], 0)
        self.assertContains(self.client.get(reverse("dashboard")), "Unlimited")

    @patch("accounts.views.call_helper")
    def test_zero_connection_limit_works_for_bulk_creation(self, helper):
        response = self.bulk_post({
            "count": "1", "prefix": "bulk_", "start_number": "1000", "password": "abc",
            "password_mode": "digits", "password_length": "8", "max_connections": "0", "valid_days": "7",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unlimited")
        self.assertIsNone(VpnAccount.objects.get().max_connections)
        self.assertEqual(helper.call_args.kwargs["max_connections"], 0)

    @patch("accounts.views.call_helper")
    def test_single_referral_text_and_first_connection_validity(self, helper):
        referral = "معرف دلخواه ✨ " + "x" * 5000
        response = self.client.post(reverse("create_account"), {
            "username": "newuser", "password": "abc", "valid_days": "30", "max_connections": "2",
            "referral_note": referral, "start_on_first_connection": "on",
        })
        self.assertEqual(response.status_code, 302)
        account = VpnAccount.objects.get(username="newuser")
        self.assertIsNone(account.expires_at)
        self.assertEqual(account.valid_days, 30)
        self.assertIsNone(account.referred_by)
        self.assertEqual(account.referral_code, "")
        self.assertEqual(account.referral_note, referral)
        helper.assert_called_once_with("create", "newuser", "abc", valid_days=30, max_connections=2)
        helper.reset_mock()
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, referral)
        self.assertContains(page, "30 days after first connection")

    @patch("accounts.views.call_helper")
    def test_single_referral_text_can_be_reused(self, helper):
        code = "Personal Name " + "z" * 5000
        VpnAccount.objects.create(username="owner1", created_by=self.staff, referral_note=code)
        response = self.client.post(reverse("create_account"), {
            "username": "owner2", "password": "abc", "valid_days": "7", "max_connections": "1",
            "referral_note": code,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(VpnAccount.objects.get(username="owner2").referral_note, code)
        helper.assert_called_once()

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
            self.client.post(action_url, {"action": "extend:30", "account_ids": selected})
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
        self.client.post(url, {"action": "extend:13", "account_ids": [str(account.pk)]})
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
    def test_zero_connection_limit_removes_existing_limit(self, helper):
        account = VpnAccount.objects.create(username="alice", created_by=self.staff, max_connections=2)
        self.client.post(reverse("edit_account", args=[account.pk]), {"max_connections": "0"})
        account.refresh_from_db()
        self.assertIsNone(account.max_connections)
        self.assertEqual(helper.call_args.kwargs["max_connections"], 0)

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
            "password": "bad\npassword", "valid_days": "-2", "max_connections": "-1",
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

    @patch("accounts.views.get_system_metrics", return_value={})
    @patch("accounts.views.get_account_usage", return_value={})
    def test_dashboard_shows_password_and_red_disabled_state_without_referral_totals(self, usage, metrics):
        VpnAccount.objects.create(
            username="alice", created_by=self.staff, enabled=False,
            expires_at=timezone.now() - timedelta(days=1),
            referral_code="ref-alice", password_ciphertext=encrypt_password("visible-pass"),
        )
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "visible-pass")
        self.assertContains(page, 'class="state state-disabled-account">Disabled</span>')
        self.assertContains(page, "<th>Account</th><th>Password</th><th>Referral</th>", html=False)
        self.assertNotContains(page, "data-reveal-password")
        self.assertNotContains(page, "referrals ·")
        self.assertIn("no-store", page["Cache-Control"])
        self.client.logout()
        self.assertNotIn("visible-pass", self.client.get(reverse("dashboard")).content.decode())

    def test_admin_can_change_own_password_and_keep_session(self):
        response = self.client.post(reverse("change_admin_password"), {
            "old_password": "correct-long-password",
            "new_password1": "1",
            "new_password2": "1",
        })
        self.assertEqual(response.status_code, 302)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password("1"))
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
        self.assertContains(response, "http://localhost:18080/private-42/settings/web/")
        self.assertContains(response, "No confirmation is needed.")
        self.assertNotContains(response, "window.location.assign")
        helper.assert_called_once_with("web-stage", web_path="/private-42", web_port=18080)

    @patch("accounts.views.call_helper", return_value='{"url": "http://localhost/private-42/", "same_port": true}')
    def test_path_only_change_opens_new_address_when_ready(self, helper):
        response = self.client.post(reverse("stage_web_address"), {"path": "/private-42", "port": "80"})
        self.assertContains(response, "http://localhost/private-42/settings/web/")
        self.assertContains(response, "window.location.assign(link.href)")
        self.assertContains(response, "This page will open it automatically when ready.")
        self.assertContains(response, "No confirmation is needed.")

    @patch("accounts.views.call_helper", return_value='{"url": "http://localhost:18080/private-42/"}')
    def test_web_change_requires_staff_and_post(self, helper):
        self.assertEqual(self.client.get(reverse("stage_web_address")).status_code, 405)
        self.assertEqual(self.client.post("/settings/web-address/confirm/").status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("stage_web_address"), {"path": "/private-42", "port": "18080"}).status_code, 302)
        helper.assert_not_called()

    @override_settings(TLS_ENABLED=True, HTTPS_PORT="8443", ALLOWED_HOSTS=["example.com"])
    @patch("accounts.views.call_helper", return_value="null")
    def test_settings_shows_https_address_and_port_control(self, helper):
        page = self.client.get(reverse("settings_web"), HTTP_HOST="example.com:8443", secure=True)
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
        self.assertContains(page, 'name="action" value="extend:30"')
        self.assertContains(page, 'name="action" value="usage-reset"')
        self.assertNotContains(page, 'data-bulk-action-select')
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

    @patch("accounts.views.call_backup", return_value={"enabled": True, "time": "03:00"})
    @patch("accounts.views.list_backups", return_value=[])
    def test_backup_settings_requires_staff(self, listing, helper):
        page = self.client.get(reverse("backup_settings"))
        self.assertContains(page, "Backup and restore")
        self.assertContains(page, "Automatic backups")
        self.assertNotContains(page, "Import users from Shahan")
        self.assertContains(page, "Backups contain sensitive account data")
        self.assertNotContains(page, "Backup passphrase")
        self.assertNotContains(page, 'name="passphrase"')
        self.client.logout()
        self.assertEqual(self.client.get(reverse("backup_settings")).status_code, 302)
        self.assertEqual(self.client.post(reverse("create_backup")).status_code, 302)
        listing.assert_called_once()
        helper.assert_called_once_with("schedule-status")

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

    @patch("accounts.views.call_backup", return_value={"type": "full", "accounts": 2})
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
            self.assertEqual(set(helper.call_args.kwargs), {"name", "actor_id"})

    @patch("accounts.views.call_backup", return_value={"type": "users", "accounts": 3, "recovery_backup": "before-import.tar.gz"})
    def test_restore_form_imports_users_archive_through_same_route(self, helper):
        with TemporaryDirectory() as directory, patch("accounts.backups.UPLOAD_DIR", Path(directory)), patch("accounts.views.UPLOAD_DIR", Path(directory)):
            response = self.client.post(reverse("restore_backup"), {
                "backup_file": SimpleUploadedFile("users.tar.gz", b"archive"), "confirm_replace": "yes",
            })
            self.assertRedirects(response, reverse("backup_settings"), fetch_redirect_response=False)
            helper.assert_called_once()
            self.assertEqual(helper.call_args.args, ("restore",))

    @patch("accounts.views.call_backup", return_value={"type": "full", "accounts": 2})
    def test_saved_backup_restores_without_upload(self, helper):
        name = "sshpanel-auto-20261014T000000Z-12345678.tar.gz"
        response = self.client.post(reverse("restore_backup"), {"saved_name": name, "confirm_replace": "yes"})
        self.assertContains(response, "2 VPN accounts")
        helper.assert_called_once_with("restore", name=name, actor_id=self.staff.pk)

    @patch("accounts.views.call_backup", return_value={"name": "sshpanel-20261014T000000Z-12345678.tar.gz"})
    def test_remove_backup_requires_staff_and_valid_name(self, helper):
        name = "sshpanel-20261014T000000Z-12345678.tar.gz"
        self.assertEqual(self.client.post(reverse("remove_backup", args=["invalid.tar.gz"])).status_code, 404)
        self.assertEqual(self.client.post(reverse("remove_backup", args=[name])).status_code, 302)
        helper.assert_called_once_with("remove", name=name)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("remove_backup", args=[name])).status_code, 302)

    @patch("accounts.views.call_backup", return_value={"enabled": True, "time": "04:15"})
    def test_backup_schedule_can_be_changed(self, helper):
        self.assertEqual(self.client.post(reverse("update_backup_schedule"), {"enabled": "yes", "time": "04:15"}).status_code, 302)
        helper.assert_called_once_with("schedule-set", enabled=True, time="04:15")

    @patch("accounts.views.call_helper")
    def test_cross_site_post_is_rejected(self, helper):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(reverse("create_account"), {"username": "vpn_alice", "password": "strong-password-123"})
        self.assertEqual(response.status_code, 403)
        helper.assert_not_called()

    @patch("accounts.views.get_ssh_port", return_value=22)
    @patch("accounts.views.get_system_metrics")
    def test_dashboard_and_metrics_are_staff_only(self, metrics, port):
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

    def test_accepts_one_character_admin_password(self):
        with patch("sys.stdin", StringIO("emad\n1\n")):
            call_command("initial_admin", stdout=StringIO())
        self.assertTrue(get_user_model().objects.get(username="emad").check_password("1"))

    def test_accepts_numeric_admin_username(self):
        with patch("sys.stdin", StringIO("1\n2\n")):
            call_command("initial_admin", stdout=StringIO())
        self.assertTrue(get_user_model().objects.get(username="1").check_password("2"))
        self.assertTrue(self.client.login(username="1", password="2"))

    def test_accepts_english_username_with_symbols(self):
        with patch("sys.stdin", StringIO("emad.test+1@example.com\n2\n")):
            call_command("initial_admin", stdout=StringIO())
        self.assertTrue(self.client.login(username="emad.test+1@example.com", password="2"))

    def test_trims_unintended_whitespace_around_admin_username(self):
        with patch("sys.stdin", StringIO(" admin \r\n2\n")):
            call_command("initial_admin", stdout=StringIO())
        self.assertTrue(self.client.login(username="admin", password="2"))

    def test_rejects_non_english_admin_username(self):
        with patch("sys.stdin", StringIO("مدیر\n2\n")):
            with self.assertRaises(CommandError):
                call_command("initial_admin", stdout=StringIO())

    def test_rejects_empty_admin_password(self):
        with patch("sys.stdin", StringIO("emad\n\n")):
            with self.assertRaises(CommandError):
                call_command("initial_admin", stdout=StringIO())
        self.assertFalse(get_user_model().objects.filter(username="emad").exists())
