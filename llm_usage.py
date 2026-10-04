"""Per-game numeric API usage. Model text belongs only to the live UI."""
from copy import deepcopy


class LLMUsage:
    def __init__(self):
        self.requests = []
        self._by_id = {}
        self.revision = 0

    def begin_request(self, request_id, side, turn=None):
        if not isinstance(request_id, str) or not request_id or side not in {"black", "white"}:
            raise ValueError("Invalid request identity")
        if turn is not None and (type(turn) is not int or turn < 1):
            raise ValueError("Invalid turn")
        if request_id in self._by_id:
            if self._by_id[request_id]["side"] != side:
                raise ValueError("Request identity belongs to another side")
            return False
        entry = {"request_id": request_id, "side": side, "turn": turn, "status": "pending",
                 "input_tokens": None, "output_tokens": None, "total_tokens": None}
        self.requests.append(entry)
        self._by_id[request_id] = entry
        self.revision += 1
        return True

    def finish_request(self, request_id, usage=None, status="completed"):
        entry = self._by_id[request_id]
        if entry["status"] != "pending":
            return False
        if status not in {"completed", "failed", "stopped"}:
            raise ValueError("Invalid request status")
        raw = usage if isinstance(usage, dict) else {}
        tokens = {}
        for key, alias in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"),
                           ("total_tokens", "total_tokens")):
            value = raw.get(key, raw.get(alias))
            tokens[key] = value if type(value) is int and value >= 0 else None
        if tokens["total_tokens"] is None and all(tokens[key] is not None for key in ("input_tokens", "output_tokens")):
            tokens["total_tokens"] = tokens["input_tokens"] + tokens["output_tokens"]
        entry.update(tokens)
        entry["status"] = status
        self.revision += 1
        return True

    def request_info(self, request_id):
        return deepcopy(self._by_id[request_id])

    def summary(self, side=None):
        entries = [entry for entry in self.requests if side is None or entry["side"] == side]
        turns = len({(entry["side"], entry["turn"]) for entry in entries if entry["turn"] is not None})
        total = sum(entry["total_tokens"] or 0 for entry in entries)
        unknown = sum(entry["total_tokens"] is None for entry in entries)
        def component(key):
            return None if any(entry[key] is None for entry in entries) else sum(entry[key] for entry in entries)
        return {"llm_turns": turns, "requests": len(entries),
                "input_tokens": component("input_tokens"),
                "output_tokens": component("output_tokens"),
                "total_tokens": total, "unreported_requests": unknown,
                "average_tokens_per_turn": total / turns if turns and not unknown else None,
                "average_tokens_per_request": total / len(entries) if entries and not unknown else None}

    def to_dict(self):
        return {"by_side": {side: self.summary(side) for side in ("black", "white")},
                "total": self.summary(), "request_usage": deepcopy(self.requests)}

    @classmethod
    def from_dict(cls, data):
        result = cls()
        if not isinstance(data, dict):
            return result
        for item in data.get("request_usage", []):
            if not isinstance(item, dict):
                continue
            try:
                fresh = result.begin_request(item["request_id"], item["side"], item.get("turn"))
                if fresh:
                    status = item.get("status", "stopped")
                    result.finish_request(item["request_id"], item,
                                          status if status in {"completed", "failed", "stopped"} else "stopped")
            except (ValueError, KeyError):
                continue
        return result

    def game_summary_text(self, winner):
        lines = ["Game Summary", f"Winner: {winner.capitalize()}"]
        for side, title in (("black", "Black"), ("white", "White"), (None, "Total")):
            summary = self.summary(side)
            def average(key):
                value = summary[key]
                return f"{value:.1f}" if value is not None else "N/A"
            def token(key):
                value = summary[key]
                return str(value) if value is not None else "Not reported"
            total_label = "known total" if summary["unreported_requests"] else "total"
            lines.extend([f"{title}: {summary['llm_turns']} LLM turns, {summary['requests']} requests",
                          f"Tokens: input {token('input_tokens')}, output {token('output_tokens')}, {total_label} {summary['total_tokens']}",
                          f"Average/request: {average('average_tokens_per_request')}",
                          f"Average/turn: {average('average_tokens_per_turn')}"])
            if summary["unreported_requests"]:
                lines.append(f"Usage incomplete: {summary['unreported_requests']} requests without reported totals.")
        return "\n".join(lines)
