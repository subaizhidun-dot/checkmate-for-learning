"""Request gates and pause/stop boundaries without contacting an external API."""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from agent_play import AgentPlayController, ChatRequest, completion_url, MAX_DECISION_REQUESTS
from agenttools import AgentTools
from gameengine import GameSession
from local_settings import default_profile
from basicgame import Resource


def response(*calls, usage=10, content=None):
    return {"data": {"choices": [{"message": {"content": content,
                     "reasoning_content": "Provider-returned reasoning", "tool_calls": [
                         {"id": f"call{index}", "type": "function", "function": {
                             "name": name, "arguments": json.dumps(arguments)}}
                         for index, (name, arguments) in enumerate(calls)]}}], "usage": {"total_tokens": usage}}}


class FakeRequest:
    def __init__(self, profile, payload):
        self.profile, self.payload = profile, payload
        self.response = None
        self.cancelled = False

    def poll(self):
        returned, self.response = self.response, None
        return returned

    def cancel(self):
        self.cancelled = True


class AppStub:
    def __init__(self, choose_token=True):
        session = GameSession.new_game(3, resolve_checks_immediately=True)
        if choose_token:
            session.submit("choose_initial_token:side=black", session.revision)
        self.state = SimpleNamespace(session=session)
        profile = {**default_profile(), "endpoint": "https://example.test/v1", "model": "configured-model"}
        self.gui = SimpleNamespace(agent_play_mode=True, player_types={"black": "llm", "white": "llm"},
                                   api_profiles={"black": dict(profile), "white": dict(profile)},
                                   llm_color="black", play_control_mode="step", append_exposure_output=self.output)
        self.agent_tools_by_side = {side: AgentTools(session, side, image_provider=lambda: [
            {"index": 1, "mime_type": "image/png", "data": "test-image"}]) for side in ("black", "white")}
        self.outputs = []
        self.progress = []
        self.saved = 0

    def output(self, text):
        self.outputs.append(text)

    def begin_llm_request(self, request_id, side):
        session = self.state.session
        session.llm_usage.begin_request(request_id, side, session.turn_number)
        return session

    def complete_llm_request(self, anchor, request_id, **kwargs):
        if anchor is not self.state.session:
            return False
        self.outputs.append(kwargs)
        return anchor.llm_usage.finish_request(request_id, kwargs.get("usage"), kwargs.get("status", "completed"))

    def update_llm_request(self, anchor, request_id, **kwargs):
        if anchor is not self.state.session:
            return False
        self.progress.append(kwargs)
        return True

    def refresh_exposure_notes(self):
        pass

    def persist(self):
        self.saved += 1

    def report_agent_notice(self, text):
        self.outputs.append("Notice | " + text)


