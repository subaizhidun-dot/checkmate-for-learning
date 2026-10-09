"""Deterministic request context and lossless public condition evidence."""
from copy import deepcopy
import json


def board_changes(board, reference):
    """Cell replacements that reconstruct a historical board from a reference."""
    if board is None:
        return None
    return [{"at": [x, y], "piece": deepcopy(piece)}
            for y, row in enumerate(board) for x, piece in enumerate(row)
            if piece != reference[y][x]]


def condition_evidence(event, reference, board_reference="latest_board"):
    evidence = {key: deepcopy(value) for key, value in event.items()
                if key not in {"start_board", "end_board", "conditions"}}
    evidence["conditions"] = [{"condition_id": index, "result": result}
                              for index, result in enumerate(event["conditions"], 1)]
    evidence["board_reference"] = board_reference
    evidence["start_board_changes"] = board_changes(event.get("start_board"), reference)
    evidence["end_board_changes"] = board_changes(event.get("end_board"), reference)
    return evidence


def evidence_sequence(events, reference, board_reference="latest_board"):
    """Newest-first evidence uses adjacent historical boards as references."""
    evidence = []
    for event in reversed(events):
        evidence.append(condition_evidence(event, reference, board_reference))
        reference = event.get("start_board") or event["end_board"]
        board_reference = {"turn_number": event["turn_number"], "checked_player": event["checked_player"],
                           "board": "start_board" if event.get("start_board") is not None else "end_board"}
    return evidence


def public_checks(session):
    """Saved facts are independent of editable notes and newly queued reviews."""
    archive = session.flow.public_check_archive
    if archive:
        return archive
    return [session.flow.last_public_check] if session.flow.last_public_check else []


def context_tool_result(name, result):
    """Return context references when a fresh copy accompanies the next request."""
    context_keys = {"get_public_state": "get_public_state", "get_legal_actions": "get_legal_actions",
                    "get_gi_rules": "public_rules", "get_notes": "get_notes"}
    if result.get("ok") and name in context_keys:
        return {key: deepcopy(value) for key, value in result.items()
                if key in {"ok", "revision", "actor", "awaiting_other_side"}} | {
                    "context_key": context_keys[name]}
    if result.get("ok") and name == "update_notes":
        return {"ok": True, "notes_updated": True, "characters": len(result["notes"]),
                "context_key": "get_notes"}
    return deepcopy(result)


def decision_summary(messages, turn_number, revision_after):
    """Archive a completed conversation as executed actions and reply text."""
    operations, replies = [], []
    for index, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        results = {}
        for following in messages[index + 1:]:
            if following["role"] != "tool":
                break
            results[following["tool_call_id"]] = json.loads(following["content"])
        if message.get("content"):
            replies.append(message["content"])
        for call in message.get("tool_calls", []):
            name = call["function"]["name"]
            if name not in {"apply_action", "move_piece", "submit_plan", "update_notes", "record_intent"}:
                continue
            result = results[call["id"]]
            operation = {"tool": name, "result": result}
            if name != "update_notes":
                operation["arguments"] = json.loads(call["function"]["arguments"])
            operations.append(operation)
    summary = {"turn_number": turn_number, "revision_after": revision_after, "operations": operations}
    if messages and messages[0]["role"] == "user":
        summary["revision_before"] = json.loads(messages[0]["content"])["context_revision"]
    if replies:
        summary["replies"] = replies
    return summary
