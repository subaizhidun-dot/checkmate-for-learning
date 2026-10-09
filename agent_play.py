"""Decision-level tool loops; only the desktop thread executes game tools."""
from __future__ import annotations

import json
import multiprocessing
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from copy import deepcopy

from agenttools import TOOLS
from agent_prompts import SYSTEM_PROMPT, NG_PLUS_PROMPT, configured_prompt
from agent_context import context_tool_result, decision_summary
from chat_stream import ChatStream, MAX_RESPONSE_BYTES, read_chat_stream
from probe_image import image_probe


MAX_DECISION_REQUESTS = 16
NETWORK_IO_TIMEOUT = 600


def tool_definitions():
    integer = {"type": "integer"}
    string = {"type": "string"}
    position = {"type": "array", "items": integer, "minItems": 2, "maxItems": 2}
    definitions = {
        "get_public_state": ({}, [], "Read the visible current board and operation state."),
        "get_legal_actions": ({}, [], "Read legal actions and the current revision."),
        "get_gi_rules": ({}, [], "Read public instructions for the current G1/G2/G3."),
        "get_rule_images": ({"index": integer}, [], "Read the current game's displayed victory clue images."),
        "get_recent_history": ({"limit": integer}, [], "Read recent public game actions."),
        "get_notes": ({}, [], "Read your editable victory-condition notes."),
        "update_notes": ({"text": string}, ["text"], "Replace your own victory-condition notes."),
        "record_intent": ({"text": string}, ["text"], "Record your experiment or winning plan and predicted numbered condition results before acting. Maximum 2000 characters; private to your side. Does not update condition notes."),
        "move_piece": ({"source": position, "target": position, "revision": integer, "request_id": string},
                       ["source", "target", "revision", "request_id"],
                       "Select and move a piece in one call. Choose source/target from get_legal_actions.moves. Complete any resulting bonus or piece-limit decision."),
        "apply_action": ({"action_id": string, "revision": integer, "request_id": string},
                         ["action_id", "revision"], "Execute one legal action at the current revision."),
        "submit_plan": ({"actions": {"type": "array", "minItems": 1, "maxItems": 32,
                                      "items": {"type": "object", "properties": {
                                          "type": string, "params": {"type": "object"}},
                                          "required": ["type"], "additionalProperties": False}},
                         "revision": integer, "request_id": string},
                        ["actions", "revision", "request_id"], "Execute ordered actions, stopping at failure or a turn boundary."),
    }
    return [{"type": "function", "function": {"name": name, "description": definitions[name][2],
             "parameters": {"type": "object", "properties": definitions[name][0],
                            "required": definitions[name][1], "additionalProperties": False}}} for name in TOOLS]


def completion_url(endpoint):
    endpoint = endpoint.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter an HTTP(S) API base URL without credentials or query parameters.")
    return endpoint if endpoint.endswith("/chat/completions") else endpoint + "/chat/completions"


def profile_parameters(profile, payload):
    if profile["max_tokens"]:
        payload[profile["token_parameter"]] = profile["max_tokens"]
    if profile["temperature"] is not None:
        payload["temperature"] = profile["temperature"]
    if profile["reasoning_effort"] != "default":
        payload["reasoning_effort"] = profile["reasoning_effort"]
    return payload


def streaming_parameters(profile, payload):
    payload["stream"] = profile.get("streaming", True)
    if payload["stream"]:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _http_request(connection, url, key, payload, timeout):
    """Isolated worker: cancellation can terminate even during connection/read."""
    accumulator = ChatStream()
    try:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if payload.get("stream") and "application/json" not in response.headers.get("Content-Type", "").lower():
                data = read_chat_stream(response, connection.send, accumulator)
            else:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("Response too large")
                data = json.loads(raw)
        connection.send({"data": data})
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            body = json.loads(error.read(16384))
            provider_error = body.get("error", {})
            detail = provider_error.get("message", "") if isinstance(provider_error, dict) else str(provider_error)
        except (ValueError, OSError, AttributeError):
            pass
        detail = str(detail).replace(key, "[redacted]") if key else str(detail)
        connection.send({"error": f"API returned HTTP {error.code}. " + (detail[:1000] or "Check endpoint, key and model.")})
    except Exception as error:
        detail = f"{type(error).__name__}: {error}"
        if key:
            detail = detail.replace(key, "[redacted]")
        connection.send({"error": "API request failed. " + detail[:1000], "usage": accumulator.usage})
    finally:
        connection.close()