class AgentPlayTests(unittest.TestCase):
    def test_time_wish_cancels_request_and_holds_auto_for_human(self):
        self.app.gui.play_control_mode = "auto"
        self.controller.next()
        request = self.requests[-1]
        session = self.app.state.session
        session.flow.phase = "time_wish"
        session.flow.time_wish_winner = "white"
        self.controller.pending = [{"id": "late", "type": "function", "function": {
            "name": "apply_action", "arguments": json.dumps({
                "action_id": "time_wish:side=black", "revision": session.revision})}}]
        self.controller.tick()
        self.assertTrue(request.cancelled)
        self.assertEqual(self.controller.pending, [])
        self.assertFalse(self.controller.auto_running)
        self.assertEqual(self.controller.message, "Time wish: human only")
        self.controller.next()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(session.game.time_token_owner, "black")
        self.assertFalse(session.can_start_new_game_plus())

    def setUp(self):
        self.app = AppStub()
        self.requests = []
        def factory(profile, payload):
            request = FakeRequest(profile, payload)
            self.requests.append(request)
            return request
        self.controller = AgentPlayController(self.app, factory)

    def start_response(self, *calls):
        self.controller.next()
        self.requests[-1].response = response(*calls)
        self.controller.tick()

    def finish_response(self, *calls, content=None):
        if self.controller.request is None:
            self.controller.next()
        self.requests[-1].response = response(*calls, content=content)
        self.controller.tick()
        while self.controller.pending:
            self.controller.tick()

    def test_step_reads_notes_then_selects_and_moves_with_explicit_clicks(self):
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        self.controller.next()
        for name in ("get_notes", "get_legal_actions"):
            self.finish_response((name, {}))
            count = len(self.requests)
            self.controller.tick()
            self.assertEqual(len(self.requests), count)
            self.controller.next()
        messages = self.requests[-1].payload["messages"]
        self.assertFalse(any(isinstance(m.get("content"), list) for m in messages))
        assistants = [m for m in messages if m["role"] == "assistant"]
        self.assertEqual(len(assistants), 2)
        self.assertTrue(all(m["reasoning_content"] == "Provider-returned reasoning" for m in assistants))
        self.finish_response(("apply_action", {"action_id": "select_piece:at=2,1", "revision": session.revision}))
        self.assertEqual(session.flow.phase, "select_target")
        self.assertEqual(session.game.current_ap, 3)
        self.assertEqual(self.controller.state, "ready")
        self.controller.tick()
        self.assertEqual(len(self.requests), 3)
        self.controller.next()
        self.finish_response(("apply_action", {"action_id": "move:from=2,1;to=3,1", "revision": session.revision}))
        self.assertEqual(session.game.get_piece((3, 1)).owner, "black")
        self.assertEqual(session.game.current_ap, 2)
        self.assertEqual(self.controller.state, "ready")
        self.controller.tick()
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(session.llm_usage.summary()["requests"], 4)
        self.assertEqual(session.llm_usage.summary()["llm_turns"], 1)
        self.assertEqual(session.llm_usage.summary()["total_tokens"], 40)

    def test_direct_move_finishes_one_step_without_selection_request(self):
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        self.controller.next()
        self.finish_response(("move_piece", {"source": [2, 1], "target": [3, 1],
                                              "revision": session.revision, "request_id": "direct"}))
        self.controller.tick()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.controller.state, "ready")
        self.assertEqual(session.game.current_ap, 2)

    def test_elephant_bonus_waits_for_next_when_it_needs_another_request(self):
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        self.controller.next()
        self.finish_response(("move_piece", {"source": [2, 3], "target": [3, 3],
                                              "revision": session.revision, "request_id": "push"}))
        self.assertEqual(session.flow.phase, "pending_elephant_bonus")
        self.assertEqual(self.controller.state, "ready")
        self.controller.tick()
        self.finish_response(("apply_action", {"action_id": "place_elephant_bonus:at=2,3", "revision": session.revision}))
        self.assertEqual(session.game.get_piece((2, 3)).kind, "squirrel")
        self.assertEqual(session.game.current_ap, 2)
        self.assertEqual(self.controller.state, "ready")
        self.controller.tick()
        self.assertEqual(len(self.requests), 2)

    def test_resuming_an_elephant_bonus_stops_after_finishing_the_original_move(self):
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        self.app.agent_tools_by_side["black"].move_piece([2, 3], [3, 3], session.revision, "earlier")
        self.controller.next()
        self.finish_response(("apply_action", {"action_id": "skip_elephant_bonus", "revision": session.revision}))
        self.controller.tick()
        self.assertEqual(session.game.current_ap, 2)
        self.assertEqual(self.controller.state, "ready")
        self.assertEqual(len(self.requests), 1)

    def test_purchase_payment_and_placement_continue_until_committed(self):
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        session.game.set_piece((4, 3), Resource("black", "squirrel"))
        session.game.set_piece((4, 4), Resource("black", "squirrel"))
        session.refresh_players()
        self.controller.next()
        steps = ["buy_butterfly:at=3,3", "select_cost:at=2,1;kind=butterfly",
                 "select_cost:at=2,5;kind=butterfly", "select_cost:at=4,3;kind=butterfly",
                 "select_cost:at=4,4;kind=butterfly", "buy_butterfly:at=3,3"]
        for index, action_id in enumerate(steps):
            self.finish_response(("apply_action", {"action_id": action_id, "revision": session.revision}))
            self.assertEqual(self.controller.state, "ready")
            self.controller.tick()
        self.assertEqual(session.game.get_piece((3, 3)).kind, "butterfly")
        self.assertEqual(session.game.current_ap, 2)
        self.assertEqual(len(self.requests), 6)

    def test_sale_refunds_continue_until_the_sale_is_committed(self):
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        session.game.set_piece((4, 3), Resource("black", "butterfly"))
        session.refresh_players()
        self.controller.next()
        for index, action_id in enumerate(("sell_butterfly:at=4,3", "place_refund:at=4,3", "place_refund:at=4,4")):
            self.finish_response(("apply_action", {"action_id": action_id, "revision": session.revision}))
            self.assertEqual(self.controller.state, "ready")
            self.controller.tick()
        self.assertEqual(session.game.current_ap, 2)
        self.assertEqual(session.game.get_piece((4, 3)).kind, "squirrel")
        self.assertEqual(len(self.requests), 3)

    def test_pause_after_selection_holds_move_request_and_next_resumes_with_context(self):
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        self.controller.next()
        self.controller.pause()
        self.finish_response(("apply_action", {"action_id": "select_piece:at=2,1", "revision": session.revision}))
        self.controller.tick()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(session.flow.phase, "select_target")
        self.assertEqual(self.controller.state, "stopped")
        self.controller.next()
        self.assertTrue(any(m["role"] == "tool" for m in self.requests[-1].payload["messages"]))
        self.finish_response(("move_piece", {"source": [2, 1], "target": [3, 1],
                                              "revision": session.revision, "request_id": "resume"}))
        self.assertEqual(self.controller.state, "ready")

    def test_repeated_read_tools_stop_at_the_decision_request_limit(self):
        self.app.gui.play_control_mode = "auto"
        self.controller.next()
        for _ in range(MAX_DECISION_REQUESTS):
            self.finish_response(("get_notes", {}))
            self.controller.tick()
        self.assertEqual(len(self.requests), MAX_DECISION_REQUESTS)
        self.assertEqual(self.controller.state, "stopped")
        self.assertIn("request limit", self.controller.message)
        self.controller.tick()
        self.assertEqual(len(self.requests), MAX_DECISION_REQUESTS)
        self.controller.next()
        self.assertEqual(len(self.requests), MAX_DECISION_REQUESTS + 1)

    def test_stop_mid_reply_preserves_matched_tool_results_for_resumption(self):
        self.start_response(("get_notes", {}), ("update_notes", {"text": "Do not run"}))
        self.controller.tick()
        self.controller.stop()
        self.controller.next()
        messages = self.requests[-1].payload["messages"]
        tool_messages = [m for m in messages if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_messages], ["call0", "call1"])
        self.assertEqual(json.loads(tool_messages[1]["content"])["code"], "stopped")
        self.assertNotEqual(self.app.state.session.agent_notes["black"], "Do not run")

    def test_context_archives_decisions_for_the_previous_and_current_own_turn(self):
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        self.controller.next()
        self.finish_response(("record_intent", {"text": "First turn"}))
        self.controller.pause()
        self.controller.next()
        self.finish_response(("move_piece", {"source": [2, 1], "target": [3, 1],
                                              "revision": session.revision, "request_id": "first"}))
        self.assertEqual(self.controller.history["black"], [])
        first_history = list(self.controller.completed_history["black"])
        self.assertEqual(first_history[0]["operations"][0]["result"]["intent_recorded"], True)
        self.assertEqual(first_history[0]["operations"][1]["result"]["executed"][-1], "move:from=2,1;to=3,1")
        session.turn_number += 2
        self.controller.next()
        self.assertEqual(self.controller.previous_history["black"], first_history)
        self.finish_response(("move_piece", {"source": [3, 1], "target": [4, 1],
                                              "revision": session.revision, "request_id": "second"}))
        second_history = list(self.controller.completed_history["black"])
        session.turn_number += 2
        self.controller.next()
        self.assertEqual(self.controller.previous_history["black"], second_history)
        self.assertNotIn("First turn", json.dumps([m for m in self.requests[-1].payload["messages"] if m["role"] == "assistant"]))

    def test_active_decision_keeps_reasoning_and_matching_results_with_one_latest_state(self):
        self.controller.next()
        for name in ("get_public_state", "get_legal_actions", "get_gi_rules", "get_notes"):
            self.finish_response((name, {}))
            self.controller.next()
        messages = self.requests[-1].payload["messages"]
        encoded = json.dumps(messages)
        self.assertEqual(encoded.count('\\"board\\":'), 1)
        self.assertEqual(encoded.count('\\"moves\\":'), 1)
        self.assertEqual(encoded.count('\\"public_rules\\":'), 1)
        assistants = [message for message in messages if message["role"] == "assistant"]
        tools = [message for message in messages if message["role"] == "tool"]
        self.assertEqual(len(assistants), 4)
        self.assertEqual(len(tools), 4)
        self.assertTrue(all(message["reasoning_content"] == "Provider-returned reasoning" for message in assistants))
        for assistant, result in zip(assistants, tools):
            self.assertEqual(result["tool_call_id"], assistant["tool_calls"][0]["id"])
            self.assertIn("context_key", json.loads(result["content"]))
        self.assertEqual(self.controller.completed_history["black"], [])

    def test_completed_decision_starts_a_fresh_context_with_action_receipts(self):
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        self.controller.next()
        self.finish_response(("move_piece", {"source": [2, 1], "target": [3, 1],
                                              "revision": session.revision, "request_id": "archive"}))
        self.controller.next()
        messages = self.requests[-1].payload["messages"]
        self.assertFalse(any(message["role"] in {"assistant", "tool"} for message in messages))
        context = json.loads(messages[-1]["content"])
        receipt = context["completed_decisions"][0]["operations"][0]["result"]
        self.assertEqual(receipt["executed"][-1], "move:from=2,1;to=3,1")
        self.assertEqual(receipt["revision_before"], 1)
        self.assertEqual(receipt["revision_after"], session.revision)
        self.assertEqual(context["get_public_state"]["board"][1][3]["owner"], "black")
        self.assertNotIn("state", receipt)
        self.assertNotIn("reasoning_content", context)

    def test_empty_reasoning_field_survives_active_tool_followup(self):
        self.controller.next()
        returned = response(("get_notes", {}))
        returned["data"]["choices"][0]["message"]["reasoning_content"] = ""
        self.requests[-1].response = returned
        self.controller.tick()
        self.controller.tick()
        self.controller.tick()
        self.controller.next()
        assistant = next(message for message in self.requests[-1].payload["messages"] if message["role"] == "assistant")
        self.assertIn("reasoning_content", assistant)
        self.assertEqual(assistant["reasoning_content"], "")

    def test_cached_images_are_excluded_from_action_requests(self):
        self.controller.next()
        self.finish_response(("get_notes", {}))
        self.controller.cached_images["black"] = {1: {"index": 1, "mime_type": "image/png", "data": "test-image"}}
        self.controller.next()
        self.assertNotIn("image_url", json.dumps(self.requests[-1].payload))
        self.assertNotIn("test-image", json.dumps(self.requests[-1].payload))
        self.controller.stop(reset=True)
        self.assertEqual(self.controller.cached_images, {})
        self.assertEqual(self.controller.history, {})

    def test_stale_board_reply_stops_read_continuation_without_executing_tools(self):
        self.controller.next()
        session = self.app.state.session
        session.submit("select_piece:at=2,1", session.revision)
        self.finish_response(("get_notes", {}))
        self.assertEqual(self.controller.state, "stopped")
        self.controller.tick()
        self.assertEqual(len(self.requests), 1)

    def test_ordered_plan_is_one_step_and_can_contain_multiple_complete_moves(self):
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        self.controller.next()
        self.finish_response(("submit_plan", {"revision": session.revision, "request_id": "batch", "actions": [
            {"type": "select_piece", "params": {"at": [2, 1]}},
            {"type": "move", "params": {"from": [2, 1], "to": [3, 1]}},
            {"type": "select_piece", "params": {"at": [3, 1]}},
            {"type": "move", "params": {"from": [3, 1], "to": [4, 1]}},
        ]}))
        self.assertEqual(session.game.current_ap, 1)
        self.assertIsNotNone(session.game.get_piece((4, 1)))
        self.assertEqual(self.controller.state, "ready")
        self.controller.tick()
        self.assertEqual(len(self.requests), 1)

    def test_step_finishes_elephant_bonus_before_the_last_ap_changes_side(self):
        session = self.app.state.session
        self.controller.next()
        self.finish_response(("move_piece", {"source": [2, 3], "target": [3, 3],
                                              "revision": session.revision, "request_id": "last-ap"}))
        self.assertEqual(session.game.current_ap, 0)
        self.assertEqual(self.controller.state, "ready")
        self.controller.tick()
        self.finish_response(("apply_action", {"action_id": "skip_elephant_bonus", "revision": session.revision}))
        self.assertEqual(session.game.current_player, "white")
        self.assertEqual(self.controller.state, "ready")
        self.controller.tick()
        self.assertEqual(len(self.requests), 2)

    def test_butterfly_extra_turn_discards_remaining_calls_even_when_actor_is_same(self):
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        session.game.set_piece((4, 3), Resource("black", "butterfly"))
        session.refresh_players()
        self.controller.next()
        self.finish_response(("apply_action", {"action_id": "butterfly_extra_turn:at=4,3", "revision": session.revision}),
                             ("update_notes", {"text": "Must wait for the extra turn"}))
        self.assertEqual(session.game.current_player, "black")
        self.assertEqual(session.game.current_ap, 2)
        self.assertEqual(self.controller.state, "ready")
        self.assertNotEqual(session.agent_notes["black"], "Must wait for the extra turn")
        receipt = self.controller.completed_history["black"][-1]["operations"][0]["result"]
        self.assertEqual(receipt["stop_reason"], "extra_turn")
        self.controller.tick()
        self.assertEqual(len(self.requests), 1)


    def test_sent_system_prompt_states_win_goal_and_standard_turn_end_victory(self):
        from agent_prompts import SYSTEM_PROMPT
        self.controller.next()
        sent = self.requests[-1].payload["messages"][0]
        self.assertEqual(sent, {"role": "system", "content": SYSTEM_PROMPT})
        self.assertIn("Your goal is to win the game.", sent["content"])
        self.assertIn("In a standard game, win by satisfying all special victory conditions at the end of your turn.", sent["content"])

    def test_initial_token_choice_is_human_only_at_controller_and_tool_layers(self):
        self.app.state.session = GameSession.new_game(3)
        tools = AgentTools(self.app.state.session, "black")
        self.controller.next()
        self.assertEqual(self.requests, [])
        self.assertEqual(self.app.state.session.llm_usage.summary()["requests"], 0)
        self.assertEqual(tools.get_legal_actions()["actions"], [])
        self.assertFalse(tools.apply_action("choose_initial_token:side=black", 0)["ok"])
        result = tools.submit_plan([{"type": "choose_initial_token", "params": {"side": "black"}}], 0, "test")
        self.assertEqual(result["executed"], [])
        self.assertIsNone(self.app.state.session.game.time_token_owner)

    def test_step_records_intents_and_executes_calls_in_order(self):
        self.start_response(("record_intent", {"text": "First hypothesis"}),
                            ("record_intent", {"text": "Second hypothesis"}))
        self.assertEqual(self.controller.state, "running")
        self.assertNotEqual(self.app.state.session.agent_notes.get("black"), "First hypothesis")
        self.controller.tick()
        self.assertEqual(self.app.state.session.flow.agent_intents["black"][-1]["text"], "First hypothesis")
        self.controller.tick()
        self.assertEqual(self.app.state.session.flow.agent_intents["black"][-1]["text"], "Second hypothesis")
        self.assertEqual(self.controller.state, "ready")
        self.assertTrue(self.controller.received)
        self.controller.tick()
        self.assertEqual(len(self.requests), 1)
        self.controller.next()
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.app.state.session.llm_usage.summary()["total_tokens"], 10)

    def test_pause_drains_received_reply_then_holds_new_requests(self):
        self.app.gui.play_control_mode = "auto"
        self.controller.next()
        self.controller.pause()
        self.assertEqual(self.controller.state, "pausing")
        self.assertFalse(self.requests[0].cancelled)
        self.requests[0].response = response(("record_intent", {"text": "Received before pause"}),
                                              ("apply_action", {"action_id": "fast_time_token", "revision": 1}))
        self.controller.tick()
        self.assertEqual(self.controller.state, "pausing")
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(self.controller.state, "stopped")
        self.assertTrue(self.controller.paused)
        self.assertEqual(self.app.state.session.flow.agent_intents["black"][-1]["text"], "Received before pause")
        self.assertEqual(self.app.state.session.game.current_player, "white")
        self.controller.tick()
        self.assertEqual(len(self.requests), 1)

    def test_stop_cancels_pending_request_immediately(self):
        self.controller.next()
        self.controller.stop()
        self.assertTrue(self.requests[0].cancelled)
        self.assertEqual(self.controller.state, "stopped")
        self.assertEqual(self.app.state.session.llm_usage.requests[0]["status"], "stopped")
        self.requests[0].response = response(("update_notes", {"text": "Late result"}))
        self.controller.tick()
        self.assertNotEqual(self.app.state.session.agent_notes.get("black"), "Late result")

    def test_stop_discards_received_but_unexecuted_actions(self):
        self.start_response(("update_notes", {"text": "Must not execute"}))
        self.controller.stop()
        self.controller.tick()
        self.assertNotEqual(self.app.state.session.agent_notes.get("black"), "Must not execute")
        self.assertEqual(self.app.state.session.llm_usage.requests[0]["status"], "completed")

    def test_illegal_action_discards_remaining_reply(self):
        self.start_response(("apply_action", {"action_id": "nonexistent", "revision": 1}),
                            ("update_notes", {"text": "Should be skipped"}))
        self.controller.pause()
        self.controller.tick()
        self.assertEqual(self.controller.state, "stopped")
        self.assertNotEqual(self.app.state.session.agent_notes.get("black"), "Should be skipped")
        self.assertEqual(len(self.controller.history["black"]), 4)

    def test_auto_moves_to_other_llm_with_separate_context(self):
        self.app.gui.play_control_mode = "auto"
        self.start_response(("apply_action", {"action_id": "fast_time_token", "revision": 1}))
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(len(self.requests), 2)
        context = json.loads(self.requests[1].payload["messages"][-1]["content"])
        self.assertEqual(context["get_public_state"]["your_side"], "white")
        self.assertNotIn("g3_turn", context["get_public_state"])
        self.assertNotIn("condition_results", context["get_public_state"])

    def test_action_request_rejects_image_tools(self):
        self.start_response(("get_rule_images", {}))
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(self.controller.state, "stopped")
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("image_url", json.dumps(self.requests[-1].payload))
        result = json.loads(self.controller.history["black"][-1]["content"])
        self.assertFalse(result["ok"])

    def test_old_board_response_is_cancelled_and_new_board_is_untouched(self):
        self.controller.next()
        self.app.state.session = GameSession.new_game(2)
        self.requests[0].response = response(("update_notes", {"text": "Old game"}))
        self.controller.tick()
        self.assertTrue(self.requests[0].cancelled)
        self.assertEqual(self.app.state.session.agent_notes, {})
        self.assertEqual(self.controller.state, "idle")

    def test_text_only_reply_and_bad_response_do_not_loop_automatically(self):
        self.app.gui.play_control_mode = "auto"
        self.controller.next()
        self.requests[0].response = response(content="No tool call")
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(len(self.requests), 1)
        self.controller.next()
        self.requests[-1].response = {"data": {"choices": [{"message": None}]}}
        self.controller.tick()
        self.assertEqual(self.controller.state, "stopped")

    def test_winner_ends_read_loop_without_another_request(self):
        self.app.gui.play_control_mode = "auto"
        self.start_response(("get_notes", {}))
        self.controller.tick()
        self.assertEqual(self.controller.state, "running")
        self.app.state.session.end_session("mate", loser="white")
        self.controller.tick()
        self.assertEqual(self.controller.state, "stopped")
        self.assertIsNone(self.controller.side_to_play())
        self.assertEqual(len(self.requests), 1)

    def test_new_game_plus_can_start_without_initial_token_placement(self):
        previous = self.app.state.session
        previous.flow.phase = "time_wish"
        previous.flow.time_wish_winner = "white"
        previous.select_camp("black")
        session = GameSession.new_game(2, previous_session=previous)
        self.app.state.session = session
        self.app.agent_tools_by_side = {side: AgentTools(session, side) for side in ("black", "white")}
        self.assertEqual(session.flow.phase, "playing")
        self.assertIsNone(session.game.time_token_owner)
        self.controller.next()
        self.assertEqual(len(self.requests), 1)

    def test_output_limit_stops_without_executing_even_a_valid_tool_prefix(self):
        self.app.gui.play_control_mode = "auto"
        self.controller.next()
        returned = response(("update_notes", {"text": "Must not run a truncated reply"}), usage=4096)
        returned["data"]["choices"][0]["finish_reason"] = "length"
        self.requests[0].response = returned
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(self.controller.state, "stopped")
        self.assertEqual(len(self.requests), 1)
        self.assertNotEqual(self.app.state.session.agent_notes.get("black"), "Must not run a truncated reply")
        usage = self.app.state.session.llm_usage.requests[0]
        self.assertEqual(usage["status"], "failed")
        self.assertEqual(usage["total_tokens"], 4096)
        self.assertIn("raise token limit", self.controller.message)

    def test_streaming_option_controls_payload_and_usage_request(self):
        self.controller.next()
        self.assertTrue(self.requests[-1].payload["stream"])
        self.assertEqual(self.requests[-1].payload["stream_options"], {"include_usage": True})
        self.controller.stop()
        self.app.gui.api_profiles["black"]["streaming"] = False
        self.controller.next()
        self.assertFalse(self.requests[-1].payload["stream"])
        self.assertNotIn("stream_options", self.requests[-1].payload)

    def test_progress_keeps_request_live_until_final_reply_then_pause_finishes_actions(self):
        self.app.gui.play_control_mode = "auto"
        self.controller.next()
        request = self.requests[-1]
        saved = self.app.saved
        request.response = {"event": "progress", "reasoning": "检查棋盘 🦋", "reply": ""}
        self.controller.tick()
        self.assertIs(self.controller.request, request)
        self.assertFalse(request.cancelled)
        self.assertEqual(self.app.progress[-1]["reasoning"], "检查棋盘 🦋")
        self.assertEqual(self.app.state.session.llm_usage.requests[0]["status"], "pending")
        self.assertEqual(self.app.saved, saved)
        self.controller.pause()
        self.assertEqual(self.controller.state, "pausing")
        request.response = response(("record_intent", {"text": "Finished hypothesis"}))
        self.controller.tick()
        self.assertNotEqual(self.app.state.session.agent_notes.get("black"), "Finished hypothesis")
        self.controller.tick()
        self.assertEqual(self.app.state.session.flow.agent_intents["black"][-1]["text"], "Finished hypothesis")
        self.assertEqual(self.controller.state, "stopped")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.app.state.session.llm_usage.requests), 1)

    def test_stop_keeps_partial_reply_and_unknown_usage_without_actions(self):
        self.controller.next()
        request = self.requests[-1]
        request.response = {"event": "progress", "reasoning": "Still thinking", "reply": "Partial text"}
        self.controller.tick()
        self.controller.stop()
        self.assertTrue(request.cancelled)
        entry = self.app.state.session.llm_usage.requests[0]
        self.assertEqual(entry["status"], "stopped")
        self.assertIsNone(entry["total_tokens"])
        self.assertEqual(self.app.outputs[-1]["reasoning"], "Still thinking")
        self.assertIn("Partial text", self.app.outputs[-1]["reply"])
        self.assertIn("interrupted", self.app.outputs[-1]["reply"])
        self.assertEqual(self.controller.pending, [])

    def test_stream_failure_preserves_partial_text_and_reported_usage(self):
        self.controller.next()
        request = self.requests[-1]
        request.response = {"event": "progress", "reasoning": "Partial reasoning", "reply": ""}
        self.controller.tick()
        request.response = {"error": "Stream interrupted", "usage": {"total_tokens": 23}}
        self.controller.tick()
        self.assertEqual(self.app.outputs[-1]["reasoning"], "Partial reasoning")
        self.assertEqual(self.app.state.session.llm_usage.requests[0]["status"], "failed")
        self.assertEqual(self.app.state.session.llm_usage.requests[0]["total_tokens"], 23)
        self.assertEqual(self.controller.state, "stopped")

    def test_malformed_later_arguments_prevent_execution_of_valid_prefix(self):
        self.controller.next()
        returned = response(("update_notes", {"text": "Must not execute"}), ("get_notes", {}))
        returned["data"]["choices"][0]["message"]["tool_calls"][1]["function"]["arguments"] = '{"broken"'
        self.requests[-1].response = returned
        self.controller.tick()
        self.assertEqual(self.controller.pending, [])
        self.assertNotEqual(self.app.state.session.agent_notes.get("black"), "Must not execute")
        self.assertEqual(self.controller.state, "stopped")

    def test_stream_progress_for_replaced_game_is_discarded(self):
        self.controller.next()
        request = self.requests[-1]
        self.app.state.session = GameSession.new_game(1)
        count = len(self.app.progress)
        request.response = {"event": "progress", "reasoning": "Stale reply", "reply": ""}
        self.controller.tick()
        self.assertTrue(request.cancelled)
        self.assertEqual(len(self.app.progress), count)

    def test_truncated_stream_arguments_report_output_limit_and_never_execute(self):
        self.controller.next()
        returned = response(("update_notes", {"text": "Truncated"}))
        choice = returned["data"]["choices"][0]
        choice["finish_reason"] = "length"
        choice["message"]["tool_calls"][0]["function"]["arguments"] = '{"text":"Unfinished'
        self.requests[-1].response = returned
        self.controller.tick()
        self.assertIn("raise token limit", self.controller.message)
        self.assertEqual(self.controller.pending, [])
        self.assertEqual(self.app.state.session.llm_usage.requests[0]["status"], "failed")

    def test_content_filtered_reply_does_not_execute_valid_tool_prefix(self):
        self.controller.next()
        returned = response(("update_notes", {"text": "Must not execute"}))
        returned["data"]["choices"][0]["finish_reason"] = "content_filter"
        self.requests[-1].response = returned
        self.controller.tick()
        self.assertEqual(self.controller.state, "stopped")
        self.assertEqual(self.controller.pending, [])
        self.assertNotEqual(self.app.state.session.agent_notes.get("black"), "Must not execute")


