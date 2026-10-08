import unittest

from i18n import normalize_locale, translate


class TranslationTests(unittest.TestCase):
    def test_discord_locale_is_normalized(self):
        self.assertEqual(normalize_locale("sl-SI"), "sl")
        self.assertEqual(normalize_locale("en-US"), "en")

    def test_unknown_locale_falls_back_to_english(self):
        self.assertEqual(translate("settings.open", "de"), "Open settings")

    def test_slovenian_translation_is_retained(self):
        self.assertEqual(translate("settings.open", "sl"), "Odpri nastavitve")

    def test_runtime_message_interpolation(self):
        self.assertEqual(
            translate("power.finished", "en-US", {"name": "Survival", "state": "RUNNING"}),
            "**Survival** is now `RUNNING`.",
        )
        self.assertEqual(
            translate("power.finished", "sl-SI", {"name": "Survival", "state": "RUNNING"}),
            "**Survival** je zdaj `RUNNING`.",
        )