class ChatRequest:
    def __init__(self, profile, payload):
        url = completion_url(profile["endpoint"])
        context = multiprocessing.get_context("spawn")
        self.connection, sender = context.Pipe(duplex=False)
        self.process = context.Process(target=_http_request, args=(
            sender, url, profile["api_key"], payload, NETWORK_IO_TIMEOUT), daemon=True)
        try:
            self.process.start()
        except Exception:
            self.connection.close()
            raise
        finally:
            sender.close()

    def poll(self):
        progress = {"reasoning": "", "reply": ""}
        # Drain bounded batches so a busy stream cannot monopolize the GUI.
        for _ in range(64):
            if not self.connection.poll():
                break
            try:
                result = self.connection.recv()
            except EOFError:
                result = {"error": "API worker stopped before returning a reply."}
            if result.get("event") == "progress":
                for key in progress:
                    progress[key] += result.get(key, "")
                continue
            if any(progress.values()):
                result["progress"] = progress
            self.cancel()
            return result
        if any(progress.values()):
            return {"event": "progress", **progress}
        if not self.process.is_alive():
            self.cancel()
            return {"error": "API worker stopped before returning a reply."}
        return None

    def cancel(self):
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=0.1)
        self.connection.close()


class ConnectionTester:
    """Text, isolated tool round-trip and image probes, without game mutations."""
    def __init__(self, app, request_factory=ChatRequest):
        self.app, self.request_factory = app, request_factory
        self.requests = {}
        self.jobs = {}
        self.results = {}

    def start(self, side):
        if side in self.requests:
            return
        profile = dict(self.app.gui.api_profiles[side])
        results = {"Text": "pending", "Tool call": "pending", "Tool result": "pending",
                   "Image input": "pending" if profile["vision"] else "skipped"}
        self.results[side] = results
        self.jobs[side] = {"stage": "Text", "nonce": uuid.uuid4().hex, "started": time.monotonic(),
                           "results": results}
        try:
            completion_url(profile["endpoint"])
            if not profile["model"].strip():
                raise ValueError("Enter a model before testing.")
            payload = streaming_parameters(profile, profile_parameters(profile, {"model": profile["model"],
                "messages": [{"role": "user", "content": "Reply with OK to confirm this API connection."}]}))
            request = self.request_factory(profile, payload)
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else "Could not start the connection test."
            self.finish(side, profile, {"error": message}, abort=True)
            return
        self.requests[side] = (request, profile)
        self.app.gui.connection_status[side] = "running"

    def tick(self):
        for side, (request, profile) in list(self.requests.items()):
            if time.monotonic() - self.jobs[side]["started"] >= profile["timeout"]:
                request.cancel()
                returned = {"error": "Connection test time limit reached."}
            else:
                returned = request.poll()
            if returned is not None and returned.get("event") != "progress":
                del self.requests[side]
                self.finish(side, profile, returned)

    def finish(self, side, profile, returned, *, abort=False):
        job = self.jobs[side]
        data = returned.get("data")
        error = returned.get("error")
        message = {}
        if not error:
            try:
                choice = data["choices"][0]
                message = choice["message"]
                if not isinstance(message, dict) or choice.get("finish_reason") not in {None, "stop", "tool_calls"}:
                    raise ValueError("Reply did not finish normally.")
                if job["stage"] == "Tool call":
                    calls = message.get("tool_calls")
                    if not isinstance(calls, list) or len(calls) != 1:
                        raise ValueError("Expected one connection_echo tool call.")
                    call = calls[0]
                    if (call.get("type") != "function" or call["function"]["name"] != "connection_echo"
                            or not isinstance(call.get("id"), str) or not call["id"]
                            or json.loads(call["function"]["arguments"]) != {"nonce": job["nonce"]}):
                        raise ValueError("Invalid test tool name or arguments.")
                    job["assistant"] = {"role": "assistant", **deepcopy(message)}
                    job["call_id"] = call["id"]
                else:
                    content = message.get("content")
                    if not isinstance(content, str) or message.get("tool_calls"):
                        raise ValueError("Expected a text reply.")
                    expected = {"Text": "OK", "Tool result": job.get("receipt"), "Image input": job.get("color")}[job["stage"]]
                    if content.strip().lower().rstrip(".") != expected.lower():
                        raise ValueError(f"{job['stage']} response did not match the expected answer.")
            except (TypeError, KeyError, IndexError, ValueError, AttributeError) as exc:
                error = "Connection capability check failed: " + str(exc)
        job["results"][job["stage"]] = "failed" if error else "passed"
        if error and job["stage"] == "Tool call":
            job["results"]["Tool result"] = "skipped"
        if abort:
            for capability, status in job["results"].items():
                if status == "pending":
                    job["results"][capability] = "skipped"
        final = "pending" not in job["results"].values()
        failed = "failed" in job["results"].values()
        self.app.gui.connection_status[side] = "failed" if failed else "success" if final else "running"
        lines = [f"Connection Test | {side.capitalize()} | {'Failed' if failed else 'Success' if final else 'Testing'}",
                 "Capability | " + job["stage"] + (" failed" if error else " passed"),
                 "Model | " + profile["model"]]
        if error:
            lines.append(str(error))
        else:
            if message.get("reasoning_content"):
                lines.extend(["Reasoning", str(message["reasoning_content"])])
            lines.extend(["Reply", str(message.get("content") or message.get("tool_calls")),
                          "Usage | " + json.dumps(data.get("usage"), ensure_ascii=False)])
        if error and job["stage"] == "Tool call":
            lines.append("Tool result | Skipped (no valid tool call to answer)")
        if final:
            lines.extend(capability + " | " + status.capitalize() for capability, status in job["results"].items())
            if not profile["vision"]:
                lines.append("Image input | Skipped (disabled in this profile)")
        text = "\n".join(lines)
        if profile["api_key"]:
            text = text.replace(profile["api_key"], "[redacted]")
        # Explicit test diagnostics use ordinary text, including failed probes.
        self.app.gui.append_exposure_output(text)
        if final:
            self.jobs.pop(side, None)
        else:
            self.advance(side, profile)

    def advance(self, side, profile):
        job = self.jobs[side]
        payload = {"model": profile["model"]}
        if job["stage"] == "Text":
            job["stage"] = "Tool call"
            payload["messages"] = [{"role": "user", "content":
                "Call connection_echo exactly once with nonce " + job["nonce"] +
                ". Use the provided tool; a text reply alone does not pass this check."}]
            job["tool"] = {"type": "function", "function": {"name": "connection_echo",
                "description": "An isolated connection test; returns a receipt.", "parameters": {
                    "type": "object", "properties": {"nonce": {"type": "string"}},
                    "required": ["nonce"], "additionalProperties": False}}}
            payload["tools"] = [job["tool"]]
            job["messages"] = payload["messages"]
        elif job["stage"] == "Tool call" and job["results"]["Tool call"] == "passed":
            job["stage"] = "Tool result"
            job["receipt"] = uuid.uuid4().hex
            payload["messages"] = [*job["messages"], job["assistant"],
                {"role": "tool", "tool_call_id": job["call_id"], "content": json.dumps({"receipt": job["receipt"]})},
                {"role": "user", "content": "Reply with only the receipt returned by the tool."}]
            payload["tools"] = [job["tool"]]
        else:
            job["stage"] = "Image input"
            url, job["color"] = image_probe()
            payload["messages"] = [{"role": "user", "content": [
                {"type": "text", "text": "What color is the top-left square? Reply with only its color name."},
                {"type": "image_url", "image_url": {"url": url}}]}]
        job["started"] = time.monotonic()
        try:
            payload = streaming_parameters(profile, profile_parameters(profile, payload))
            self.requests[side] = (self.request_factory(profile, payload), profile)
        except Exception as exc:
            self.finish(side, profile, {"error": f"Could not start capability test: {type(exc).__name__}."})

    def cancel(self, *, reset=True):
        for request, _ in self.requests.values():
            request.cancel()
        if not reset:
            for side in self.requests:
                self.app.gui.connection_status[side] = "idle"
        self.requests.clear()
        self.jobs.clear()
        if reset:
            self.app.gui.connection_status = {side: "idle" for side in ("black", "white")}
            self.results.clear()


