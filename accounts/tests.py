from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import AuditEvent, VpnAccount


class AccountViewsTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user("owner", password="correct-long-password", is_staff=True)
        self.client.force_login(self.staff)

    @patch("accounts.views.call_helper")
    def test_create_and_disable_account(self, helper):
        response = self.client.post(reverse("create_account"), {"username": "vpn_alice", "password": "strong-password-123"})
        self.assertEqual(response.status_code, 302)
        helper.assert_called_with("create", "vpn_alice", "strong-password-123")
        account = VpnAccount.objects.get(username="vpn_alice")
        self.client.post(reverse("disable_account", args=[account.pk]))
        account.refresh_from_db()
        self.assertFalse(account.enabled)
        self.assertEqual(AuditEvent.objects.filter(succeeded=True).count(), 2)

    @patch("accounts.views.call_helper")
    def test_bad_username_does_not_reach_helper(self, helper):
        self.client.post(reverse("create_account"), {"username": "root", "password": "strong-password-123"})
        helper.assert_not_called()
        self.assertFalse(VpnAccount.objects.exists())

    @patch("accounts.views.call_helper")
    def test_non_staff_cannot_create_account(self, helper):
        user = get_user_model().objects.create_user("viewer", password="correct-long-password")
        self.client.force_login(user)
        response = self.client.post(reverse("create_account"), {"username": "vpn_alice", "password": "strong-password-123"})
        self.assertEqual(response.status_code, 302)
        helper.assert_not_called()

    @patch("accounts.views.call_helper")
    def test_cross_site_post_is_rejected(self, helper):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(reverse("create_account"), {"username": "vpn_alice", "password": "strong-password-123"})
        self.assertEqual(response.status_code, 403)
        helper.assert_not_called()
