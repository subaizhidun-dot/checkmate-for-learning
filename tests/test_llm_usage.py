"""Numeric usage survives saves; action histories and response text do not."""
import json
import tempfile
import unittest
from pathlib import Path

from gameengine import GameSession, save_session_file, load_session_file, encode_action_id
from llm_usage import LLMUsage
from agenttools import AgentTools


class LLMUsageTests(unittest.TestCase):
    def test_requests_and_turns_are_separate_and_retry_is_idempotent(self):
        usage = LLMUsage()
        for request_id, turn, total in (("r1", 1, 100), ("r2", 1, 200), ("r3", 2, 300)):
            usage.begin_request(request_id, "black", turn)
            usage.finish_request(request_id, {"input_tokens": total - 10, "output_tokens": 10})
        self.assertFalse(usage.begin_request("r1", "black", 1))
        self.assertFalse(usage.finish_request("r1", {"total_tokens": 999}))
        summary = usage.summary("black")
        self.assertEqual(summary["llm_turns"], 2)
        self.assertEqual(summary["requests"], 3)
        self.assertEqual(summary["total_tokens"], 600)
        self.assertEqual(summary["average_tokens_per_request"], 200)
        self.assertEqual(summary["average_tokens_per_turn"], 300)

    def test_usage_aliases_and_missing_usage_are_explicit(self):
        usage = LLMUsage()
        usage.begin_request("r1", "white", 2)
        usage.finish_request("r1", {"prompt_tokens": 40, "completion_tokens": 10})
        usage.begin_request("r2", "white", 2)
        usage.finish_request("r2", None, "stopped")
        self.assertEqual(usage.summary()["total_tokens"], 50)
        self.assertEqual(usage.summary()["unreported_requests"], 1)
        self.assertIsNone(usage.summary()["average_tokens_per_request"])
        self.assertIsNone(usage.summary()["input_tokens"])
        self.assertIsNone(usage.request_info("r2")["total_tokens"])

    def test_only_numeric_usage_saved_and_legacy_action_logs_ignored(self):
        session = GameSession.new_g3()
        session.submit(encode_action_id("choose_initial_token", {"side": "black"}), session.revision)
        session.llm_usage.begin_request("r1", "black", session.turn_number)
        session.llm_usage.finish_request("r1", {"input_tokens": 40, "output_tokens": 10, "reply": "secret reply"})
        self.assertTrue(session.action_log)
        with tempfile.TemporaryDirectory() as directory:
            path = save_session_file(session, Path(directory) / "game.json")
            contents = path.read_text()
            data = json.loads(contents)
            for field in ("action_log", "request_cache", "llm_log"):
                self.assertNotIn(field, data)
            self.assertNotIn("secret reply", contents)
            self.assertEqual(data["llm_stats"]["total"]["total_tokens"], 50)
            data["action_log"] = [entry.as_dict() for entry in session.action_log]
            path.write_text(json.dumps(data))
            loaded = load_session_file(path)
            self.assertEqual(loaded.action_log, [])
            self.assertEqual(loaded.llm_usage.summary()["total_tokens"], 50)
            self.assertEqual(loaded.turn_number, session.turn_number)

    def test_actual_extra_turn_changes_turn_number(self):
        from basicgame import Resource
        session = GameSession.new_g3()
        session.submit(encode_action_id("choose_initial_token", {"side": "black"}), session.revision)
        session.game.current_ap = 5
        session.game.set_piece((3, 3), Resource("black", "butterfly"))
        session.refresh_players()
        before = session.turn_number
        session.submit(encode_action_id("butterfly_extra_turn", {"at": [3, 3]}), session.revision)
        self.assertEqual(session.turn_number, before + 1)
        self.assertEqual(session.game.current_player, "black")

    def test_public_recent_history_uses_actual_log_success_field(self):
        session = GameSession.new_g2()
        session.submit(encode_action_id("choose_initial_token", {"side": "black"}), session.revision)
        result = AgentTools(session, "black").call("get_recent_history")
        self.assertTrue(result["ok"])
        self.assertTrue(result["actions"][0]["ok"])


if __name__ == "__main__":
    unittest.main()
