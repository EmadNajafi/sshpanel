from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .models import AuditEvent, TelegramBotConfig, VpnAccount
from .secrets import decrypt_bot_token, encrypt_bot_token
from .services import ProvisionError
from .telegram_bot import handle_update, send_latest_automatic_backup
from .telegram_api import TelegramAPI, TelegramError


class TelegramAPITests(SimpleTestCase):
    @patch("accounts.telegram_api.http.client.HTTPSConnection")
    def test_reuses_https_connection_for_poll_and_reply(self, connection_class):
        connection = connection_class.return_value
        connection.sock = Mock()
        response = Mock(status=200, will_close=False)
        response.read.return_value = b'{"ok": true, "result": []}'
        connection.getresponse.return_value = response
        api = TelegramAPI("test-token")
        self.assertEqual(api.call("getUpdates", timeout=30), [])
        api.send_message(12345, "hello")
        connection_class.assert_called_once_with("api.telegram.org", timeout=30)
        self.assertEqual(connection.request.call_count, 2)
        connection.sock.settimeout.assert_called_once_with(15)
        api.close()
        connection.close.assert_called_once()

    @patch("accounts.telegram_api.http.client.HTTPSConnection")
    def test_poll_reconnects_after_stale_socket(self, connection_class):
        stale = Mock()
        stale.request.side_effect = OSError("stale socket")
        fresh = Mock()
        fresh.getresponse.return_value = Mock(status=200, will_close=False)
        fresh.getresponse.return_value.read.return_value = b'{"ok": true, "result": []}'
        connection_class.side_effect = [stale, fresh]
        api = TelegramAPI("test-token")
        self.assertEqual(api.call("getUpdates"), [])
        self.assertEqual(connection_class.call_count, 2)
        stale.close.assert_called_once()
        api.close()


class TelegramSettingsTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="secret", is_staff=True)
        self.client.force_login(self.owner)

    @patch("accounts.views.TelegramAPI")
    @override_settings(TLS_ENABLED=True)
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
        self.assertNotIn(token, self.client.get(reverse("settings_telegram")).content.decode())

    @patch("accounts.views.TelegramAPI")
    @override_settings(TLS_ENABLED=True)
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
    @override_settings(TLS_ENABLED=True)
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

    @patch("accounts.views.TelegramAPI")
    def test_http_hides_token_and_rejects_configuration_and_test(self, api_class):
        token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
        page = self.client.get(reverse("settings_telegram")).content.decode()
        self.assertIn("Suspended until HTTPS", page)
        self.assertNotIn('name="token"', page)
        response = self.client.post(reverse("save_telegram_bot"), {
            "token": token, "enabled": "yes", "allowed_ids": "12345",
        }, follow=True)
        self.assertContains(response, "HTTPS is required to configure or use the Telegram bot.")
        self.assertFalse(TelegramBotConfig.objects.exists())
        response = self.client.post(reverse("test_telegram_bot"), follow=True)
        self.assertContains(response, "HTTPS is required to configure or use the Telegram bot.")
        api_class.assert_not_called()

    def test_http_suspends_existing_bot_but_allows_token_removal(self):
        TelegramBotConfig.objects.create(
            pk=1, owner=self.owner, enabled=True,
            token_ciphertext=encrypt_bot_token("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"),
            allowed_user_ids=[12345],
        )
        page = self.client.get(reverse("settings_telegram")).content.decode()
        self.assertIn("Suspended until HTTPS", page)
        self.assertIn("Remove bot token", page)
        self.assertNotIn('name="token"', page)
        self.client.post(reverse("save_telegram_bot"), {"remove": "yes"})
        self.assertFalse(TelegramBotConfig.objects.get(pk=1).enabled)


