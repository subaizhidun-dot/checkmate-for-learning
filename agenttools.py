"""The model's explicit tool boundary, bound to one live game and one side.

agenthelper remains a trusted Python/session-management interface. Register
only TOOLS below with a model, and execute mutations on the desktop thread.
"""
from copy import deepcopy

from basicgame import is_player_accessible
from gameengine import EngineError, encode_action_id, describe_coordinates
from game_rules import rules_for_game
from agent_prompts import NG_PLUS_NOTES


TOOLS = (
    "get_public_state", "get_legal_actions", "get_gi_rules", "get_rule_images",
    "get_recent_history", "get_notes", "update_notes", "apply_action", "submit_plan",
)


def initial_notes(session):
    if session.game.time_token_owner is None and session.flow.phase != "choose_token":
        return NG_PLUS_NOTES
    if session.game.gamemode in {2, 3}:
        return "Known special victory requirement: the opponent holds the time token. Continue inferring the other requirements from the clue images and visible feedback."
    return "Continue inferring the special victory requirements from the clue images and visible feedback."


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

    def call(self, name, arguments=None):
        """Dispatch only named tools; errors never expose source or file paths."""
        if name not in TOOLS:
            return {"ok": False, "code": "unknown_tool", "message": "Tool is not available."}
        try:
            if not self.is_active():
                return {"ok": False, "code": "stale_game", "message": "This game has been replaced."}
            return deepcopy(getattr(self, name)(**(arguments or {})))
        except EngineError as error:
            return {"ok": False, "code": error.code, "message": "Action rejected. Refresh the current state and legal actions."}
        except (TypeError, ValueError, KeyError):
            return {"ok": False, "code": "invalid_argument", "message": "Invalid tool arguments."}
        except Exception:
            return {"ok": False, "code": "tool_failed", "message": "Tool could not complete."}

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
        return {"ok": True, **deepcopy(public)}

    def get_legal_actions(self):
        actor = self.session.actor()
        can_decide = self._can_decide()
        return {"ok": True, "revision": self.session.revision, "actor": actor,
                "awaiting_other_side": not can_decide,
                "actions": deepcopy(self.session.legal_actions()) if can_decide else []}

    def _can_decide(self):
        actor = self.session.actor()
        return actor == self.color and self.session.flow.phase != "choose_token"

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

    def update_notes(self, text):
        if not isinstance(text, str) or len(text) > 12000:
            raise ValueError("Invalid notes")
        self.session.agent_notes[self.color] = text
        return {"ok": True, "notes": text}

    def apply_action(self, action_id, revision, request_id=None):
        if not self._can_decide():
            return {"ok": False, "code": "awaiting_other_side"}
        response = self.session.submit(action_id, revision, request_id=request_id, actor=self.color)
        if not response["replayed"] and self.on_change:
            self.on_change()
        # Do not forward submit's internal state snapshot, including on retries.
        return {"ok": True, "replayed": response["replayed"], "action_id": action_id,
                "state": self.get_public_state()}

    def submit_plan(self, actions, revision, request_id):
        if (not isinstance(actions, list) or not 1 <= len(actions) <= 32
                or not isinstance(request_id, str) or not request_id or type(revision) is not int):
            raise ValueError("Invalid plan")
        signature = {"actions": deepcopy(actions), "revision": revision}
        if request_id in self._plans:
            previous, result = self._plans[request_id]
            if signature != previous:
                return {"ok": False, "code": "request_conflict"}
            return {**deepcopy(result), "replayed": True, "state": self.get_public_state()}
        if revision != self.session.revision:
            return {"ok": False, "code": "revision_mismatch", "state": self.get_public_state()}
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
        payload = {"ok": True, "replayed": False, "executed": executed, "stop_reason": stop_reason,
                   "state": self.get_public_state()}
        self._plans[request_id] = (signature, deepcopy(payload))
        if len(self._plans) > 128:
            self._plans.pop(next(iter(self._plans)))
        return payload
