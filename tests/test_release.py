import unittest

from scripts.release import VERSION_RE, bumped


class ReleaseScriptTests(unittest.TestCase):
    def test_bump_prerelease_and_patch(self):
        self.assertEqual(bumped("0.2.0-beta.1"), "0.2.0-beta.2")
        self.assertEqual(bumped("0.2.0-rc.9"), "0.2.0-rc.10")
        self.assertEqual(bumped("1.4.7"), "1.4.8")

    def test_version_format(self):
        self.assertTrue(VERSION_RE.fullmatch("0.2.0-beta.2"))
        self.assertFalse(VERSION_RE.fullmatch("v0.2.0"))
        self.assertFalse(VERSION_RE.fullmatch("0.2"))
