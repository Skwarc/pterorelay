import re
import unittest
from pathlib import Path


MIGRATIONS = Path(__file__).parents[1] / "pterosync-discord" / "database" / "migrations"
EXPLICIT_NAME_RE = re.compile(r"\$table->(?:unique|index)\([^;]+?,\s*'([^']+)'\s*\);")


class ExtensionMigrationTests(unittest.TestCase):
    def test_explicit_database_identifiers_fit_mysql_limit(self):
        migration = (MIGRATIONS / "2026_10_06_000001_create_ext_pterosync_tables.php").read_text(encoding="utf-8")

        explicit_names = EXPLICIT_NAME_RE.findall(migration)
        self.assertIn("psync_role_permissions_unique", explicit_names)
        self.assertTrue(all(len(name) <= 64 for name in explicit_names))

    def test_integration_migration_names_its_channel_index(self):
        migration = (MIGRATIONS / "2026_10_07_000001_add_ext_pterosync_integration.php").read_text(encoding="utf-8")

        self.assertEqual(EXPLICIT_NAME_RE.findall(migration), ["psync_discord_channels_unique"])