class TelegramCommandTests(TestCase):
    def setUp(self):
        https = patch("accounts.telegram_bot.panel_https_enabled", return_value=True)
        https.start()
        self.addCleanup(https.stop)
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

    def test_menu_lists_users_and_edits_callback_message(self):
        VpnAccount.objects.create(username="alice", created_by=self.owner)
        update = self.callback("menu:users:0")
        update["callback_query"]["message"]["message_id"] = 42
        handle_update(self.config, self.api, update, self.pending)
        self.api.call.assert_called_with("answerCallbackQuery", {"callback_query_id": "callback-1"})
        self.api.edit_message.assert_called_once()
        chat_id, message_id, body, keyboard = self.api.edit_message.call_args.args
        self.assertEqual((chat_id, message_id), (12345, 42))
        self.assertIn("کاربران: 1", body)
        self.assertTrue(any(button["callback_data"] == "u:alice" for row in keyboard for button in row))
        self.api.send_message.assert_not_called()

    @patch("accounts.telegram_bot.get_account_usage", return_value={})
    @patch("accounts.views.call_helper")
    def test_create_wizard_requires_confirmation(self, helper, usage):
        handle_update(self.config, self.api, self.callback("flow:create"), self.pending)
        for value in ("alice", "abc", "30"):
            handle_update(self.config, self.api, self.message(12345, value), self.pending)
        for _ in range(3):
            handle_update(self.config, self.api, self.callback("flow:skip"), self.pending)
        handle_update(self.config, self.api, self.callback("flow:value:0"), self.pending)
        self.assertFalse(VpnAccount.objects.filter(username="alice").exists())
        nonce = self.pending[12345][0]
        handle_update(self.config, self.api, self.callback(f"ok:{nonce}"), self.pending)
        account = VpnAccount.objects.get(username="alice")
        self.assertIsNone(account.max_connections)
        create_call = next(item for item in helper.call_args_list if item.args[:3] == ("create", "alice", "abc"))
        self.assertEqual(create_call.kwargs["max_connections"], 0)
        self.assertIn("expires_at", create_call.kwargs)

    def test_search_prompt_filters_referral_without_refresh(self):
        VpnAccount.objects.create(username="alice", referral_note="friend-one", created_by=self.owner)
        VpnAccount.objects.create(username="bob", referral_note="friend-two", created_by=self.owner)
        handle_update(self.config, self.api, self.callback("prompt:search"), self.pending)
        handle_update(self.config, self.api, self.message(12345, "friend-one"), self.pending)
        body, keyboard = self.api.send_message.call_args.args[1:3]
        self.assertIn("کاربران: 1", body)
        self.assertTrue(any(button["callback_data"] == "u:alice" for row in keyboard for button in row))
        self.assertFalse(any(button["callback_data"] == "u:bob" for row in keyboard for button in row))

    def test_cancel_clears_wizard(self):
        handle_update(self.config, self.api, self.callback("flow:create"), self.pending)
        handle_update(self.config, self.api, self.message(12345, "/cancel"), self.pending)
        self.assertNotIn(("flow", 12345), self.pending)
        self.assertNotIn(12345, self.pending)

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

    def test_http_ignores_commands_and_suspends_backup_delivery(self):
        with patch("accounts.telegram_bot.panel_https_enabled", return_value=False):
            handle_update(self.config, self.api, self.message(12345, "/id"), self.pending)
            self.api.send_message.assert_not_called()
            with patch("accounts.telegram_bot.list_backups") as backups:
                self.assertEqual(send_latest_automatic_backup(), "Telegram delivery requires HTTPS.")
                backups.assert_not_called()

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

    def test_failed_restore_still_removes_the_upload(self):
        from .backups import BackupError
        with TemporaryDirectory() as directory, patch("accounts.telegram_bot.UPLOAD_DIR", Path(directory)), \
             patch("accounts.telegram_bot.call_backup", side_effect=BackupError("bad archive")):
            name = "restore-" + "b" * 32 + ".tar.gz"
            (Path(directory) / name).write_bytes(b"archive")
            handle_update(self.config, self.api, self.message(12345, f"/restore {name}"), self.pending)
            nonce = self.pending[12345][0]
            handle_update(self.config, self.api, self.callback(f"ok:{nonce}"), self.pending)
            self.assertFalse((Path(directory) / name).exists())
        self.assertTrue(AuditEvent.objects.filter(action="restore", succeeded=False,
                                                  telegram_user_id=12345).exists())

    def test_stale_uploads_are_purged(self):
        import os
        import time
        from .telegram_bot import purge_stale_uploads
        with TemporaryDirectory() as directory, patch("accounts.telegram_bot.UPLOAD_DIR", Path(directory)):
            old = Path(directory) / ("restore-" + "c" * 32 + ".tar.gz")
            new = Path(directory) / ("restore-" + "d" * 32 + ".tar.gz")
            old.write_bytes(b"x")
            new.write_bytes(b"x")
            two_days_ago = time.time() - 2 * 86400
            os.utime(old, (two_days_ago, two_days_ago))
            purge_stale_uploads()
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())

    def test_unknown_user_sending_a_sticker_does_not_crash(self):
        update = self.message(99999, "")
        update["message"]["sticker"] = {"file_id": "sticker"}
        handle_update(self.config, self.api, update, self.pending)
        self.api.send_message.assert_not_called()

    @patch("accounts.telegram_bot.call_helper")
    def test_password_messages_are_deleted_from_the_chat(self, helper):
        VpnAccount.objects.create(username="alice", created_by=self.owner)
        update = self.message(12345, "/password alice n3w-secret")
        update["message"]["message_id"] = 77
        handle_update(self.config, self.api, update, self.pending)
        helper.assert_called_once_with("password", "alice", "n3w-secret")
        self.api.delete_message.assert_called_once_with(12345, 77)
        self.assertTrue(AuditEvent.objects.filter(action="password", telegram_user_id=12345).exists())

    def test_ordinary_commands_are_not_deleted(self):
        update = self.message(12345, "/help")
        update["message"]["message_id"] = 78
        handle_update(self.config, self.api, update, self.pending)
        self.api.delete_message.assert_not_called()

    def test_secret_detection(self):
        from .telegram_bot import contains_secret
        self.assertTrue(contains_secret("/create username=a password=x days=1"))
        self.assertTrue(contains_secret("/adminpassword old new"))
        self.assertFalse(contains_secret("/bulkcreate count=2 prefix=u start=1 password=auto days=1"))
        self.assertFalse(contains_secret("/user alice"))

    @patch("accounts.telegram_bot.call_helper", side_effect=ProvisionError("port busy"))
    def test_failed_port_change_is_audited(self, helper):
        handle_update(self.config, self.api, self.message(12345, "/sshport 2222"), self.pending)
        nonce = self.pending[12345][0]
        handle_update(self.config, self.api, self.callback(f"ok:{nonce}"), self.pending)
        self.assertTrue(AuditEvent.objects.filter(action="port-stage", succeeded=False).exists())

    @patch("accounts.telegram_bot.call_helper", return_value='{"allowed": false, "disconnected": 1}')
    def test_closing_main_port_needs_confirmation(self, helper):
        handle_update(self.config, self.api, self.message(12345, "/vpnmain close"), self.pending)
        helper.assert_not_called()
        nonce = self.pending[12345][0]
        handle_update(self.config, self.api, self.callback(f"ok:{nonce}"), self.pending)
        helper.assert_called_once_with("vpn-main-access", allowed=False)
        self.assertTrue(AuditEvent.objects.filter(action="main-port-close", telegram_user_id=12345).exists())
