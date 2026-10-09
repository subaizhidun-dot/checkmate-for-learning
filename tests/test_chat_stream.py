"""Fragmented SSE, final usage, interruption and live worker boundaries."""
import io
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from agent_play import AgentPlayController
from chat_stream import ChatStream, read_chat_stream
from test_agent_play import AppStub


def event(delta=None, finish=None, usage=None):
    return {"choices": [] if delta is None and finish is None else [
        {"index": 0, "delta": delta or {}, "finish_reason": finish}], "usage": usage}


def encode_event(chunk):
    return ("data: " + json.dumps(chunk, ensure_ascii=False) + "\r\n\r\n").encode("utf-8")


def stream_bytes(*chunks, done=True):
    return b": keep-alive\r\n\r\n" + b"".join(encode_event(chunk) for chunk in chunks) + (
        b"data: [DONE]\r\n\r\n" if done else b"")


class StreamParserTests(unittest.TestCase):
    def test_interleaved_tool_arguments_unicode_reasoning_and_final_usage(self):
        chunks = [
            event({"role": "assistant", "reasoning_content": "检查棋盘 🦋"}),
            event({"tool_calls": [{"index": 1, "id": "b", "type": "function",
                                   "function": {"name": "get_notes", "arguments": "{"}},
                                  {"index": 0, "id": "a", "type": "function",
                                   "function": {"name": "update_notes", "arguments": '{"text":"'}}]}),
            event({"content": "我的回复", "tool_calls": [
                {"index": 0, "function": {"arguments": '假设"}'}},
                {"index": 1, "function": {"arguments": "}"}}]}),
            event({}, "tool_calls"), event(usage={"prompt_tokens": 80, "completion_tokens": 35, "total_tokens": 115}),
        ]
        progress = []
        data = read_chat_stream(io.BytesIO(stream_bytes(*chunks)), progress.append)
        message = data["choices"][0]["message"]
        self.assertEqual(message["reasoning_content"], "检查棋盘 🦋")
        self.assertEqual(message["content"], "我的回复")
        self.assertEqual([call["id"] for call in message["tool_calls"]], ["a", "b"])
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"text": "假设"})
        self.assertEqual(data["usage"]["total_tokens"], 115)
        self.assertEqual("".join(item["reasoning"] for item in progress), "检查棋盘 🦋")
        self.assertEqual("".join(item["reply"] for item in progress), "我的回复")

    def test_end_requires_both_finish_reason_and_done_marker(self):
        for raw in (stream_bytes(event({"content": "Partial"}), done=True),
                    stream_bytes(event({"content": "Partial"}), event({}, "stop"), done=False)):
            progress = []
            with self.assertRaises(ValueError):
                read_chat_stream(io.BytesIO(raw), progress.append)
            self.assertEqual(progress[0]["reply"], "Partial")

    def test_missing_usage_remains_unknown(self):
        data = read_chat_stream(io.BytesIO(stream_bytes(event({"content": "OK"}), event({}, "stop"))), lambda _: None)
        self.assertIsNone(data["usage"])

    def test_length_preserves_truncated_arguments_for_controller_to_reject(self):
        data = read_chat_stream(io.BytesIO(stream_bytes(event({"tool_calls": [
            {"index": 0, "id": "a", "function": {"name": "update_notes", "arguments": '{"text":'}}]}),
            event({}, "length"), event(usage={"total_tokens": 100}))), lambda _: None)
        self.assertEqual(data["choices"][0]["finish_reason"], "length")
        self.assertEqual(data["usage"]["total_tokens"], 100)

    def test_multiline_sse_data_and_comments(self):
        raw = b'\xef\xbb\xbf: ping\n\nevent: message\nid: 1\ndata: {"choices":\ndata: [{"index":0,"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        data = read_chat_stream(io.BytesIO(raw), lambda _: None)
        self.assertEqual(data["choices"][0]["message"]["content"], "OK")

    def test_malformed_events_and_provider_errors_fail_without_result(self):
        bad = [b"data: {broken\n\n", encode_event({"error": {"message": "Provider failed"}}),
               encode_event(event({"tool_calls": [{"index": -1}]})), encode_event(event({"content": 42}))]
        for raw in bad:
            with self.assertRaises(ValueError):
                read_chat_stream(io.BytesIO(raw), lambda _: None)

    def test_raw_stream_size_is_bounded_including_keep_alive(self):
        with patch("chat_stream.MAX_RESPONSE_BYTES", 64):
            with self.assertRaisesRegex(ValueError, "too large"):
                read_chat_stream(io.BytesIO(b":" + b"x" * 65 + b"\n\n"), lambda _: None)


