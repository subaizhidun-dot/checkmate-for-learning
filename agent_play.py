"""Single-request play control; only the desktop thread executes game tools."""
from __future__ import annotations

import json
import multiprocessing
import urllib.error
import urllib.parse
import urllib.request
import uuid

from agenttools import TOOLS
from agent_prompts import SYSTEM_PROMPT


def tool_definitions():
    integer = {"type": "integer"}
    string = {"type": "string"}
    definitions = {
        "get_public_state": ({}, [], "Read the visible current board and operation state."),
        "get_legal_actions": ({}, [], "Read legal actions and the current revision."),
        "get_gi_rules": ({}, [], "Read public instructions for the current G1/G2/G3."),
        "get_rule_images": ({"index": integer}, [], "Read the current game's displayed victory clue images."),
        "get_recent_history": ({"limit": integer}, [], "Read recent public game actions."),
        "get_notes": ({}, [], "Read your editable victory-condition notes."),
        "update_notes": ({"text": string}, ["text"], "Replace your own victory-condition notes."),
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


def _http_request(connection, url, key, payload, timeout):
    """Isolated worker: cancellation can terminate even during connection/read."""
    try:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
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
        connection.send({"error": "API request failed. " + detail[:1000]})
    finally:
        connection.close()


class ChatRequest:
    def __init__(self, profile, payload):
        url = completion_url(profile["endpoint"])
        context = multiprocessing.get_context("spawn")
        self.connection, sender = context.Pipe(duplex=False)
        self.process = context.Process(target=_http_request, args=(
            sender, url, profile["api_key"], payload, profile["timeout"]), daemon=True)
        try:
            self.process.start()
        except Exception:
            self.connection.close()
            raise
        finally:
            sender.close()

    def poll(self):
        if self.connection.poll():
            try:
                result = self.connection.recv()
            except EOFError:
                result = {"error": "API worker stopped before returning a reply."}
            self.cancel()
            return result
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
    """Explicit model probes have no game tools, actions or game token accounting."""
    def __init__(self, app, request_factory=ChatRequest):
        self.app, self.request_factory = app, request_factory
        self.requests = {}

    def start(self, side):
        if side in self.requests:
            return
        profile = dict(self.app.gui.api_profiles[side])
        try:
            completion_url(profile["endpoint"])
            if not profile["model"].strip():
                raise ValueError("Enter a model before testing.")
            payload = profile_parameters(profile, {"model": profile["model"], "stream": False,
                "messages": [{"role": "user", "content": "Reply with OK to confirm this API connection."}]})
            request = self.request_factory(profile, payload)
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else "Could not start the connection test."
            self.finish(side, profile, {"error": message})
            return
        self.requests[side] = (request, profile)
        self.app.gui.connection_status[side] = "running"

    def tick(self):
        for side, (request, profile) in list(self.requests.items()):
            returned = request.poll()
            if returned is not None:
                del self.requests[side]
                self.finish(side, profile, returned)

    def finish(self, side, profile, returned):
        data = returned.get("data")
        error = returned.get("error")
        if not error:
            try:
                choice = data["choices"][0]
                message = choice["message"]
                if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                    raise ValueError()
                if choice.get("finish_reason") == "length":
                    error = "Output token limit reached during connection test."
            except (TypeError, KeyError, IndexError, ValueError):
                error = "API returned an invalid chat reply."
        self.app.gui.connection_status[side] = "failed" if error else "success"
        lines = [f"Connection Test | {side.capitalize()} | {'Failed' if error else 'Success'}",
                 "Model | " + profile["model"]]
        if error:
            lines.append(str(error))
        else:
            if message.get("reasoning_content"):
                lines.extend(["Reasoning", str(message["reasoning_content"])])
            lines.extend(["Reply", message["content"], "Usage | " + json.dumps(data.get("usage"), ensure_ascii=False)])
        text = "\n".join(lines)
        if profile["api_key"]:
            text = text.replace(profile["api_key"], "[redacted]")
        # Explicit test diagnostics use ordinary text, including failed probes.
        self.app.gui.append_exposure_output(text)

    def cancel(self, *, reset=True):
        for request, _ in self.requests.values():
            request.cancel()
        if not reset:
            for side in self.requests:
                self.app.gui.connection_status[side] = "idle"
        self.requests.clear()
        if reset:
            self.app.gui.connection_status = {side: "idle" for side in ("black", "white")}


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
        self.exchange = []
        self.images = []
        self.received = False
        self.paused = False

    def side_to_play(self):
        session = self.app.state.session
        if session is None or session.result()["winner"] is not None or session.flow.phase in {"checking_win", "choose_token"}:
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
        self.dispatch()

    def dispatch(self):
        side = self.side_to_play()
        if side is None:
            self.message = "Start a game or wait for an LLM turn."
            return
        profile = self.app.gui.api_profiles[side]
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
        context = {name: self.tools.call(name) for name in (
            "get_public_state", "get_legal_actions", "get_gi_rules", "get_recent_history", "get_notes")}
        payload = {"model": profile["model"], "stream": False, "tools": tool_definitions(),
                   "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + self.history.get(side, []) +
                   [{"role": "user", "content": json.dumps(context, ensure_ascii=False)}]}
        profile_parameters(profile, payload)
        self.request_id = uuid.uuid4().hex
        self.anchor = self.app.begin_llm_request(self.request_id, side)
        self.revision = self.anchor.revision
        self.exchange, self.images = [], []
        try:
            self.request = self.request_factory(dict(profile), payload)
        except Exception:
            self.app.complete_llm_request(self.anchor, self.request_id, status="failed")
            self.state, self.message = "stopped", "Could not start the API request."
            self.app.report_agent_notice(self.message)
            self.auto_running = False
            return
        self.state, self.message = "running", f"Requesting {side.capitalize()} reply"
        self.app.refresh_exposure_notes()

    def pause(self):
        self.auto_running = False
        self.paused = True
        self.state = "pausing" if self.request is not None or self.pending else "stopped"
        self.message = "Finishing current reply" if self.state == "pausing" else "Paused"

    def stop(self, *, reset=False):
        self.auto_running = False
        if self.request is not None:
            self.request.cancel()
            self.request = None
            self.app.complete_llm_request(self.anchor, self.request_id, status="stopped")
        self.pending = []
        self.exchange, self.images = [], []
        self.state, self.message = ("idle", "Ready") if reset else ("stopped", "Stopped")
        self.paused = False
        if reset:
            self.history = {}
            self.received = False

    def tick(self):
        if (self.request is not None or self.pending) and self.anchor is not self.app.state.session:
            self.stop(reset=True)
            return
        session = self.app.state.session
        if session is not None and session.result()["winner"] is not None:
            if self.request is not None or self.pending:
                self.stop()
            self.state, self.message = "stopped", "Game decided"
            self.auto_running = False
            return
        if self.request is not None:
            response = self.request.poll()
            if response is None:
                return
            self.request = None
            self.receive(response)
        elif self.pending:
            self.execute_one()
        elif self.auto_running and self.side_to_play() is not None:
            self.dispatch()

    def receive(self, response):
        if response.get("error"):
            self.app.complete_llm_request(self.anchor, self.request_id, status="failed", reply=response["error"])
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
            if message.get("content") is not None and not isinstance(message["content"], str):
                raise ValueError()
            assistant = {"role": "assistant", "content": message.get("content") or None}
            if calls:
                assistant["tool_calls"] = calls
            if message.get("reasoning_content"):
                assistant["reasoning_content"] = message["reasoning_content"]
            reply = str(message.get("content") or "")
            if calls:
                reply += "\n" + json.dumps(calls, ensure_ascii=False)
        except (KeyError, IndexError, TypeError, ValueError):
            self.app.complete_llm_request(self.anchor, self.request_id, status="failed",
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
        self.app.complete_llm_request(self.anchor, self.request_id, usage=data.get("usage"),
                                      reasoning=message.get("reasoning_content", ""), reply=reply)
        self.received = True
        self.exchange = [assistant]
        self.pending = list(calls)
        if self.anchor.revision != self.revision:
            self.discard_pending("stale_board")
            self.auto_running = False
            self.message = "Board changed; reply actions were discarded."
            self.app.report_agent_notice(self.message)
        if not self.pending:
            self.finish_reply()
            self.auto_running = False  # Do not spin on a text-only or invalid response.

    def tool_result(self, call, result):
        self.exchange.append({"role": "tool", "tool_call_id": call["id"],
                              "content": json.dumps(result, ensure_ascii=False)})

    def discard_pending(self, reason):
        for call in self.pending:
            self.tool_result(call, {"ok": False, "code": reason})
        self.pending = []

    def execute_one(self):
        call = self.pending.pop(0)
        try:
            name = call["function"]["name"]
            arguments = json.loads(call["function"].get("arguments", "{}"))
            if not isinstance(arguments, dict):
                raise ValueError()
            if name == "get_rule_images" and not self.app.gui.api_profiles[self.tools.color]["vision"]:
                result = {"ok": False, "code": "vision_disabled", "message": "Image input is disabled in this API profile."}
            else:
                result = self.tools.call(name, arguments)
            if name == "get_rule_images" and result.get("ok"):
                for image in result["images"]:
                    self.images.append({"type": "image_url", "image_url": {
                        "url": "data:" + image["mime_type"] + ";base64," + image["data"]}})
                result = {"ok": True, "gamemode": result["gamemode"],
                          "images": [{"index": image["index"], "mime_type": image["mime_type"]} for image in result["images"]]}
        except (ValueError, KeyError, TypeError):
            result = {"ok": False, "code": "invalid_tool_call"}
        self.tool_result(call, result)
        if not result.get("ok"):
            self.app.report_agent_notice("Tool result | " + json.dumps(result, ensure_ascii=False))
        self.app.refresh_exposure_notes()
        if not result.get("ok") or (name == "submit_plan" and result.get("stop_reason") not in {"completed", None}):
            self.discard_pending("previous_action_stopped")
            if not result.get("ok") or result.get("stop_reason") not in {
                "checking_win", "time_wish", "game_over", "awaiting_other_side", "extra_turn",
            }:
                self.auto_running = False
        if self.anchor.result()["winner"] is not None or self.anchor.flow.phase == "checking_win" or not self.tools._can_decide():
            self.discard_pending("turn_boundary")
        if not self.pending:
            self.finish_reply()

    def finish_reply(self):
        if self.images:
            self.exchange.append({"role": "user", "content": [
                {"type": "text", "text": "Requested public rule clue images:"}, *self.images]})
        self.history[self.tools.color] = list(self.exchange)
        self.exchange, self.images = [], []
        winner = self.anchor.result()["winner"]
        if self.paused or winner is not None:
            self.state = "stopped"
            self.message = "Paused" if self.paused else "Game decided"
            self.auto_running = False
        else:
            self.state, self.message = "ready", "Reply finished; next request ready"
        self.app.persist()
