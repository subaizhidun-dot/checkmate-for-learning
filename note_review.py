"""One notes-only model request per configured side after a public turn check."""
from collections import deque
from copy import deepcopy
import json
import time
import uuid

from agent_play import ChatRequest, completion_url, profile_parameters, streaming_parameters, tool_definitions
from agent_prompts import NOTES_PROMPT, configured_prompt
from agent_context import condition_evidence, evidence_sequence, public_checks


class NoteReviewer:
    def __init__(self, app, request_factory=ChatRequest):
        self.app, self.request_factory = app, request_factory
        self.session = None
        self.seen = 0
        self.queue = deque()
        self.active = None

    @property
    def busy(self):
        return bool(self.queue or self.active)

    @staticmethod
    def eligible(session, event):
        return (session.result()["winner"] is None
                and event.get("time_token_owner") in {"black", "white"}
                and bool(event.get("conditions")) and not all(event["conditions"]))

    def collect(self):
        session = self.app.state.session
        if session is not self.session:
            self.cancel()
            self.session, self.seen = session, 0
        if session is None:
            return
        events = session.public_check_events[self.seen:]
        self.seen = len(session.public_check_events)
        if session.result()["winner"] is not None:
            self.cancel()
            return
        if not self.app.gui.agent_play_mode:
            return
        for event in events:
            if not self.eligible(session, event):
                continue
            for side, kind in self.app.gui.player_types.items():
                if kind == "llm":
                    self.queue.append((session, side, deepcopy(event)))

    def tick(self):
        self.collect()
        if self.active:
            job = self.active
            if time.monotonic() - job["started"] >= job["profile"]["timeout"]:
                job["request"].cancel()
                self.finish({"error": "Notes review time limit reached."})
                return
            returned = job["request"].poll()
            if returned is None:
                return
            progress = returned.get("progress", returned if returned.get("event") == "progress" else {})
            job["reasoning"] += progress.get("reasoning", "")
            job["reply"] += progress.get("reply", "")
            if returned.get("event") == "progress":
                if time.monotonic() - job["displayed"] >= 0.05:
                    self.app.update_llm_request(job["session"], job["id"],
                                                reasoning=job["reasoning"], reply=job["reply"], turn=job["event"]["turn_number"])
                    job["displayed"] = time.monotonic()
                return
            self.finish(returned)
        if (self.queue and not self.app.agent_play.paused and self.app.agent_play.request is None
                and not self.app.agent_play.pending):
            self.start(*self.queue.popleft())

    def start(self, session, side, event):
        if session is not self.app.state.session or not self.app.gui.agent_play_mode:
            return
        if not self.eligible(session, event):
            return
        tools = self.app.agent_tools_by_side.get(side)
        if tools is None:
            return
        profile = dict(self.app.gui.api_profiles[side])
        if not profile["endpoint"].strip() or not profile["model"].strip():
            self.app.report_agent_notice(f"Notes review skipped for {side}: configure an API URL and model.")
            return
        old_notes = tools.get_notes()["notes"]
        completed_check = condition_evidence(event, event["end_board"], "completed_check.end_board")
        completed_check["end_board"] = deepcopy(event["end_board"])
        completed_check.pop("end_board_changes")
        context = {"your_side": side, "completed_check": completed_check, "current_notes": old_notes,
                   "own_intents": [deepcopy(item) for item in session.flow.agent_intents.get(side, [])
                                   if item["turn_number"] <= event["turn_number"]][-8:],
                   "prior_condition_evidence": evidence_sequence(
                       [item for item in public_checks(session) if item["turn_number"] < event["turn_number"]],
                       event["end_board"], "completed_check.end_board")}
        content = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
        if profile["vision"]:
            images = tools.call("get_rule_images")
            if images.get("ok"):
                for item in images["images"]:
                    content.extend([{"type": "text", "text": f"Public condition clue {item['index']}"},
                                    {"type": "image_url", "image_url": {
                                        "url": "data:" + item["mime_type"] + ";base64," + item["data"]}}])
        payload = streaming_parameters(profile, profile_parameters(profile, {
            "model": profile["model"], "messages": [{"role": "system", "content": configured_prompt(self.app.gui, "notes_prompt")},
                {"role": "system", "content": json.dumps({"public_rules": tools.call("get_gi_rules")}, ensure_ascii=False)},
                {"role": "user", "content": content}],
            "tools": [tool for tool in tool_definitions() if tool["function"]["name"] == "update_notes"],
        }))
        request_id = uuid.uuid4().hex
        session.llm_usage.begin_request(request_id, side, purpose="notes")
        self.active = {"session": session, "side": side, "tools": tools, "event": event,
                       "profile": profile, "id": request_id, "old_notes": old_notes,
                       "reasoning": "", "reply": "", "started": time.monotonic(), "displayed": 0}
        try:
            completion_url(profile["endpoint"])
            self.active["request"] = self.request_factory(profile, payload)
            self.app.update_llm_request(session, request_id, turn=event["turn_number"])
        except Exception as error:
            self.finish({"error": f"Could not start notes review: {type(error).__name__}."})

    def finish(self, returned):
        job, self.active = self.active, None
        if job["session"] is not self.app.state.session:
            return
        data = returned.get("data")
        usage = data.get("usage") if isinstance(data, dict) else returned.get("usage")
        error = returned.get("error")
        reply, reasoning = job["reply"], job["reasoning"]
        if not error:
            try:
                choice = data["choices"][0]
                message = choice["message"]
                reply, reasoning = str(message.get("content") or ""), str(message.get("reasoning_content") or "")
                if choice.get("finish_reason") not in {None, "stop", "tool_calls"}:
                    raise ValueError("Reply did not finish normally")
                calls = message.get("tool_calls")
                if not isinstance(calls, list) or len(calls) != 1:
                    raise ValueError("Expected exactly one update_notes call")
                call = calls[0]
                if call.get("type") != "function" or call["function"]["name"] != "update_notes":
                    raise ValueError("Only update_notes is allowed")
                arguments = json.loads(call["function"]["arguments"])
                if (not isinstance(arguments, dict) or set(arguments) != {"text"}
                        or not isinstance(arguments["text"], str) or not arguments["text"].strip()
                        or len(arguments["text"]) > 12000):
                    raise ValueError("Invalid replacement notes")
                if job["session"].agent_notes.get(job["side"]) != job["old_notes"]:
                    raise ValueError("Notes changed while reviewing; retained the newer notes")
                result = job["tools"].call("update_notes", arguments)
                if not result.get("ok"):
                    raise ValueError("The notes binding is no longer active")
                reply += "\nUpdated notes\n" + arguments["text"]
            except (KeyError, TypeError, IndexError, ValueError, AttributeError) as exc:
                error = "Notes review failed: " + str(exc)
        event = job["event"]
        label = f"Review of {event['checked_player'].capitalize()} turn {event['turn_number']}\n"
        self.app.complete_llm_request(job["session"], job["id"], reasoning=reasoning,
                                      reply=label + reply + ("\n" + error if error else ""),
                                      usage=usage, status="failed" if error else "completed", turn=event["turn_number"])
        if error:
            self.app.report_agent_notice(error)
        self.app.refresh_exposure_notes()
        self.app.persist()

    def cancel(self):
        if self.active:
            job, self.active = self.active, None
            if job.get("request"):
                job["request"].cancel()
            self.app.complete_llm_request(job["session"], job["id"], status="stopped",
                                          reasoning=job["reasoning"], reply=job["reply"] + "\nNotes review stopped.",
                                          turn=job["event"]["turn_number"])
        self.queue.clear()
        if self.session is not None:
            self.seen = len(self.session.public_check_events)