class AgentPlayController:
    def __init__(self, app, request_factory=ChatRequest):
        self.app = app
        self.request_factory = request_factory
        self.state = "idle"
        self.message = "Ready"
        self.request = None
        self.pending = []
        self.anchor = None
        self.tools = None
        self.request_id = None
        self.auto_running = False
        self.history = {}
        self.completed_history = {}
        self.previous_history = {}
        self.history_turn = {}
        self.cached_images = {}
        self.context_session = None
        self.request_context = None
        self.continuation = False
        self.decision_running = False
        self.decision_side = None
        self.decision_requests = 0
        self.operation_committed = False
        self.reply_failed = False
        self.exchange = []
        self.images = []
        self.received = False
        self.paused = False
        self.partial_reasoning = ""
        self.partial_reply = ""
        self.progress_updated_at = 0
        self.progress_dirty = False
        self.no_ap_since = None
        self.no_ap_limit = 120
        self.execution_revision = None
        self.call_revisions = {}
        self.step_permit = None

    def side_to_play(self):
        session = self.app.state.session
        if session is None or session.result()["winner"] is not None or session.flow.phase in {"checking_win", "choose_token", "time_wish"}:
            return None
        side = session.actor()
        if self.app.gui.agent_play_mode and self.app.gui.player_types.get(side) == "llm":
            return side
        return None

    def next(self):
        if self.request is not None or self.pending:
            return
        self.paused = False
        self.auto_running = self.app.gui.play_control_mode == "auto"
        if self.reviews_busy():
            self.message = "Reviewing notes; press Next after review for an action."
            return
        self.step_permit = None
        self.decision_running = False
        session = self.app.state.session
        side = self.side_to_play()
        if session is not None and side is not None:
            self.step_permit = (session, session.turn_number, side)
        self.dispatch()

    def dispatch(self):
        if self.request is not None or self.pending:
            return
        if self.reviews_busy():
            return
        side = self.side_to_play()
        if side is None:
            self.message = "Start a game or wait for an LLM turn."
            return
        session = self.app.state.session
        if self.app.gui.play_control_mode == "step":
            if self.step_permit != (session, session.turn_number, side):
                return
            self.step_permit = None
        if self.context_session is not session:
            self.history, self.completed_history, self.previous_history = {}, {}, {}
            self.history_turn, self.cached_images = {}, {}
            self.context_session = session
        if not self.decision_running or self.decision_side != side:
            self.decision_running = True
            self.decision_side = side
            self.decision_requests = 0
            self.operation_committed = session.flow.phase == "pending_elephant_bonus"
            self.no_ap_since = time.monotonic()
        self.continuation = False
        if self.decision_requests >= MAX_DECISION_REQUESTS:
            self.decision_running = self.auto_running = False
            self.state, self.message = "stopped", "Decision request limit reached."
            self.app.report_agent_notice("Decision request limit reached before completing an operation. Review the reply and press Next to continue.")
            self.app.persist()
            return
        profile = self.app.gui.api_profiles[side]
        self.no_ap_limit = profile["timeout"]
        if not profile["model"].strip() or not profile["endpoint"].strip():
            self.state, self.message = "stopped", "Set API URL and model first."
            self.app.report_agent_notice(self.message)
            self.auto_running = False
            return
        try:
            completion_url(profile["endpoint"])
        except ValueError as error:
            self.state, self.message = "stopped", str(error)
            self.app.report_agent_notice(self.message)
            self.auto_running = False
            return
        self.tools = self.app.agent_tools_by_side[side]
        self.app.agent_tools = self.tools
        self.app.gui.llm_color = side
        if self.history_turn.get(side) != session.turn_number:
            self.archive_decision(side)
            self.previous_history[side] = self.completed_history.get(side, [])
            self.completed_history[side] = []
            self.history[side] = []
            self.history_turn[side] = session.turn_number
        standard = session.game.time_token_owner is not None
        context_names = ["get_public_state", "get_legal_actions", "get_recent_history"]
        if standard:
            context_names.append("get_notes")
        context = {name: self.tools.call(name) for name in context_names}
        public_state = context["get_public_state"]
        public_rules = self.tools.call("get_gi_rules")
        public_rules["instructions"] = [line for line in public_rules["instructions"]
                                         if "clue images" not in line]
        rules = {"public_rules": public_rules, "coordinates": public_state.pop("coordinates")}
        public_state.pop("last_completed_check")
        public_state["turn_number"] = session.turn_number
        public_state.pop("visible_condition_feedback", None)
        if standard:
            context["own_intents"] = deepcopy(session.flow.agent_intents.get(side, [])[-8:])
        else:
            context.pop("get_notes", None)
            context.pop("public_condition_evidence", None)
            public_state.pop("visible_condition_feedback", None)
        context["completed_decisions"] = self.previous_history.get(side, []) + self.completed_history.get(side, [])
        self.request_context = {"role": "user", "content": json.dumps(context, ensure_ascii=False)}
        excluded = {"update_notes", "get_rule_images"} if standard else {"update_notes", "record_intent", "get_notes", "get_rule_images"}
        self.allowed_tools = {name for name in TOOLS if name not in excluded}
        payload = {"model": profile["model"], "tools": [t for t in tool_definitions() if t["function"]["name"] in self.allowed_tools],
                   "messages": [{"role": "system", "content": configured_prompt(self.app.gui, "system_prompt" if standard else "ng_plus_prompt")},
                                {"role": "system", "content": json.dumps(rules, ensure_ascii=False)}] +
                   self.history.get(side, []) + [self.request_context]}
        profile_parameters(profile, payload)
        streaming_parameters(profile, payload)
        self.request_id = uuid.uuid4().hex
        self.anchor = self.app.begin_llm_request(self.request_id, side)
        self.revision = self.anchor.revision
        self.exchange, self.images = [], []
        self.reply_failed = False
        self.partial_reasoning, self.partial_reply = "", ""
        self.progress_updated_at = 0
        self.progress_dirty = False
        if payload["stream"]:
            self.app.update_llm_request(self.anchor, self.request_id)
        try:
            self.request = self.request_factory(dict(profile), payload)
        except Exception:
            self.app.complete_llm_request(self.anchor, self.request_id, status="failed")
            self.state, self.message = "stopped", "Could not start the API request."
            self.app.report_agent_notice(self.message)
            self.auto_running = False
            return
        self.state, self.message = "running", f"Requesting {side.capitalize()} reply"
        self.decision_requests += 1
        self.app.refresh_exposure_notes()

    def pause(self):
        self.step_permit = None
        self.auto_running = False
        self.continuation = False
        self.paused = True
        self.state = "pausing" if self.request is not None or self.pending else "stopped"
        self.message = "Finishing current reply" if self.state == "pausing" else "Paused"

    def stop(self, *, reset=False):
        self.step_permit = None
        self.auto_running = False
        self.continuation = self.decision_running = False
        self.no_ap_since = None
        if self.request is not None:
            self.request.cancel()
            self.request = None
            self.app.complete_llm_request(self.anchor, self.request_id, status="stopped",
                                          reasoning=self.partial_reasoning,
                                          reply=self.partial_reply + "\nRequest interrupted; no remaining actions executed.")
        self.discard_pending("stopped")
        self.remember_exchange()
        self.state, self.message = ("idle", "Ready") if reset else ("stopped", "Stopped")
        self.paused = False
        if reset:
            self.history = {}
            self.completed_history = {}
            self.previous_history, self.history_turn, self.cached_images = {}, {}, {}
            self.context_session = None
            self.received = False

    def tick(self):
        if (self.request is not None or self.pending) and self.anchor is not self.app.state.session:
            self.stop(reset=True)
            return
        session = self.app.state.session
        if self.app.gui.play_control_mode == "step":
            self.auto_running = False
            if self.request is None and not self.pending:
                self.continuation = self.decision_running = False
                if self.state == "running":
                    self.state, self.message = "ready", "Press Next to continue"
        if (self.decision_running and self.no_ap_since is not None
                and (self.request is not None or self.pending or self.continuation)
                and time.monotonic() - self.no_ap_since >= self.no_ap_limit):
            self.stop()
            self.message = f"Stopped: no AP cost for {self.no_ap_limit} seconds."
            self.app.report_agent_notice(self.message)
            return
        if session is not None and session.flow.phase == "time_wish":
            if self.request is not None or self.pending:
                self.stop()
            self.state, self.message = "stopped", "Time wish: human only"
            self.auto_running = False
            return
        if session is not None and session.result()["winner"] is not None:
            if self.request is not None or self.pending:
                self.stop()
            self.state, self.message = "stopped", "Game decided"
            self.auto_running = False
            return
        if self.request is not None:
            response = self.request.poll()
            if response is None:
                self.show_progress()
                return
            if response.get("progress"):
                self.receive_progress(response["progress"])
            if response.get("event") == "progress":
                self.receive_progress(response)
                return
            self.request = None
            self.receive(response)
        elif self.pending:
            self.execute_one()
        elif self.reviews_busy():
            return
        elif (self.continuation and self.app.gui.play_control_mode == "auto"
              and self.side_to_play() == self.decision_side and not self.paused):
            self.dispatch()
        elif self.auto_running and self.app.gui.play_control_mode == "auto" and self.side_to_play() is not None:
            self.dispatch()

    def receive_progress(self, response):
        if self.anchor is not self.app.state.session:
            return
        self.partial_reasoning += response.get("reasoning", "")
        self.partial_reply += response.get("reply", "")
        self.progress_dirty = True
        self.show_progress()

    def reviews_busy(self):
        reviewer = getattr(self.app, "note_reviewer", None)
        if reviewer is not None:
            reviewer.collect()
        return reviewer is not None and reviewer.busy

    def show_progress(self):
        now = time.monotonic()
        if self.progress_dirty and now - self.progress_updated_at >= 0.05:
            self.app.update_llm_request(self.anchor, self.request_id,
                                        reasoning=self.partial_reasoning, reply=self.partial_reply)
            self.progress_updated_at = now
            self.progress_dirty = False

    def receive(self, response):
        if response.get("error"):
            self.app.complete_llm_request(self.anchor, self.request_id, status="failed",
                                          reasoning=self.partial_reasoning,
                                          reply=self.partial_reply + "\n" + response["error"], usage=response.get("usage"))
            self.state, self.message = "stopped", response["error"]
            self.auto_running = False
            return
        data = response.get("data")
        try:
            choice = data["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError()
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list) or len(calls) > 32 or any(not isinstance(call, dict) for call in calls):
                raise ValueError()
            ids = [call["id"] for call in calls]
            if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
                raise ValueError()
            for call in calls:
                if call.get("type") != "function" or not isinstance(call.get("function"), dict):
                    raise ValueError()
                if not isinstance(call["function"].get("name"), str) or not isinstance(call["function"].get("arguments"), str):
                    raise ValueError()
                if choice.get("finish_reason") not in {"length", "content_filter"}:
                    if not isinstance(json.loads(call["function"]["arguments"]), dict):
                        raise ValueError()
            if message.get("content") is not None and not isinstance(message["content"], str):
                raise ValueError()
            assistant = {"role": "assistant", "content": message.get("content") or None}
            if calls:
                assistant["tool_calls"] = calls
            if "reasoning_content" in message:
                assistant["reasoning_content"] = message["reasoning_content"]
            reply = str(message.get("content") or "")
            if calls:
                reply += "\n" + json.dumps(calls, ensure_ascii=False)
        except (KeyError, IndexError, TypeError, ValueError):
            self.app.complete_llm_request(self.anchor, self.request_id, status="failed",
                                          reasoning=self.partial_reasoning, reply=self.partial_reply,
                                          usage=data.get("usage") if isinstance(data, dict) else None)
            self.state, self.message = "stopped", "API returned an invalid chat reply."
            self.app.report_agent_notice(self.message)
            self.auto_running = False
            return
        if choice.get("finish_reason") == "length":
            self.app.complete_llm_request(
                self.anchor, self.request_id, status="failed", usage=data.get("usage"),
                reasoning=message.get("reasoning_content", ""),
                reply="Output token limit reached; no reply actions executed.\n" + reply)
            self.received = True
            self.pending = []
            self.auto_running = False
            self.state, self.message = "stopped", "Output limit reached; raise token limit."
            self.app.report_agent_notice("Output token limit reached. Increase Output Token Limit in Set, then press Next.")
            return
        if choice.get("finish_reason") not in {None, "stop", "tool_calls"}:
            self.message = "API did not complete the reply normally; no actions executed."
            self.app.complete_llm_request(self.anchor, self.request_id, status="failed", usage=data.get("usage"),
                                          reasoning=message.get("reasoning_content", ""), reply=reply + "\n" + self.message)
            self.pending = []
            self.auto_running = False
            self.state = "stopped"
            return
        self.app.complete_llm_request(self.anchor, self.request_id, usage=data.get("usage"),
                                      reasoning=message.get("reasoning_content", ""), reply=reply)
        self.received = True
        self.exchange = [assistant]
        self.pending = list(calls)
        self.execution_revision = self.revision
        self.call_revisions = {}
        if self.anchor.revision != self.revision:
            self.discard_pending("stale_board")
            self.auto_running = False
            self.reply_failed = True
            self.message = "Board changed; reply actions were discarded."
            self.app.report_agent_notice(self.message)
        if not self.pending:
            self.finish_reply()
            self.auto_running = False  # Do not spin on a text-only or invalid response.

    def tool_result(self, call, result):
        result = context_tool_result(call["function"]["name"], result)
        self.exchange.append({"role": "tool", "tool_call_id": call["id"],
                              "content": json.dumps(result, ensure_ascii=False)})

    def discard_pending(self, reason):
        for call in self.pending:
            self.tool_result(call, {"ok": False, "code": reason})
        self.pending = []

    def execute_one(self):
        if self.anchor.revision != self.execution_revision:
            self.discard_pending("stale_board")
            self.reply_failed = True
            self.auto_running = False
            self.app.report_agent_notice("Board changed outside this reply; remaining actions discarded.")
            self.finish_reply()
            return
        call = self.pending.pop(0)
        before = (self.anchor.game.current_ap, self.anchor.turn_number)
        try:
            name = call["function"]["name"]
            if name not in self.allowed_tools:
                raise ValueError("Tool unavailable in action requests")
            arguments = json.loads(call["function"].get("arguments", "{}"))
            if not isinstance(arguments, dict):
                raise ValueError()
            if name in {"move_piece", "apply_action", "submit_plan"}:
                supplied_revision = arguments.get("revision")
                identity = (name, arguments.get("request_id"))
                cached_revision = self.call_revisions.get(identity) if isinstance(identity[1], str) else None
                if type(supplied_revision) is not int:
                    raise ValueError("Invalid revision")
                if cached_revision is not None:
                    if supplied_revision != cached_revision[0]:
                        raise ValueError("Conflicting request revision")
                    arguments["revision"] = cached_revision[1]
                elif supplied_revision in {self.revision, self.execution_revision}:
                    arguments["revision"] = self.execution_revision
                    if isinstance(identity[1], str):
                        self.call_revisions[identity] = (supplied_revision, self.execution_revision)
                else:
                    raise ValueError("Stale action revision")
            if name == "get_rule_images" and not self.app.gui.api_profiles[self.tools.color]["vision"]:
                result = {"ok": False, "code": "vision_disabled", "message": "Image input is disabled in this API profile."}
            else:
                result = self.tools.call(name, arguments)
            if name == "get_rule_images" and result.get("ok"):
                for image in result["images"]:
                    self.images.append((image["index"], {"type": "image_url", "image_url": {
                        "url": "data:" + image["mime_type"] + ";base64," + image["data"]}}))
                result = {"ok": True, "gamemode": result["gamemode"],
                          "images": [{"index": image["index"], "mime_type": image["mime_type"]} for image in result["images"]]}
        except (ValueError, KeyError, TypeError):
            result = {"ok": False, "code": "invalid_tool_call"}
        self.tool_result(call, result)
        self.execution_revision = self.anchor.revision
        if result.get("ok") and not result.get("replayed"):
            spent = self.anchor.game.current_ap < before[0] or self.anchor.turn_number != before[1]
            self.operation_committed |= spent
            if spent:
                self.no_ap_since = time.monotonic()
            # Resolving a limit can be the entire pending operation on entry.
            if name in {"apply_action", "submit_plan"} and self.anchor.flow.phase == "playing":
                action_ids = result.get("executed", [result.get("action_id", "")])
                self.operation_committed |= any(action_id.startswith("remove_piece_limit:") for action_id in action_ids)
        if not result.get("ok"):
            self.app.report_agent_notice("Tool result | " + json.dumps(result, ensure_ascii=False))
        self.app.refresh_exposure_notes()
        if not result.get("ok") or (name == "submit_plan" and result.get("stop_reason") not in {"completed", None}):
            self.discard_pending("previous_action_stopped")
            if not result.get("ok") or result.get("stop_reason") not in {
                "checking_win", "time_wish", "game_over", "awaiting_other_side", "extra_turn",
            }:
                self.auto_running = False
                self.reply_failed = True
        if (self.anchor.result()["winner"] is not None or self.anchor.flow.phase == "checking_win"
                or not self.tools._can_decide() or self.anchor.turn_number != before[1]):
            self.discard_pending("turn_boundary")
        if not self.pending:
            self.finish_reply()

    def remember_exchange(self):
        if self.tools is not None and self.exchange:
            marker = {"role": "user", "content": json.dumps({"context_revision": self.revision,
                       "turn_number": self.history_turn[self.tools.color]})}
            self.history.setdefault(self.tools.color, []).extend([marker, *self.exchange])
            cache = self.cached_images.setdefault(self.tools.color, {})
            cache.update(self.images)
        self.exchange, self.images = [], []

    def archive_decision(self, side):
        messages = self.history.get(side, [])
        if messages:
            self.completed_history.setdefault(side, []).append(
                decision_summary(messages, self.history_turn[side], self.context_session.revision))
            self.history[side] = []

    def finish_reply(self):
        has_tools = any(message.get("tool_calls") for message in self.exchange)
        self.remember_exchange()
        winner = self.anchor.result()["winner"]
        phase = self.anchor.flow.phase
        boundary = (phase in {"checking_win", "time_wish", "game_over"}
                    or not self.tools._can_decide() or self.anchor.turn_number != self.history_turn[self.tools.color])
        completed = boundary or (self.operation_committed and phase == "playing")
        if completed or not has_tools:
            self.archive_decision(self.tools.color)
        if self.paused or winner is not None or self.reply_failed:
            self.state = "stopped"
            self.message = "Paused" if self.paused else "Game decided" if winner is not None else "Tool failed; review before continuing."
            self.auto_running = False
        elif has_tools and not completed and self.app.gui.play_control_mode == "auto":
            self.continuation = True
            self.state, self.message = "running", "Continuing the current operation"
            return
        else:
            self.state, self.message = "ready", "Operation finished; next decision ready" if completed else "Reply finished; press Next to continue"
        self.continuation = self.decision_running = False
        self.app.persist()
