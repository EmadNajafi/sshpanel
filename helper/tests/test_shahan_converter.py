import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[2] / "tools"))
from shahan_to_user_import import USER_COLUMNS, convert, insert_rows


class ShahanConverterTests(unittest.TestCase):
    def test_mysql_quoted_values_are_preserved(self):
        rows = insert_rows("INSERT INTO `users` VALUES (1,'ali','p,as\\'s','ref ');", "users")
        self.assertEqual(rows, [["1", "ali", "p,as's", "ref "]])

    def test_expiry_referral_and_unlimited_connection(self):
        schema = "CREATE TABLE `users` (\n" + "\n".join(
            f"  `{name}` varchar(100)," for name in USER_COLUMNS
        ).rstrip(",") + "\n) ENGINE=InnoDB;\n"
        values = ["1", "ali", "plainpass", "", "", "0", "2026-10-01", "2026-10-31",
                  "true", "", "campaign with space ", "", "30", "", "", "", "", "", "", ""]
        row = "(" + ",".join("'" + value.replace("'", "\\'") + "'" for value in values) + ")"
        users = convert(schema + "INSERT INTO `users` VALUES " + row + ";")
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["password"], "plainpass")
        self.assertEqual(users[0]["max_connections"], 0)
        self.assertEqual(users[0]["referral_note"], "campaign with space ")
        self.assertIsNone(users[0]["traffic_limit_bytes"])
        self.assertIsNone(users[0]["valid_days"])
        self.assertGreater(users[0]["expires_at"], users[0]["created_at"])


if __name__ == "__main__":
    unittest.main()