class ChatTransportTests(unittest.TestCase):
    def test_local_http_round_trip_and_cancellation(self):
        arrived, release = threading.Event(), threading.Event()
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                received.append((self.path, self.headers.get("Authorization"),
                                 json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                arrived.set()
                if len(received) == 3:
                    release.wait(5)
                failed = received[-1][2]["model"] == "bad-fixture"
                body = json.dumps({"error": {"message": "Unknown model; credential fixture-key"}}
                                  if failed else response(content="Local fixture")["data"]).encode()
                try:
                    self.send_response(400 if failed else 200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (OSError, ConnectionError):
                    pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        profile = {**default_profile(), "endpoint": f"http://127.0.0.1:{server.server_port}/v1", "api_key": "fixture-key"}
        request = None
        try:
            request = ChatRequest(profile, {"model": "fixture", "messages": []})
            deadline = time.monotonic() + 8
            result = None
            while result is None and time.monotonic() < deadline:
                result = request.poll()
                if result is None:
                    time.sleep(0.02)
            self.assertEqual(result["data"]["usage"]["total_tokens"], 10)
            self.assertEqual(received[0][0], "/v1/chat/completions")
            self.assertEqual(received[0][1], "Bearer fixture-key")
            request = ChatRequest(profile, {"model": "bad-fixture", "messages": []})
            deadline = time.monotonic() + 8
            result = None
            while result is None and time.monotonic() < deadline:
                result = request.poll()
                if result is None:
                    time.sleep(0.02)
            self.assertIn("HTTP 400", result["error"])
            self.assertIn("Unknown model", result["error"])
            self.assertNotIn("fixture-key", result["error"])
            arrived.clear()
            request = ChatRequest(profile, {"model": "fixture", "messages": []})
            self.assertTrue(arrived.wait(8))
            request.cancel()
            self.assertFalse(request.process.is_alive())
            request = None
        finally:
            if request is not None and request.process.is_alive():
                request.cancel()
            release.set()
            server.shutdown()
            server.server_close()

    def test_endpoint_normalization(self):
        self.assertEqual(completion_url("https://example.test/v1/"), "https://example.test/v1/chat/completions")
        self.assertEqual(completion_url("https://example.test/v1/chat/completions"), "https://example.test/v1/chat/completions")
        for url in ("", "file:///tmp", "https://secret@example.test/v1", "https://example.test/v1?key=secret"):
            with self.assertRaises(ValueError):
                completion_url(url)
