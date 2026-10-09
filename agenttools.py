"""The model's explicit tool boundary, bound to one live game and one side.

agenthelper remains a trusted Python/session-management interface. Register
only TOOLS below with a model, and execute mutations on the desktop thread.
"""
from copy import deepcopy

from basicgame import is_player_accessible, get_legal_moves_for_piece
from gameengine import EngineError, encode_action_id, describe_coordinates
from game_rules import rules_for_game
from agent_prompts import NG_PLUS_NOTES


TOOLS = (
    "get_public_state", "get_legal_actions", "get_gi_rules", "get_rule_images",
    "get_recent_history", "get_notes", "update_notes", "record_intent", "move_piece", "apply_action", "submit_plan",
)


def initial_notes(session):
    if session.game.time_token_owner is None and session.flow.phase != "choose_token":
        return NG_PLUS_NOTES
    if session.game.gamemode in {2, 3}:
        return "Known special victory requirement: the opponent holds the time token. Other requirements are unknown; condition reviews will update these notes."
    return "Special victory requirements are unknown; condition reviews will update these notes."


class AgentTools:
    def __init__(self, session, color, *, image_provider=None, on_change=None, is_active=None):
        if color not in {"black", "white"}:
            raise ValueError("Agent side must be black or white")
        self.session = session
        self.color = color
        self.image_provider = image_provider
        self.on_change = on_change
        self.is_active = is_active or (lambda: True)
        self._plans = {}
        self._moves = {}

    def call(self, name, arguments=None):
        """Dispatch only named tools; errors never expose source or file paths."""
        if name not in TOOLS:
            return {"ok": False, "code": "unknown_tool", "message": "Tool is not available."}
        revision_before = self.session.revision
        try:
            if not self.is_active():
                return {"ok": False, "code": "stale_game", "message": "This game has been replaced."}
            result = getattr(self, name)(**(arguments or {}))
        except EngineError as error:
            result = {"ok": False, "code": error.code, "message": "Action rejected. Refresh the current state and legal actions."}
        except (TypeError, ValueError, KeyError):
            result = {"ok": False, "code": "invalid_argument", "message": "Invalid tool arguments."}
        except Exception:
            result = {"ok": False, "code": "tool_failed", "message": "Tool could not complete."}
        return self._action_result(result, revision_before) if name in {"apply_action", "move_piece", "submit_plan"} else deepcopy(result)

    def _action_result(self, result, revision_before):
        """A receipt for execution; the request supplies the authoritative board."""
        result = deepcopy(result)
        result.setdefault("revision_before", revision_before)
        result.setdefault("revision_after", self.session.revision)
        result.update(current_revision=self.session.revision, phase=self.session.flow.phase,
                      actor=self.session.actor(), current_ap=self.session.game.current_ap)
        if "stop_reason" not in result:
            phase = self.session.flow.phase
            result["stop_reason"] = (result.get("code", "rejected") if not result.get("ok") else
                                     phase if phase in {"checking_win", "time_wish", "game_over"} else
                                     "awaiting_other_side" if not self._can_decide() else "completed")
        return result

    def get_public_state(self):
        snapshot = self.session.snapshot()
        public = {key: snapshot[key] for key in (
            "gamemode", "revision", "phase", "actor", "current_player", "time_token_owner",
            "current_ap", "max_ap", "board", "new_pieces", "finished", "new_game_plus_available",
        )}
        public["your_side"] = self.color
        public["coordinates"] = describe_coordinates()
        public["players"] = {
            color: {key: player[key] for key in ("num_pieces", "num_squirrels", "has_elephant",
                                                "has_lion", "has_mole", "has_butterfly")}
            for color, player in snapshot["players"].items()
        }
        public["pending"] = {key: snapshot["pending"][key] for key in (
            "pending_action", "selected_pos", "selected_resource", "selected_cost",
            "pending_refund_owner", "pending_refund_count", "pending_refund_kind", "pending_piece_limit_owner",
        )}
        public["result"] = {key: snapshot["result"][key] for key in ("status", "winner", "loser", "reason")}
        public["playable_cells"] = [[x, y] for y in range(7) for x in range(9)
                                   if is_player_accessible(self.session.game, (x, y))]
        if snapshot["gamemode"] == 2:
            public["tree_markers"] = snapshot["tree_markers"]
            public["expanded_corners"] = snapshot["expanded_corners"]
        results = snapshot["condition_results"]
        public["visible_condition_feedback"] = (
            results[:self.session.condition_reveal_count] if results is not None else None
        )
        public["last_completed_check"] = self.session.flow.last_public_check
        return {"ok": True, **deepcopy(public)}

    def get_legal_actions(self):
        actor = self.session.actor()
        can_decide = self._can_decide()
        actions = self.session.legal_actions() if can_decide else []
        sources = {tuple(action["params"]["at"]) for action in actions if action["type"] == "select_piece"}
        if can_decide and self.session.flow.phase == "select_target" and self.session.interaction.selected_pos is not None:
            sources.add(tuple(self.session.interaction.selected_pos))
        game = self.session.game
        moves = [{"source": list(source), "target": list(target),
                  "ap": 1 if game.get_piece(source).owner == game.current_player else 2}
                 for source in sorted(sources)
                 for target in sorted(get_legal_moves_for_piece(game, self.session.players, source))]
        return {"ok": True, "revision": self.session.revision, "actor": actor,
                "awaiting_other_side": not can_decide,
                "actions": deepcopy(actions), "moves": moves}

    def _can_decide(self):
        actor = self.session.actor()
        return actor == self.color and self.session.flow.phase not in {"choose_token", "time_wish"}

    def get_gi_rules(self):
        return {"ok": True, **rules_for_game(self.session.game.gamemode)}

    def get_rule_images(self, index=None):
        if index is not None and (type(index) is not int or index < 1):
            raise ValueError("Invalid clue index")
        if self.image_provider is None:
            return {"ok": False, "code": "images_unavailable", "message": "Rule images are unavailable."}
        images = self.image_provider()
        if index is not None:
            if index > len(images):
                raise ValueError("Invalid clue index")
            images = [images[index - 1]]
        return {"ok": True, "gamemode": self.session.game.gamemode, "images": deepcopy(images)}

    def get_recent_history(self, limit=10):
        if type(limit) is not int or not 0 <= limit <= 50:
            raise ValueError("Invalid history limit")
        entries = self.session.recent_log(limit)
        return {"ok": True, "actions": [{key: entry[key] for key in (
            "actor", "action_type", "params", "revision_before", "revision_after", "ok",
        )} for entry in entries]}

    def get_notes(self):
        old_default = "Without a time token, special victory is impossible. Focus on mating the opponent."
        no_token_game = self.session.game.time_token_owner is None and self.session.flow.phase != "choose_token"
        if (self.color not in self.session.agent_notes
                or (no_token_game and self.session.agent_notes[self.color] == old_default)):
            self.session.agent_notes[self.color] = initial_notes(self.session)
        return {"ok": True, "notes": self.session.agent_notes[self.color]}

    def record_intent(self, text):
        """Private predictions for later comparison with public check results."""
        if not self._can_decide() or self.session.game.time_token_owner is None:
            return {"ok": False, "code": "intent_unavailable"}
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Invalid intent")
        items = self.session.flow.agent_intents.setdefault(self.color, [])
        item = {"turn_number": self.session.turn_number, "revision": self.session.revision, "text": text}
        if not items or items[-1] != item:
            items.append(item)
        del items[:-32]
        return {"ok": True, "intent_recorded": True, **item}

    def update_notes(self, text):
        if not isinstance(text, str) or len(text) > 12000:
            raise ValueError("Invalid notes")
        self.session.agent_notes[self.color] = text
        return {"ok": True, "notes": text}

    def apply_action(self, action_id, revision, request_id=None):
        if self.session.flow.phase == "time_wish":
            return {"ok": False, "code": "human_only_phase", "message": "The user must complete the time wish."}
        if not self._can_decide():
            return {"ok": False, "code": "awaiting_other_side"}
        response = self.session.submit(action_id, revision, request_id=request_id, actor=self.color)
        if not response["replayed"] and self.on_change:
            self.on_change()
        result = {"ok": True, "replayed": response["replayed"], "action_id": action_id,
                  "revision_after": response["revision"]}
        if response["code"] == "butterfly_extra_turn":
            result["stop_reason"] = "extra_turn"
        return self._action_result(result, revision)

    def move_piece(self, source, target, revision, request_id):
        """Move without exposing the human interface's selection clicks.

        Validate the complete move before selecting anything. Both engine
        actions still use the normal legality and revision checks.
        """
        if (type(revision) is not int or not isinstance(request_id, str) or not request_id
                or any(not isinstance(pos, list) or len(pos) != 2
                       or any(type(n) is not int for n in pos) for pos in (source, target))):
            raise ValueError("Invalid move")
        signature = {"source": list(source), "target": list(target), "revision": revision}
        if self.session.flow.phase == "time_wish":
            return {"ok": False, "code": "human_only_phase"}
        if not self._can_decide():
            return {"ok": False, "code": "awaiting_other_side"}
        if request_id in self._moves:
            previous, result = self._moves[request_id]
            if signature != previous:
                return {"ok": False, "code": "request_conflict"}
            return self._action_result({**result, "replayed": True}, revision)
        if revision != self.session.revision:
            return self._action_result({"ok": False, "code": "revision_mismatch"}, self.session.revision)
        legal = self.get_legal_actions()
        if not any(move["source"] == source and move["target"] == target for move in legal["moves"]):
            return self._action_result({"ok": False, "code": "illegal_action"}, self.session.revision)
        actions = []
        if self.session.flow.phase == "playing":
            actions.append(encode_action_id("select_piece", {"at": source}))
        actions.append(encode_action_id("move", {"from": source, "to": target}))
        executed = []
        for action_id in actions:
            result = self.call("apply_action", {"action_id": action_id, "revision": self.session.revision})
            if not result["ok"]:
                payload = {**result, "executed": executed, "revision_before": revision}
                break
            executed.append(action_id)
        else:
            payload = {"ok": True, "replayed": False, "executed": executed}
        payload = self._action_result(payload, revision)
        self._moves[request_id] = (signature, deepcopy(payload))
        if len(self._moves) > 128:
            self._moves.pop(next(iter(self._moves)))
        return payload

    def submit_plan(self, actions, revision, request_id):
        if self.session.flow.phase == "time_wish":
            return {"ok": False, "code": "human_only_phase", "message": "The user must complete the time wish."}
        if (not isinstance(actions, list) or not 1 <= len(actions) <= 32
                or not isinstance(request_id, str) or not request_id or type(revision) is not int):
            raise ValueError("Invalid plan")
        signature = {"actions": deepcopy(actions), "revision": revision}
        if request_id in self._plans:
            previous, result = self._plans[request_id]
            if signature != previous:
                return {"ok": False, "code": "request_conflict"}
            return self._action_result({**result, "replayed": True}, revision)
        if revision != self.session.revision:
            return self._action_result({"ok": False, "code": "revision_mismatch"}, self.session.revision)
        ids = []
        for action in actions:
            if (not isinstance(action, dict) or set(action) - {"type", "params"}
                    or not isinstance(action.get("type"), str) or not isinstance(action.get("params", {}), dict)):
                raise ValueError("Invalid planned action")
            ids.append(encode_action_id(action["type"], action.get("params", {})))
        executed = []
        stop_reason = "completed"
        for action_id in ids:
            if not self._can_decide():
                stop_reason = "awaiting_other_side"
                break
            result = self.call("apply_action", {"action_id": action_id, "revision": self.session.revision})
            if not result["ok"]:
                stop_reason = result["code"]
                break
            executed.append(action_id)
            if self.session.flow.phase in {"checking_win", "time_wish", "game_over"}:
                stop_reason = self.session.flow.phase
                break
            if action_id.split(":", 1)[0] == "butterfly_extra_turn":
                stop_reason = "extra_turn"
                break
            if self.session.actor() != self.color:
                stop_reason = "awaiting_other_side"
                break
        payload = self._action_result({"ok": True, "replayed": False, "executed": executed,
                                       "stop_reason": stop_reason}, revision)
        self._plans[request_id] = (signature, deepcopy(payload))
        if len(self._plans) > 128:
            self._plans.pop(next(iter(self._plans)))
        return payload
