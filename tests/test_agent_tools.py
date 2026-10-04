"""Model visibility, side boundaries and ordered action feedback."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agenttools import AgentTools, TOOLS
from gameengine import GameSession, encode_action_id, save_session_file, load_session_file


def playing(mode=3):
    session = GameSession.new_game(mode)
    session.submit(encode_action_id("choose_initial_token", {"side": "black"}), session.revision)
    session.game.current_ap = session.game.max_ap = 5
    return session


class AgentToolTests(unittest.TestCase):
    def test_queries_do_not_evaluate_victory_or_expose_internal_records(self):
        for mode in (1, 2, 3):
            session = playing(mode)
            session.flow.last_condition_results = [True, False, True]
            session.condition_reveal_count = 1
            tools = AgentTools(session, "black")
            with patch.dict("gameengine.MODE_CONDITIONS", {mode: lambda *args: self.fail("Oracle called")}):
                state = tools.call("get_public_state")
                self.assertTrue(tools.call("get_gi_rules")["ok"])
                self.assertTrue(tools.call("get_legal_actions")["ok"])
            self.assertEqual(state["visible_condition_feedback"], [True])
            for private in ("g3_turn", "condition_results", "start_positions", "move_count",
                            "resources_changed", "wish_continuation", "skip_token_selection"):
                self.assertNotIn(private, json.dumps(state))
            state["board"][1][2] = None
            self.assertIsNotNone(session.game.get_piece((2, 1)))

    def test_only_current_game_rules_are_returned(self):
        shops = {1: {"elephant", "lion"}, 2: {"mole", "lion"}, 3: {"elephant", "lion", "butterfly"}}
        for mode in (1, 2, 3):
            tools = AgentTools(playing(mode), "black")
            rules = tools.call("get_gi_rules")
            self.assertEqual(rules["gamemode"], mode)
            self.assertEqual(set(rules["shop"]), shops[mode])
            self.assertFalse(tools.call("get_gi_rules", {"gamemode": 2})["ok"])
            self.assertNotIn("distance", json.dumps(rules))
            self.assertNotIn("exactly one movement", json.dumps(rules))

    def test_management_code_and_oracle_tools_are_not_exposed(self):
        tools = AgentTools(playing(), "black")
        for name in ("get_state", "summarize", "get_session", "create_game", "load_session", "list_sessions",
                     "save_session", "end_game", "configure", "get_g3_condition_results", "eval"):
            self.assertEqual(tools.call(name)["code"], "unknown_tool")
        self.assertNotIn("end_game", TOOLS)

    def test_other_side_cannot_act_and_old_game_is_rejected(self):
        session = playing()
        tools = AgentTools(session, "white")
        self.assertEqual(tools.call("get_legal_actions")["actions"], [])
        before = session.snapshot()
        result = tools.call("apply_action", {"action_id": "select_piece:at=2,1", "revision": session.revision})
        self.assertFalse(result["ok"])
        self.assertEqual(session.snapshot(), before)
        tools.is_active = lambda: False
        self.assertEqual(tools.call("get_public_state")["code"], "stale_game")

    def test_notes_are_private_initialized_and_saved(self):
        session = playing(2)
        black, white = AgentTools(session, "black"), AgentTools(session, "white")
        self.assertIn("opponent holds", black.call("get_notes")["notes"])
        black.call("update_notes", {"text": "My hypothesis"})
        self.assertNotIn("My hypothesis", json.dumps(white.call("get_notes")))
        with tempfile.TemporaryDirectory() as directory:
            path = save_session_file(session, Path(directory) / "game.json")
            loaded = load_session_file(path)
            self.assertEqual(AgentTools(loaded, "black").get_notes()["notes"], "My hypothesis")
        session = playing()
        session.game.time_token_owner = None
        self.assertIn("special victory is impossible", AgentTools(session, "black").get_notes()["notes"])

    def test_ng_plus_notes_explain_first_condition_and_mate_without_overwriting_edits(self):
        from agent_prompts import NG_PLUS_NOTES
        for mode in (1, 2, 3):
            source = playing()
            source.end_session("mate", loser="white")
            session = GameSession.new_game(mode, previous_session=source)
            self.assertIsNone(session.game.time_token_owner)
            for side in ("black", "white"):
                notes = AgentTools(session, side).get_notes()["notes"]
                self.assertEqual(notes, NG_PLUS_NOTES)
                self.assertIn("first victory condition can never be met", notes)
                self.assertIn("action points remaining but no legal action", notes)
            session.agent_notes["black"] = "Without a time token, special victory is impossible. Focus on mating the opponent."
            session.agent_notes["white"] = "My plan: block the opponent's remaining movement."
            with tempfile.TemporaryDirectory() as directory:
                path = save_session_file(session, Path(directory) / "ng-plus.json")
                loaded = load_session_file(path)
            self.assertEqual(AgentTools(loaded, "black").get_notes()["notes"], NG_PLUS_NOTES)
            self.assertEqual(AgentTools(loaded, "white").get_notes()["notes"], session.agent_notes["white"])

    def test_plan_executes_prefix_and_retry_does_not_repeat_it(self):
        session = playing()
        tools = AgentTools(session, "black")
        revision = session.revision
        args = {"revision": revision, "request_id": "plan-1", "actions": [
            {"type": "select_piece", "params": {"at": [2, 1]}},
            {"type": "move", "params": {"from": [2, 1], "to": [3, 1]}},
            {"type": "move", "params": {"from": [3, 1], "to": [8, 6]}},
        ]}
        result = tools.call("submit_plan", args)
        self.assertEqual(len(result["executed"]), 2)
        self.assertEqual(result["stop_reason"], "illegal_action")
        self.assertNotIn("g3_turn", json.dumps(result))
        self.assertIsNone(session.game.get_piece((2, 1)))
        self.assertIsNotNone(session.game.get_piece((3, 1)))
        after = session.snapshot()
        self.assertTrue(tools.call("submit_plan", args)["replayed"])
        self.assertEqual(session.snapshot(), after)
        args["actions"].pop()
        self.assertEqual(tools.call("submit_plan", args)["code"], "request_conflict")

    def test_action_retry_has_only_public_current_state(self):
        session = playing()
        tools = AgentTools(session, "black")
        args = {"action_id": "select_piece:at=2,1", "revision": session.revision, "request_id": "select"}
        first = tools.call("apply_action", args)
        second = tools.call("apply_action", args)
        self.assertTrue(first["ok"])
        self.assertTrue(second["replayed"])
        self.assertNotIn("g3_turn", json.dumps(second))

    def test_images_only_use_ordinals(self):
        cards = [{"index": 1, "mime_type": "image/png", "data": base64.b64encode(b"PNG").decode()}]
        tools = AgentTools(playing(), "black", image_provider=lambda: cards)
        self.assertEqual(tools.call("get_rule_images", {"index": 1})["images"], cards)
        for invalid in (0, 2, True, "../game3.py"):
            self.assertFalse(tools.call("get_rule_images", {"index": invalid})["ok"])


if __name__ == "__main__":
    unittest.main()
