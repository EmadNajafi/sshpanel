"""Small Telegram Bot API client with bounded, streaming document uploads."""

import http.client
import json
from pathlib import Path
import secrets
import urllib.error
import urllib.request


DOCUMENT_CHUNK_SIZE = 45 * 1024 * 1024
DOWNLOAD_LIMIT = 20 * 1024 * 1024


class TelegramError(Exception):
    pass


class TelegramAPI:
    def __init__(self, token):
        self.token = token

    def call(self, method, payload=None, *, timeout=15):
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get("description", "Telegram rejected the request.")
            except (ValueError, OSError):
                detail = "Telegram rejected the request."
            raise TelegramError(str(detail)[:250]) from None
        except (OSError, ValueError):
            raise TelegramError("Could not reach Telegram.") from None
        if not isinstance(data, dict):
            raise TelegramError("Telegram returned an invalid response.")
        if not data.get("ok"):
            raise TelegramError(str(data.get("description", "Telegram rejected the request."))[:250])
        return data.get("result")

    def send_message(self, chat_id, text, keyboard=None):
        text = str(text)
        if len(text) > 4000:
            for start in range(0, len(text), 4000):
                self.send_message(chat_id, text[start:start + 4000])
            return
        payload = {"chat_id": chat_id, "text": text}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        self.call("sendMessage", payload)

    def download_file(self, file_id, destination):
        info = self.call("getFile", {"file_id": file_id})
        if not isinstance(info, dict) or not isinstance(info.get("file_path"), str):
            raise TelegramError("Telegram did not return a file path.")
        if isinstance(info.get("file_size"), int) and info["file_size"] > DOWNLOAD_LIMIT:
            raise TelegramError("Telegram only lets bots download files up to 20 MB. Use the web panel for this archive.")
        segments = info["file_path"].split("/")
        if not segments or any(segment in ("", ".", "..") for segment in segments):
            raise TelegramError("Telegram returned an invalid file path.")
        request = urllib.request.Request(
            f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}"
        )
        destination = Path(destination)
        try:
            with urllib.request.urlopen(request, timeout=60) as response, destination.open("xb") as output:
                total = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > DOWNLOAD_LIMIT:
                        raise TelegramError("Telegram file exceeds the 20 MB download limit.")
                    output.write(block)
        except (OSError, urllib.error.URLError):
            destination.unlink(missing_ok=True)
            raise TelegramError("Could not download the Telegram file.") from None
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return total

    def send_document(self, chat_id, path, *, start=0, length=None, filename=None, caption=""):
        path = Path(path)
        length = path.stat().st_size - start if length is None else length
        if length > DOCUMENT_CHUNK_SIZE:
            raise TelegramError("Document part exceeds the Telegram upload limit.")
        boundary = "sshvpn" + secrets.token_hex(16)
        filename = (filename or path.name).replace('"', "_").replace("\r", "").replace("\n", "")
        preface = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        ending = f"\r\n--{boundary}--\r\n".encode("ascii")
        connection = http.client.HTTPSConnection("api.telegram.org", timeout=120)
        try:
            connection.putrequest("POST", f"/bot{self.token}/sendDocument")
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(len(preface) + length + len(ending)))
            connection.endheaders()
            connection.send(preface)
            with path.open("rb") as source:
                source.seek(start)
                remaining = length
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise TelegramError("Backup changed while uploading.")
                    connection.send(block)
                    remaining -= len(block)
            connection.send(ending)
            response = connection.getresponse()
            data = json.loads(response.read(1024 * 1024))
            if response.status != 200 or not data.get("ok"):
                raise TelegramError(str(data.get("description", "Telegram rejected the backup."))[:250])
        except (OSError, ValueError, http.client.HTTPException):
            raise TelegramError("Could not upload the backup to Telegram.") from None
        finally:
            connection.close()
