"""Configuration location, safe persistence and invalid-file recovery."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_settings import LocalSettings, settings_path


class LocalSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "CheckMate" / "settings.json"

    def test_windows_location_uses_user_directory_not_package(self):
        with patch("local_settings.sys.platform", "win32"), patch.dict(
            "os.environ", {"LOCALAPPDATA": str(Path(self.directory.name) / "Local")}
        ):
            self.assertEqual(settings_path(), Path(self.directory.name) / "Local" / "CheckMate" / "settings.json")

    def test_missing_settings_do_not_create_files_at_launch(self):
        store = LocalSettings(self.path)
        preferences = store.load()
        self.assertFalse(preferences["agent_play_mode"])
        self.assertEqual(preferences["player_types"], {"black": "human", "white": "llm"})
        self.assertEqual(preferences["play_control_mode"], "step")
        self.assertFalse(self.path.parent.exists())

    def test_round_trip_and_unchanged_preferences_skip_disk_write(self):
        store = LocalSettings(self.path)
        store.load()
        self.assertTrue(store.save({"agent_play_mode": True, "llm_color": "black", "session": "excluded"}))
        restored = LocalSettings(self.path).load()
        self.assertTrue(restored["agent_play_mode"])
        self.assertEqual(restored["player_types"], {"black": "llm", "white": "human"})
        persisted = json.loads(self.path.read_text())
        self.assertNotIn("session", persisted)
        self.assertNotIn("api_key", persisted["api_profiles"]["black"])
        with patch("local_settings.os.replace") as replace:
            self.assertFalse(store.save({"agent_play_mode": True, "llm_color": "black"}))
            replace.assert_not_called()

    def test_corrupt_or_invalid_settings_fall_back_safely(self):
        self.path.parent.mkdir()
        for text in ("{broken", "[]"):
            self.path.write_text(text)
            store = LocalSettings(self.path)
            self.assertFalse(store.load()["agent_play_mode"])
            self.assertIsNotNone(store.error)
        self.path.write_text('{"agent_play_mode": "false", "llm_color": "invalid"}')
        restored = LocalSettings(self.path).load()
        self.assertFalse(restored["agent_play_mode"])
        self.assertEqual(restored["llm_color"], "white")

    def test_failed_replace_keeps_previous_file_and_can_retry(self):
        store = LocalSettings(self.path)
        store.save({"agent_play_mode": True, "llm_color": "white"})
        before = self.path.read_bytes()
        with patch("local_settings.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                store.save({"agent_play_mode": True, "llm_color": "black"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.assertTrue(store.save({"agent_play_mode": True, "llm_color": "black"}))

    @unittest.skipUnless(sys.platform == "win32", "Uses Windows user credential protection")
    def test_api_key_is_protected_locally_and_restored_with_profiles(self):
        store = LocalSettings(self.path)
        preferences = store.load()
        preferences["api_profiles"]["black"].update({"api_key": "fixture-secret-key", "model": "my-model"})
        preferences["player_types"] = {"black": "llm", "white": "llm"}
        preferences["play_control_mode"] = "auto"
        store.save(preferences)
        self.assertNotIn("fixture-secret-key", self.path.read_text())
        restored = LocalSettings(self.path).load()
        self.assertEqual(restored["api_profiles"]["black"]["api_key"], "fixture-secret-key")
        self.assertEqual(restored["api_profiles"]["black"]["model"], "my-model")
        self.assertEqual(restored["player_types"], {"black": "llm", "white": "llm"})
        self.assertEqual(restored["play_control_mode"], "auto")

    def test_malformed_nested_preferences_are_ignored(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"api_profiles": [], "player_types": {"black": []}}))
        self.assertEqual(LocalSettings(self.path).load()["player_types"]["black"], "human")
        self.path.write_text(json.dumps({"api_profiles": {"black": {"reasoning_effort": {}, "token_parameter": []}}}))
        self.assertEqual(LocalSettings(self.path).load()["api_profiles"]["black"]["reasoning_effort"], "default")

    def test_loaded_profile_edit_is_saved_without_mutating_cached_preferences(self):
        store = LocalSettings(self.path)
        preferences = store.load()
        preferences["api_profiles"]["white"]["model"] = "first-model"
        self.assertTrue(store.save(preferences))
        preferences = store.load()
        preferences["api_profiles"]["white"]["model"] = "second-model"
        self.assertTrue(store.save(preferences))
        self.assertEqual(LocalSettings(self.path).load()["api_profiles"]["white"]["model"], "second-model")
