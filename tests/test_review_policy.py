"""Action/review routing and private experiment memory; no remote API calls."""
import json
import tempfile
import unittest
from pathlib import Path
from agent_prompts import NG_PLUS_PROMPT
from gameengine import save_session_file, load_session_file
import test_model_workflows as workflows
from test_agent_play import response


class ReviewPolicyTests(unittest.TestCase):
    setUp = workflows.WorkflowTests.setUp
    check_turn = workflows.WorkflowTests.check_turn
    reply = workflows.WorkflowTests.reply
    move = workflows.WorkflowTests.move

    def test_action_request_cannot_write_condition_notes(self):
        self.play.next()
        names = {t["function"]["name"] for t in self.requests[-1].payload["tools"]}
        self.assertIn("record_intent", names)
        self.assertNotIn("update_notes", names)
        old = dict(self.session.agent_notes)
        self.reply(("update_notes", {"text": "Unverified claim"}))
        self.assertEqual(self.session.agent_notes, old)
        self.assertEqual(self.play.state, "stopped")

    def test_ng_plus_uses_mate_prompt_and_skips_reviews(self):
        self.session.game.set_time_token_owner(None)
        self.check_turn()
        self.notes.tick()
        self.assertFalse(self.notes.busy)
        self.assertEqual(self.requests, [])
        self.play.next()
        payload = self.requests[-1].payload
        self.assertEqual(payload["messages"][0]["content"], NG_PLUS_PROMPT)
        names = {t["function"]["name"] for t in payload["tools"]}
        self.assertFalse(names & {"update_notes", "record_intent", "get_rule_images", "get_notes"})
        context = json.loads(payload["messages"][-1]["content"])
        self.assertNotIn("public_condition_evidence", context)
        self.assertNotIn("get_notes", context)

    def test_all_conditions_true_skips_review_even_before_wish(self):
        self.check_turn()
        self.session.public_check_events[-1]["conditions"] = [True] * 4
        self.notes.tick()
        self.assertEqual(self.requests, [])
        self.assertFalse(self.notes.busy)

    def test_winner_cancels_active_review_and_queued_reviews(self):
        self.check_turn()
        self.notes.tick()
        request = self.requests[-1]
        old = dict(self.session.agent_notes)
        request.response = response(("update_notes", {"text": "Late result"}))
        self.session.end_session("check_victory", loser="white")
        self.notes.tick()
        self.assertTrue(request.cancelled)
        self.assertFalse(self.notes.busy)
        self.assertEqual(self.session.agent_notes, old)
        self.assertEqual(len(self.requests), 1)

    def test_intents_are_private_saved_and_included_in_review(self):
        tools = self.app.agent_tools_by_side["black"]
        old = tools.get_notes()["notes"]
        revision = self.session.revision
        text = "Condition 3: move one squirrel; predict false becomes true."
        self.assertTrue(tools.call("record_intent", {"text": text})["ok"])
        self.assertEqual(tools.get_notes()["notes"], old)
        self.assertEqual(self.session.revision, revision)
        self.session.flow.agent_intents["white"] = [{"turn_number": 1, "text": "White private", "revision": revision}]
        with tempfile.TemporaryDirectory() as directory:
            path = save_session_file(self.session, Path(directory) / "intent.json")
            restored = load_session_file(path)
        self.assertEqual(restored.flow.agent_intents, self.session.flow.agent_intents)
        self.check_turn()
        self.notes.tick()
        black_payload = self.requests[-1].payload
        self.assertIn(text, str(black_payload))
        self.assertNotIn("White private", str(black_payload))
        self.assertNotIn("agent_intents", str(self.session.flow.public_check_archive))
        self.requests[-1].response = response(("update_notes", {"text": "Revised hypothesis"}))
        self.notes.tick()
        self.assertIn("White private", str(self.requests[-1].payload))
        self.assertNotIn(text, str(self.requests[-1].payload))
        self.assertEqual(self.session.agent_notes["black"], "Revised hypothesis")

    def test_direct_start_rechecks_review_eligibility(self):
        self.check_turn()
        event = dict(self.session.public_check_events[-1])
        event["time_token_owner"] = None
        self.notes.start(self.session, "black", event)
        self.assertEqual(self.requests, [])
        event["time_token_owner"] = "black"
        event["conditions"] = [True]
        self.notes.start(self.session, "black", event)
        self.assertEqual(self.requests, [])

    def test_opponent_cannot_record_intents_on_your_turn(self):
        result = self.app.agent_tools_by_side["white"].call("record_intent", {"text": "Wrong turn"})
        self.assertFalse(result["ok"])
        self.assertEqual(self.session.flow.agent_intents, {})

    def test_nonwinning_normal_check_still_reviews_both_llm_sides(self):
        self.check_turn()
        self.notes.tick()
        self.assertIsNotNone(self.notes.active)
        self.assertEqual(len(self.notes.queue), 1)
        self.assertEqual(len(self.requests), 1)

    def test_step_does_not_reuse_auto_flags_or_dispatch_without_next(self):
        self.play.auto_running = self.play.continuation = True
        self.play.decision_side = "black"
        self.play.dispatch()
        self.play.tick()
        self.assertEqual(self.requests, [])
        self.play.next()
        self.assertEqual(len(self.requests), 1)
        self.reply(("get_notes", {}))
        for _ in range(3):
            self.play.tick()
        self.assertEqual(len(self.requests), 2)
        self.reply(self.move())
        for _ in range(3):
            self.play.tick()
        self.assertEqual(len(self.requests), 2)
        self.assertIsNone(self.play.step_permit)

    def test_step_human_turn_reviews_automatically_then_waits_for_next(self):
        self.app.gui.player_types["black"] = "human"
        self.check_turn()
        # Even Next before the reviewer has collected the event cannot jump ahead.
        self.play.next()
        self.assertEqual(self.requests, [])
        self.notes.tick()
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(self.play.request)
        self.play.next()
        self.requests[-1].response = response(("update_notes", {"text": "Reviewed conditions"}))
        self.notes.tick()
        revision = self.session.revision
        for _ in range(3):
            self.play.tick()
            self.notes.tick()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.session.revision, revision)
        self.play.next()
        self.assertEqual(len(self.requests), 2)
        self.assertIn("Reviewed conditions", str(self.requests[-1].payload))

    def test_review_waits_for_action_reply_and_pending_calls(self):
        self.play.next()
        self.requests[-1].response = response(("get_notes", {}), ("get_legal_actions", {}))
        self.play.tick()
        self.check_turn()
        self.notes.tick()
        self.assertTrue(self.notes.queue)
        self.assertIsNone(self.notes.active)
        self.assertTrue(self.play.pending)
        self.play.tick()  # External board change invalidates the remaining calls.
        self.notes.tick()
        self.assertIsNotNone(self.notes.active)
        self.assertIsNone(self.play.request)
        self.assertFalse(self.play.pending)
        self.play.next()
        self.assertEqual(len(self.requests), 2)
        for side in ("black", "white"):
            self.assertEqual(self.notes.active["side"], side)
            self.requests[-1].response = response(("update_notes", {"text": side + " reviewed"}))
            self.notes.tick()
            self.play.tick()
            self.assertIsNone(self.play.request)
        self.assertEqual(len(self.requests), 3)
        self.assertFalse(self.notes.busy)
        self.play.next()
        self.assertEqual(len(self.requests), 4)

    def test_review_waits_for_inflight_action_network_request(self):
        self.play.next()
        action = self.requests[-1]
        self.check_turn()
        self.notes.tick()
        self.assertIsNone(self.notes.active)
        self.assertEqual(len(self.requests), 1)
        action.response = response(("get_notes", {}))
        self.play.tick()
        self.notes.tick()
        self.assertIsNone(self.play.request)
        self.assertIsNotNone(self.notes.active)
        self.assertEqual(len(self.requests), 2)

    def test_images_and_condition_evidence_only_enter_review_requests(self):
        self.play.next()
        payload = self.requests[-1].payload
        names = {t["function"]["name"] for t in payload["tools"]}
        self.assertNotIn("get_rule_images", names)
        self.assertNotIn("image_url", str(payload))
        context = json.loads(payload["messages"][-1]["content"])
        self.assertNotIn("last_completed_check", context["get_public_state"])
        self.assertNotIn("visible_condition_feedback", context["get_public_state"])
        self.assertIn("This is an action request", payload["messages"][0]["content"])
        self.reply(("get_rule_images", {}))
        self.assertEqual(self.play.state, "stopped")
        self.check_turn()
        self.notes.tick()
        payload = self.requests[-1].payload
        self.assertIn("image_url", str(payload))
        self.assertIn("completed_check", str(payload))
        self.assertEqual([t["function"]["name"] for t in payload["tools"]], ["update_notes"])

    def test_switch_from_auto_to_step_holds_followup_and_excludes_idle_timeout(self):
        from unittest.mock import patch
        self.app.gui.play_control_mode = "auto"
        with patch("agent_play.time.monotonic", return_value=100):
            self.play.next()
            self.reply(("get_notes", {}))
        self.assertTrue(self.play.continuation)
        self.app.gui.play_control_mode = "step"
        with patch("agent_play.time.monotonic", return_value=1000):
            self.play.tick()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.play.state, "stopped")
        self.assertIn("mode changed", self.play.message)
        self.assertIsNone(self.play.step_permit)
        self.assertFalse(self.play.continuation)
        self.play.next()
        self.assertEqual(len(self.requests), 2)

    def test_custom_prompts_route_to_matching_requests_with_tool_limits(self):
        self.app.gui.prompts = {"system_prompt": "行动中文", "notes_prompt": "复盘中文", "ng_plus_prompt": "NG+中文"}
        self.play.next()
        self.assertEqual(self.requests[-1].payload["messages"][0]["content"], "行动中文")
        self.assertNotIn("update_notes", self.play.allowed_tools)
        self.reply(("get_notes", {}))
        self.check_turn()
        self.play.tick()  # The external turn change revokes the unfinished decision.
        self.notes.tick()
        self.assertEqual(self.requests[-1].payload["messages"][0]["content"], "复盘中文")
        self.assertEqual([t["function"]["name"] for t in self.requests[-1].payload["tools"]], ["update_notes"])
        self.notes.cancel()
        self.session.game.set_time_token_owner(None)
        self.play.next()
        self.assertEqual(self.requests[-1].payload["messages"][0]["content"], "NG+中文")
        self.assertFalse(self.play.allowed_tools & {"get_rule_images", "get_notes", "update_notes", "record_intent"})
