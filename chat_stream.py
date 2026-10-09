"""Read bounded Chat Completions SSE streams without executing model tools."""
import json


MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ChatStream:
    def __init__(self):
        self.content = []
        self.reasoning = []
        self.calls = {}
        self.finish_reason = None
        self.usage = None

    def add(self, chunk):
        if not isinstance(chunk, dict):
            raise ValueError("Invalid streaming event")
        if chunk.get("error"):
            error = chunk["error"]
            raise ValueError(str(error.get("message", "Stream failed") if isinstance(error, dict) else error))
        if chunk.get("usage") is not None:
            if not isinstance(chunk["usage"], dict):
                raise ValueError("Invalid streaming usage")
            self.usage = chunk["usage"]
        choices = chunk.get("choices", [])
        if not isinstance(choices, list):
            raise ValueError("Invalid streaming choices")
        progress = {"reasoning": "", "reply": ""}
        for choice in choices:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                raise ValueError("Unexpected streaming choice")
            delta = choice.get("delta", {})
            if delta is None:
                delta = {}
            if not isinstance(delta, dict) or (self.finish_reason is not None and delta):
                raise ValueError("Invalid streaming delta")
            if delta.get("role", "assistant") != "assistant":
                raise ValueError("Unexpected streaming role")
            for field, target, key in (("content", self.content, "reply"),
                                       ("reasoning_content", self.reasoning, "reasoning")):
                value = delta.get(field)
                if value is not None:
                    if not isinstance(value, str):
                        raise ValueError("Invalid streaming text")
                    target.append(value)
                    progress[key] += value
            calls = delta.get("tool_calls", [])
            if calls is None:
                calls = []
            if not isinstance(calls, list):
                raise ValueError("Invalid streaming tools")
            for fragment in calls:
                if not isinstance(fragment, dict):
                    raise ValueError("Invalid streaming tool")
                index = fragment.get("index")
                if type(index) is not int or not 0 <= index < 32:
                    raise ValueError("Invalid streaming tool index")
                call = self.calls.setdefault(index, {"id": "", "type": "function",
                                                    "function": {"name": "", "arguments": ""}})
                if fragment.get("type", "function") != "function":
                    raise ValueError("Unsupported streaming tool")
                if fragment.get("id") is not None:
                    value = fragment["id"]
                    if not isinstance(value, str) or (call["id"] and value != call["id"]):
                        raise ValueError("Invalid streaming tool identity")
                    call["id"] = value
                function = fragment.get("function") or {}
                if not isinstance(function, dict):
                    raise ValueError("Invalid streaming tool function")
                for field in ("name", "arguments"):
                    value = function.get(field)
                    if value is not None:
                        if not isinstance(value, str):
                            raise ValueError("Invalid streaming tool fragment")
                        call["function"][field] += value
            finish = choice.get("finish_reason")
            if finish is not None:
                if finish not in {"stop", "tool_calls", "length", "content_filter"}:
                    raise ValueError("Unexpected streaming finish reason")
                if self.finish_reason is not None and self.finish_reason != finish:
                    raise ValueError("Conflicting streaming finish reasons")
                self.finish_reason = finish
        return progress

    def result(self):
        if self.finish_reason is None:
            raise ValueError("Stream ended without a completion finish reason")
        message = {"role": "assistant", "content": "".join(self.content) or None}
        if self.reasoning:
            message["reasoning_content"] = "".join(self.reasoning)
        if self.calls:
            message["tool_calls"] = [self.calls[index] for index in sorted(self.calls)]
        return {"choices": [{"message": message, "finish_reason": self.finish_reason}], "usage": self.usage}


def read_chat_stream(response, emit, accumulator=None):
    """Emit text fragments; return a complete reply only after data: [DONE]."""
    accumulator = accumulator if accumulator is not None else ChatStream()
    total, event = 0, []
    while True:
        raw = response.readline(MAX_RESPONSE_BYTES + 1)
        if not raw:
            raise ValueError("Stream interrupted before its completion marker")
        total += len(raw)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError("Response too large")
        line = raw.decode("utf-8-sig").rstrip("\r\n")
        if line:
            if line.startswith("data:"):
                event.append(line[5:].lstrip(" "))
            continue  # Ignore SSE comments, ids and event labels.
        if not event:
            continue
        data, event = "\n".join(event), []
        if data == "[DONE]":
            return accumulator.result()
        progress = accumulator.add(json.loads(data))
        if progress["reasoning"] or progress["reply"]:
            emit({"event": "progress", **progress})
