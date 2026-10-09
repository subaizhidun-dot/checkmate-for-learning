"""Model visibility, side boundaries and ordered action feedback."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agenttools import AgentTools, TOOLS
from basicgame import Resource
from gameengine import GameSession, encode_action_id, save_session_file, load_session_file


def playing(mode=3):
    session = GameSession.new_game(mode)
    session.submit(encode_action_id("choose_initial_token", {"side": "black"}), session.revision)
    session.game.current_ap = session.game.max_ap = 5
    return session


class AgentToolTests(unittest.TestCase):
    def test_direct_move_selects_and_moves_once_and_retries_without_spending_ap(self):
        session = playing()
        tools = AgentTools(session, "black")
        self.assertIn({"source": [2, 1], "target": [3, 1], "ap": 1}, tools.get_legal_actions()["moves"])
        args = {"source": [2, 1], "target": [3, 1], "revision": session.revision, "request_id": "move-1"}
        result = tools.call("move_piece", args)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["executed"]), 2)
        self.assertNotIn("state", result)
        self.assertNotIn("board", json.dumps(result))
        self.assertEqual(result["revision_before"], args["revision"])
        self.assertEqual(result["revision_after"], session.revision)
        self.assertEqual(result["current_ap"], 4)
        self.assertEqual(result["stop_reason"], "completed")
        self.assertEqual(session.game.current_ap, 4)
        self.assertEqual(session.flow.phase, "playing")
        self.assertIsNone(session.interaction.selected_pos)
        after = session.snapshot()
        self.assertTrue(tools.call("move_piece", args)["replayed"])
        self.assertEqual(session.snapshot(), after)
        self.assertNotIn("g3_turn", json.dumps(result))
        self.assertEqual(tools.call("move_piece", {**args, "target": [4, 1]})["code"], "request_conflict")

    def test_invalid_direct_move_does_not_leave_a_selection_or_change_board(self):
        session = playing()
        tools = AgentTools(session, "black")
        before = session.snapshot()
        for target in ([8, 6], [3, 2], [True, 1], [3]):
            result = tools.call("move_piece", {"source": [2, 1], "target": target,
                                              "revision": session.revision, "request_id": "invalid"})
            self.assertFalse(result["ok"])
            self.assertEqual(session.snapshot(), before)
        self.assertEqual(tools.call("move_piece", {"source": [2, 1], "target": [3, 1],
                                                   "revision": -1, "request_id": "stale"})["code"], "revision_mismatch")
        self.assertEqual(session.snapshot(), before)

    def test_direct_move_handles_existing_selection_and_elephant_bonus(self):
        session = playing()
        tools = AgentTools(session, "black")
        tools.apply_action("select_piece:at=2,3", session.revision)
        result = tools.call("move_piece", {"source": [2, 3], "target": [3, 3],
                                          "revision": session.revision, "request_id": "elephant"})
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["executed"]), 1)
        self.assertEqual(session.flow.phase, "pending_elephant_bonus")
        self.assertEqual(session.game.current_ap, 4)
        self.assertEqual(tools.get_legal_actions()["moves"], [])

    def test_direct_move_respects_lion_control_and_new_piece_restrictions(self):
        session = playing()
        session.game.set_piece((4, 3), Resource("white", "squirrel"))
        session.game.set_piece((4, 4), Resource("black", "lion"))
        session.game.mark_new_piece((2, 1))
        session.refresh_players()
        tools = AgentTools(session, "black")
        legal = tools.get_legal_actions()["moves"]
        self.assertFalse(any(move["source"] == [2, 1] for move in legal))
        self.assertIn({"source": [4, 3], "target": [5, 3], "ap": 2}, legal)
        result = tools.call("move_piece", {"source": [4, 3], "target": [5, 3],
                                          "revision": session.revision, "request_id": "controlled"})
        self.assertTrue(result["ok"])
        self.assertEqual(session.game.current_ap, 3)
        self.assertEqual(session.game.get_piece((5, 3)).owner, "white")

    def test_direct_move_is_unavailable_during_human_phases_or_other_side(self):
        session = GameSession.new_game(2)
        tools = AgentTools(session, "black")
        args = {"source": [2, 1], "target": [3, 1], "revision": session.revision, "request_id": "blocked"}
        self.assertFalse(tools.call("move_piece", args)["ok"])
        session.flow.phase = "time_wish"
        self.assertEqual(tools.call("move_piece", args)["code"], "human_only_phase")
        session = playing()
        self.assertEqual(AgentTools(session, "white").get_legal_actions()["moves"], [])
        self.assertEqual(AgentTools(session, "white").call("move_piece", args)["code"], "awaiting_other_side")

    def test_time_wish_is_hidden_and_rejected_by_model_tools(self):
        for mode in (2, 3):
            session = playing(mode)
            session.flow.phase = "time_wish"
            session.flow.time_wish_winner = "white"
            for side in ("black", "white"):
                tools = AgentTools(session, side)
                before = session.snapshot()
                self.assertEqual(tools.call("get_legal_actions")["actions"], [])
                self.assertEqual(tools.call("apply_action", {
                    "action_id": "time_wish:side=black", "revision": session.revision,
                })["code"], "human_only_phase")
                self.assertEqual(tools.call("submit_plan", {
                    "actions": [{"type": "time_wish", "params": {"side": "black"}}],
                    "revision": session.revision, "request_id": "wish",
                })["code"], "human_only_phase")
                self.assertEqual(session.snapshot(), before)

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
            source.flow.phase = "time_wish"
            source.flow.time_wish_winner = "white"
            source.select_camp("black")
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
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "illegal_action")
        self.assertEqual(len(result["executed"]), 2)
        self.assertEqual(result["stop_reason"], "illegal_action")
        self.assertNotIn("state", result)
        self.assertEqual(result["revision_before"], revision)
        self.assertEqual(result["revision_after"], session.revision)
        self.assertNotIn("g3_turn", json.dumps(result))
        self.assertIsNone(session.game.get_piece((2, 1)))
        self.assertIsNotNone(session.game.get_piece((3, 1)))
        after = session.snapshot()
        self.assertTrue(tools.call("submit_plan", args)["replayed"])
        self.assertEqual(session.snapshot(), after)
        args["actions"].pop()
        self.assertEqual(tools.call("submit_plan", args)["code"], "request_conflict")

    def test_plan_rejected_first_step_is_failure_without_board_or_ap_change(self):
        session = playing()
        tools = AgentTools(session, "black")
        before = session.snapshot()
        result = tools.call("submit_plan", {"revision": session.revision, "request_id": "rejected-plan",
                            "actions": [{"type": "move", "params": {"from": [2, 1], "to": [99, 99]}}]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["executed"], [])
        self.assertEqual(result["stop_reason"], "illegal_action")
        after = session.snapshot()
        for key in ("board", "current_ap", "revision", "phase"):
            self.assertEqual(after[key], before[key])

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