class LiveStreamingTests(unittest.TestCase):
    def setUp(self):
        self.release = threading.Event()
        self.received = []
        release, received = self.release, self.received

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append(payload)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                name, arguments = "record_intent", '{"text":"Final hypothesis"}'
                if payload["model"] == "decision-fixture" and len(received) > 1:
                    context = json.loads(payload["messages"][-1]["content"])
                    name = "move_piece"
                    arguments = json.dumps({"source": [2, 1], "target": [3, 1],
                                            "revision": context["get_public_state"]["revision"],
                                            "request_id": "streamed-move"})
                initial = stream_bytes(event({"reasoning_content": "检查棋盘 🦋", "content": "Partial reply",
                    "tool_calls": [{"index": 0, "id": "fixture-call", "type": "function",
                                    "function": {"name": name, "arguments": arguments}}]}), done=False)
                try:
                    # Split transport writes inside UTF-8 characters and SSE records.
                    for offset in range(0, len(initial), 3):
                        self.wfile.write(initial[offset:offset + 3])
                    self.wfile.flush()
                    release.wait(8)
                    if payload["model"] == "malformed":
                        self.wfile.write(b"data: {broken\n\n")
                    else:
                        self.wfile.write(stream_bytes(event({}, "tool_calls"),
                            event(usage={"prompt_tokens": 80, "completion_tokens": 35, "total_tokens": 115}),
                            done=payload["model"] != "drop"))
                    self.wfile.flush()
                except (OSError, ConnectionError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.app = AppStub()
        self.app.gui.api_profiles["black"].update(endpoint=f"http://127.0.0.1:{self.server.server_port}", model="fixture")
        self.controller = AgentPlayController(self.app)

    def tearDown(self):
        self.controller.stop()
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 8
        while not predicate() and time.monotonic() < deadline:
            self.controller.tick()
            time.sleep(0.01)
        self.assertTrue(predicate(), "Timed out waiting for local fixture")

    def start_until_progress(self, model="fixture"):
        self.app.gui.api_profiles["black"]["model"] = model
        revision = self.app.state.session.revision
        self.controller.next()
        self.wait_for(lambda: bool(self.controller.partial_reasoning))
        self.assertEqual(self.app.state.session.revision, revision)
        self.assertEqual(self.controller.pending, [])
        self.assertEqual(self.app.state.session.flow.agent_intents, {})
        return self.controller.request

    def test_text_arrives_before_finish_and_only_complete_reply_executes_tools(self):
        request = self.start_until_progress()
        self.assertTrue(request.process.is_alive())
        self.assertTrue(self.received[0]["stream"])
        self.assertEqual(self.received[0]["stream_options"], {"include_usage": True})
        self.assertEqual(self.app.progress[-1]["reasoning"], "检查棋盘 🦋")
        self.assertEqual(self.app.saved, 0)
        self.release.set()
        self.wait_for(lambda: self.controller.request is None)
        self.assertEqual(self.app.state.session.flow.agent_intents, {})
        self.controller.tick()
        self.assertEqual(self.app.state.session.flow.agent_intents["black"][-1]["text"], "Final hypothesis")
        self.assertEqual(self.app.state.session.llm_usage.requests[0]["total_tokens"], 115)
        self.assertEqual(len(self.app.state.session.llm_usage.requests), 1)
        assistant = next(message for message in self.controller.history["black"] if message["role"] == "assistant")
        self.assertEqual(assistant["reasoning_content"], "检查棋盘 🦋")

    def test_auto_streamed_tool_result_continues_to_a_move(self):
        self.app.gui.play_control_mode = "auto"
        self.start_until_progress("decision-fixture")
        self.release.set()
        self.wait_for(lambda: self.controller.state == "ready")
        self.assertEqual(len(self.received), 2)
        messages = self.received[1]["messages"]
        assistant = next(message for message in messages if message["role"] == "assistant")
        self.assertEqual(assistant["reasoning_content"], "检查棋盘 🦋")
        self.assertTrue(any(message["role"] == "tool" for message in messages))
        session = self.app.state.session
        self.assertIsNone(session.game.get_piece((2, 1)))
        self.assertEqual(session.game.get_piece((3, 1)).owner, "black")
        self.assertEqual(session.game.current_player, "white")
        self.assertEqual(session.llm_usage.summary()["requests"], 2)
        self.assertEqual(session.llm_usage.summary()["total_tokens"], 230)

    def test_stop_terminates_worker_during_stream_and_keeps_received_text(self):
        request = self.start_until_progress()
        self.controller.stop()
        self.assertFalse(request.process.is_alive())
        self.assertEqual(self.app.outputs[-1]["reasoning"], "检查棋盘 🦋")
        self.assertEqual(self.app.state.session.llm_usage.requests[0]["status"], "stopped")
        self.assertIsNone(self.app.state.session.llm_usage.requests[0]["total_tokens"])
        self.assertEqual(self.app.state.session.flow.agent_intents, {})

    def test_disconnect_after_finish_and_usage_discards_all_actions(self):
        self.start_until_progress("drop")
        self.release.set()
        self.wait_for(lambda: self.controller.request is None)
        self.assertEqual(self.controller.state, "stopped")
        self.assertEqual(self.app.outputs[-1]["reasoning"], "检查棋盘 🦋")
        self.assertEqual(self.app.state.session.llm_usage.requests[0]["status"], "failed")
        self.assertEqual(self.app.state.session.llm_usage.requests[0]["total_tokens"], 115)
        self.assertEqual(self.app.state.session.flow.agent_intents, {})

    def test_malformed_stream_keeps_partial_text_and_does_not_execute_prefix(self):
        self.start_until_progress("malformed")
        self.release.set()
        self.wait_for(lambda: self.controller.request is None)
        self.assertEqual(self.controller.state, "stopped")
        self.assertIn("Partial reply", self.app.outputs[-1]["reply"])
        self.assertEqual(self.app.state.session.llm_usage.requests[0]["status"], "failed")
        self.assertEqual(self.app.state.session.flow.agent_intents, {})
