from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import TelegramBotConfig, VpnAccount
from .secrets import decrypt_bot_token, encrypt_bot_token
from .telegram_bot import handle_update, send_latest_automatic_backup
from .telegram_api import TelegramError


class TelegramSettingsTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="secret", is_staff=True)
        self.client.force_login(self.owner)

    @patch("accounts.views.TelegramAPI")
    def test_token_is_encrypted_and_never_rendered(self, api_class):
        api_class.return_value.call.return_value = {"is_bot": True, "username": "test_panel_bot"}
        token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
        response = self.client.post(reverse("save_telegram_bot"), {
            "token": token, "enabled": "yes", "allowed_ids": "12345\n67890",
        })
        self.assertEqual(response.status_code, 302)
        config = TelegramBotConfig.objects.get(pk=1)
        self.assertTrue(config.enabled)
        self.assertEqual(config.allowed_user_ids, [12345, 67890])
        self.assertEqual(decrypt_bot_token(config.token_ciphertext), token)
        self.assertNotIn(token, self.client.get(reverse("panel_settings")).content.decode())

    @patch("accounts.views.TelegramAPI")
    def test_invalid_ids_do_not_replace_existing_configuration(self, api_class):
        config = TelegramBotConfig.objects.create(
            pk=1, owner=self.owner, enabled=True,
            token_ciphertext=encrypt_bot_token("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"),
            allowed_user_ids=[12345],
        )
        self.client.post(reverse("save_telegram_bot"), {"enabled": "yes", "allowed_ids": "12345 wrong"})
        config.refresh_from_db()
        self.assertEqual(config.allowed_user_ids, [12345])
        self.assertTrue(config.enabled)
        api_class.assert_not_called()

    def test_remove_clears_access_and_token(self):
        TelegramBotConfig.objects.create(
            pk=1, owner=self.owner, enabled=True,
            token_ciphertext=encrypt_bot_token("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"),
            allowed_user_ids=[12345],
        )
        self.client.post(reverse("save_telegram_bot"), {"remove": "yes"})
        config = TelegramBotConfig.objects.get(pk=1)
        self.assertFalse(config.enabled)
        self.assertEqual(config.token_ciphertext, "")
        self.assertEqual(config.allowed_user_ids, [])

    @patch("accounts.views.TelegramAPI")
    def test_test_message_targets_only_saved_ids(self, api_class):
        TelegramBotConfig.objects.create(
            pk=1, owner=self.owner, enabled=True,
            token_ciphertext=encrypt_bot_token("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"),
            allowed_user_ids=[12345, 67890],
        )
        self.client.post(reverse("test_telegram_bot"))
        self.assertEqual(api_class.return_value.send_message.call_count, 2)
        self.assertEqual(
            [item.args[0] for item in api_class.return_value.send_message.call_args_list],
            [12345, 67890],
        )


class TelegramCommandTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="secret", is_staff=True)
        self.config = TelegramBotConfig.objects.create(
            pk=1, owner=self.owner, enabled=True,
            token_ciphertext=encrypt_bot_token("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"),
            allowed_user_ids=[12345],
        )
        self.api = Mock()
        self.pending = {}

    def message(self, user_id, text):
        return {"message": {"chat": {"id": user_id, "type": "private"},
                            "from": {"id": user_id}, "text": text}}

    def callback(self, data):
        return {"callback_query": {
            "id": "callback-1", "from": {"id": 12345}, "data": data,
            "message": {"chat": {"id": 12345, "type": "private"}},
        }}

    def test_unknown_chat_cannot_manage_accounts_but_can_read_own_id(self):
        handle_update(self.config, self.api, self.message(99999, "/delete alice"), self.pending)
        self.api.send_message.assert_not_called()
        handle_update(self.config, self.api, self.message(99999, "/id"), self.pending)
        self.assertIn("99999", self.api.send_message.call_args.args[1])

    def test_group_chat_is_ignored_even_for_allowed_user(self):
        update = {"message": {"chat": {"id": -100, "type": "supergroup"},
                              "from": {"id": 12345}, "text": "/status"}}
        handle_update(self.config, self.api, update, self.pending)
        self.api.send_message.assert_not_called()

    @patch("accounts.telegram_bot.call_helper")
    def test_delete_needs_a_fresh_private_confirmation(self, helper):
        VpnAccount.objects.create(username="alice", created_by=self.owner)
        handle_update(self.config, self.api, self.message(12345, "/delete alice"), self.pending)
        helper.assert_not_called()
        nonce = self.pending[12345][0]
        handle_update(self.config, self.api, self.callback(f"ok:{nonce}"), self.pending)
        helper.assert_called_once_with("delete", "alice")
        self.assertFalse(VpnAccount.objects.filter(username="alice").exists())
        handle_update(self.config, self.api, self.callback(f"ok:{nonce}"), self.pending)
        self.assertEqual(helper.call_count, 1)

    @patch("accounts.telegram_bot.get_account_usage", return_value={})
    @patch("accounts.views.call_helper")
    def test_create_uses_panel_provisioning_rules(self, helper, usage):
        handle_update(
            self.config, self.api,
            self.message(12345, '/create username=alice password=abc days=30 limit=0 traffic=0 referral="friend" first=1'),
            self.pending,
        )
        account = VpnAccount.objects.get(username="alice")
        self.assertEqual(account.valid_days, 30)
        self.assertIsNone(account.max_connections)
        self.assertEqual(account.referral_note, "friend")
        helper.assert_any_call("create", "alice", "abc", max_connections=0, valid_days=30)

    @patch("accounts.telegram_bot.send_backup")
    @patch("accounts.telegram_bot.list_backups")
    def test_automatic_backup_is_sent_once_to_each_admin(self, list_saved, send):
        self.config.allowed_user_ids = [12345, 67890]
        self.config.save()
        list_saved.return_value = [{"name": "sshpanel-auto-20261015T120000Z-12345678.tar.gz", "automatic": True}]
        self.assertIn("Sent", send_latest_automatic_backup())
        self.assertEqual([item.args[1] for item in send.call_args_list], [12345, 67890])
        self.assertEqual(send_latest_automatic_backup(), "No new automatic backup.")
        self.assertEqual(send.call_count, 2)

    @patch("accounts.telegram_bot.send_backup")
    @patch("accounts.telegram_bot.list_backups")
    def test_backup_delivery_continues_after_one_chat_fails(self, list_saved, send):
        self.config.allowed_user_ids = [12345, 67890]
        self.config.save()
        list_saved.return_value = [{"name": "sshpanel-auto-20261015T120000Z-12345678.tar.gz", "automatic": True}]
        send.side_effect = [TelegramError("Chat not available"), None]
        with self.assertRaises(TelegramError):
            send_latest_automatic_backup()
        self.assertEqual(send.call_count, 2)
        self.config.refresh_from_db()
        self.assertEqual(self.config.last_backup_name, "")

    @patch("accounts.telegram_bot.call_backup", return_value={"accounts": 2})
    def test_private_archive_upload_requires_restore_confirmation(self, backup):
        with TemporaryDirectory() as directory, patch("accounts.telegram_bot.UPLOAD_DIR", Path(directory)):
            self.api.download_file.side_effect = lambda file_id, target: target.write_bytes(b"archive")
            update = self.message(12345, "")
            update["message"]["document"] = {"file_id": "telegram-file", "file_name": "users.tar.gz"}
            handle_update(self.config, self.api, update, self.pending)
            uploaded = list(Path(directory).iterdir())
            self.assertEqual(len(uploaded), 1)
            name = uploaded[0].name
            handle_update(self.config, self.api, self.message(12345, f"/restore {name}"), self.pending)
            backup.assert_not_called()
            nonce = self.pending[12345][0]
            handle_update(self.config, self.api, self.callback(f"ok:{nonce}"), self.pending)
            backup.assert_called_once_with("restore", name=name, actor_id=self.owner.pk)
            self.assertFalse(uploaded[0].exists())
