"""Scripted helpers share the desktop's checkpoint rules."""
import tempfile
import unittest
from unittest.mock import patch

import agenthelper
from gameengine import encode_action_id


class ScriptedAutosaveTests(unittest.TestCase):
    def test_llm_turn_and_duplicate_requests_do_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            created = agenthelper.create_game(session_id="save-policy-review", save_directory=directory, gamemode=3)
            self.assertTrue(created["ok"])
            session = agenthelper.get_session("save-policy-review")
            config = agenthelper.session_config("save-policy-review")
            config.autosave = True

            def act(kind, **params):
                return agenthelper.apply_action(session.session_id, encode_action_id(kind, params), session.revision)

            try:
                self.assertTrue(act("choose_initial_token", side="black")["ok"])
                agenthelper.configure_agent_play_mode(session.session_id, True, ("white",))
                with patch("agenthelper.save_session", wraps=agenthelper.save_session) as write:
                    self.assertTrue(act("fast_time_token")["ok"])
                    self.assertTrue(act("select_piece", at=[6, 1])["ok"])
                    self.assertTrue(act("move", **{"from": [6, 1], "to": [5, 1]})["ok"])
                    self.assertEqual(write.call_count, 0)
                    self.assertTrue(act("fast_time_token")["ok"])
                    self.assertEqual(write.call_count, 1)
                    args = (session.session_id, "select_piece:at=2,1", session.revision)
                    self.assertTrue(agenthelper.apply_action(*args, request_id="human-select")["ok"])
                    self.assertEqual(write.call_count, 2)
                    self.assertTrue(agenthelper.apply_action(*args, request_id="human-select")["replayed"])
                    self.assertEqual(write.call_count, 2)
            finally:
                agenthelper._SESSIONS.pop(session.session_id, None)
                agenthelper._SESSION_CONFIGS.pop(session.session_id, None)


if __name__ == "__main__":
    unittest.main()
