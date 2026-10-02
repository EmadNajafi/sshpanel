"""The current panel port follows completed SSH changes."""

import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sshvpn_port as port  # noqa: E402


class CurrentPortTests(unittest.TestCase):
    def test_completed_change_uses_new_port(self):
        with patch.object(port, "effective_ports", return_value=[6677]), \
             patch.object(port, "current_state", return_value=None):
            self.assertEqual(port.current_port(), 6677)

    def test_staged_change_uses_old_port_until_confirmed(self):
        with patch.object(port, "effective_ports", return_value=[5656, 6677]), \
             patch.object(port, "current_state", return_value={"old": 5656, "new": 6677}):
            self.assertEqual(port.current_port(), 5656)
