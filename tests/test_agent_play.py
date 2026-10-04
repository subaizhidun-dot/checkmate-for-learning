"""Request gates and pause/stop boundaries without contacting an external API."""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from agent_play import AgentPlayController, ChatRequest, completion_url
from agenttools import AgentTools
from gameengine import GameSession
from local_settings import default_profile


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
        return self.response

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

    def refresh_exposure_notes(self):
        pass

    def persist(self):
        self.saved += 1

    def report_agent_notice(self, text):
        self.outputs.append("Notice | " + text)


class AgentPlayTests(unittest.TestCase):
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

    def test_step_waits_after_one_request_and_executes_calls_in_order(self):
        self.start_response(("update_notes", {"text": "First hypothesis"}),
                            ("update_notes", {"text": "Second hypothesis"}))
        self.assertEqual(self.controller.state, "running")
        self.assertNotEqual(self.app.state.session.agent_notes.get("black"), "First hypothesis")
        self.controller.tick()
        self.assertEqual(self.app.state.session.agent_notes["black"], "First hypothesis")
        self.controller.tick()
        self.assertEqual(self.app.state.session.agent_notes["black"], "Second hypothesis")
        self.assertEqual(self.controller.state, "ready")
        self.assertTrue(self.controller.received)
        self.controller.tick()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.app.state.session.llm_usage.summary()["total_tokens"], 10)

    def test_pause_drains_received_reply_then_holds_new_requests(self):
        self.app.gui.play_control_mode = "auto"
        self.controller.next()
        self.controller.pause()
        self.assertEqual(self.controller.state, "pausing")
        self.assertFalse(self.requests[0].cancelled)
        self.requests[0].response = response(("update_notes", {"text": "Received before pause"}),
                                              ("apply_action", {"action_id": "fast_time_token", "revision": 1}))
        self.controller.tick()
        self.assertEqual(self.controller.state, "pausing")
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(self.controller.state, "stopped")
        self.assertTrue(self.controller.paused)
        self.assertEqual(self.app.state.session.agent_notes["black"], "Received before pause")
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
        self.assertEqual(len(self.controller.history["black"]), 3)

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

    def test_image_tool_inserts_actual_image_content_in_next_request(self):
        self.start_response(("get_rule_images", {}))
        self.controller.tick()
        self.controller.next()
        image_messages = [message for message in self.requests[-1].payload["messages"] if isinstance(message.get("content"), list)]
        self.assertEqual(image_messages[0]["content"][1]["image_url"]["url"], "data:image/png;base64,test-image")

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

    def test_winner_ends_ready_blinking_without_another_request(self):
        self.start_response(("get_notes", {}))
        self.controller.tick()
        self.assertEqual(self.controller.state, "ready")
        self.app.state.session.end_session("mate", loser="white")
        self.controller.tick()
        self.assertEqual(self.controller.state, "stopped")
        self.assertIsNone(self.controller.side_to_play())
        self.assertEqual(len(self.requests), 1)

    def test_new_game_plus_can_start_without_initial_token_placement(self):
        previous = self.app.state.session
        previous.end_session("mate", loser="white")
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
