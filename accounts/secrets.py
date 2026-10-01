"""Encrypt recoverable VPN credentials using a server-side application secret."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _cipher():
    # Separate this purpose from Django's signing key with a domain prefix.
    material = hashlib.sha256(b"sshvpn-credential-v1:" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def encrypt_password(password):
    return _cipher().encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_password(token):
    if not token:
        return None
    try:
        return _cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None
