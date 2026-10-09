"""Headless CheckMate engine for G1, G2 and G3.

This module owns the whole game flow: phase transitions, action execution,
transactions, turn resolution and win/mate resolution.  It imports neither
pygame nor any graphical module, so the same code drives the desktop window
(main.py) and the scripted agent interface (agenthelper.py).

The desktop interface keeps only rendering, click hit-testing and the
victory-condition reveal animation.  Rule resolution never waits for a window,
a frame loop or an animation timer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from basicgame import (
    Chessboard,
    Interaction,
    Playerstate,
    Resource,
    SPECIAL_KINDS,
    SELL_REFUND,
    MODE_SPECIALS,
    can_buy_special,
    can_sell_special,
    can_select_piece,
    can_select_resource,
    get_empty_accessible_cells,
    get_legal_board_targets,
    get_legal_camp_targets,
    get_legal_moves_for_piece,
    get_legal_resource_targets,
    is_empty_accessible_cell,
    is_player_accessible,
)
import ui_text
from llm_usage import LLMUsage
from game1 import create_g1_board, get_g1_condition_results
from game2 import TREE_STARTS, create_g2_board, get_g2_condition_results, tree_action, tree_action_cost, shift_corner
from game3 import G3Turn, create_g3_board, get_g3_condition_results, can_start_extra_turn


Position = tuple[int, int]

# Phases (identical to the names the graphical interface already used).
PHASE_MENU = "menu"
PHASE_CHOOSE_TOKEN = "choose_token"
PHASE_PLAYING = "playing"
PHASE_SELECT_RESOURCE = "select_resource"
PHASE_SELECT_COST = "select_cost"
PHASE_SELECT_TARGET = "select_target"
PHASE_PENDING_ELEPHANT_BONUS = "pending_elephant_bonus"
PHASE_PENDING_REFUND = "pending_refund"
PHASE_PENDING_PIECE_LIMIT = "pending_piece_limit"
PHASE_CHECKING_WIN = "checking_win"
PHASE_TIME_WISH = "time_wish"
PHASE_GAME_OVER = "game_over"

# Internal pending-action labels.
ACTION_MOVE = "move"
ACTION_BUY_ELEPHANT = "buy_elephant"
ACTION_BUY_LION = "buy_lion"
ACTION_PLACE_SQUIRREL = "place_squirrel"
ACTION_SELL_PIECE = "sell_piece"
ACTION_MOVE_TIME_TOKEN = "move_time_token"

# Agent-facing action types.  The engine and the agent interface share this
# vocabulary, so the two interfaces cannot drift apart.
ACTION_TYPE_MOVE = "move"
ACTION_TYPE_PLACE_SQUIRREL = "place_squirrel"
ACTION_TYPE_BUY_ELEPHANT = "buy_elephant"
ACTION_TYPE_BUY_LION = "buy_lion"
ACTION_TYPE_SELL_ELEPHANT = "sell_elephant"
ACTION_TYPE_SELL_LION = "sell_lion"
ACTION_TYPE_SELECT_COST = "select_cost"
ACTION_TYPE_PLACE_REFUND = "place_refund"
ACTION_TYPE_PLACE_ELEPHANT_BONUS = "place_elephant_bonus"
ACTION_TYPE_SKIP_ELEPHANT_BONUS = "skip_elephant_bonus"
ACTION_TYPE_CHOOSE_INITIAL_TOKEN = "choose_initial_token"
ACTION_TYPE_TIME_WISH = "time_wish"
ACTION_TYPE_MOVE_TIME_TOKEN = "move_time_token"
ACTION_TYPE_FAST_TIME_TOKEN = "fast_time_token"
ACTION_TYPE_REMOVE_PIECE_LIMIT = "remove_piece_limit"
ACTION_TYPE_CANCEL = "cancel"
ACTION_TYPE_SELECT_PIECE = "select_piece"

BUY_COST = {"elephant": 2, "lion": 4, "mole": 2, "butterfly": 4}
REVERSED_BUY = {kind: f"buy_{kind}" for kind in BUY_COST}
MODE_CONDITIONS = {1: get_g1_condition_results, 2: get_g2_condition_results, 3: get_g3_condition_results}

# Stable error codes returned to the agent.  They never change the game state.
ERR_INVALID_ARGUMENT = "invalid_argument"
ERR_UNKNOWN_SESSION = "unknown_session"
ERR_NO_ACTIVE_GAME = "no_active_game"
ERR_UNKNOWN_ACTION = "unknown_action"
ERR_ILLEGAL_ACTION = "illegal_action"
ERR_WRONG_ACTOR = "wrong_actor"
ERR_REVISION_MISMATCH = "revision_mismatch"
ERR_REQUEST_CONFLICT = "request_conflict"
ERR_SAVE_FAILED = "save_failed"
ERR_LOAD_FAILED = "load_failed"
ERR_SESSION_ENDED = "session_ended"


class EngineError(Exception):
    """Structured, JSON-friendly error raised before any state change."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = dict(self.details)
        return payload


def other_player(player: str) -> str:
    return "white" if player == "black" else "black"


def make_players_from_board(board: list[list[Any]]) -> dict[str, Playerstate]:
    players = {color: Playerstate(color, 0, 0) for color in ("black", "white")}
    for player in players.values():
        player.refresh_pieces(board)
    return players


def serialize_piece(piece: Resource | None) -> dict[str, Any] | None:
    if piece is None:
        return None
    result: dict[str, Any] = {"owner": str(piece.owner), "kind": str(piece.kind)}
    if piece.kind == "tree":
        result["rooted"] = piece.rooted
    return result


def serialize_board(board: list[list[Any]]) -> list[list[dict[str, Any] | None]]:
    return [[serialize_piece(piece) for piece in row] for row in board]


def serialize_position(position: Position | None) -> list[int] | None:
    return None if position is None else [int(position[0]), int(position[1])]


@dataclass
class GameFlow:
    phase: str = PHASE_MENU
    pending_action: str | None = None
    pending_piece_limit_owner: str | None = None
    pending_refund_owner: str | None = None
    pending_refund_count: int = 0
    pending_refund_kind: str | None = None
    last_condition_results: list[bool] | None = None
    time_wish_winner: str | None = None
    checking_action_message: str = ""
    temp_removed: list[tuple[Position, Resource]] = field(default_factory=list)
    temp_placed: list[Position] = field(default_factory=list)
    temp_new_pieces: list[Position] | None = None
    last_public_check: dict | None = None
    public_check_archive: list[dict] = field(default_factory=list)
    turn_start_board: list | None = None
    turn_actions: list[dict] = field(default_factory=list)
    agent_intents: dict = field(default_factory=dict)


@dataclass
class ActionLogEntry:
    """One applied action, kept for replaying and debugging a session."""

    revision_before: int
    revision_after: int
    actor: str | None
    action_type: str
    params: dict[str, Any]
    request_id: str | None
    ok: bool
    error_code: str | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "revision_before": self.revision_before,
            "revision_after": self.revision_after,
            "actor": self.actor,
            "action_type": self.action_type,
            "params": dict(self.params),
            "request_id": self.request_id,
            "ok": self.ok,
            "error_code": self.error_code,
            "detail": self.detail,
        }

class GameSession:
    """One game session, independent of any interface.

    Entry points:
    - ``submit`` executes one action id against an expected revision.
    - ``apply_action`` executes an already validated action by type.
    - ``select_board`` / ``select_resource`` / ``select_camp`` / ``cancel``
      are the interface-level clicks the graphical window forwards.
    An action either completes or raises :class:`EngineError` before the
    state changes; nothing here blocks on a window or a frame loop.
    """

    MAX_REQUEST_CACHE = 256
    MAX_LOG_ENTRIES = 500

    def __init__(
        self,
        game: Chessboard | None = None,
        players: dict[str, Playerstate] | None = None,
        flow: GameFlow | None = None,
        interaction: Interaction | None = None,
        session_id: str | None = None,
        agent_color: str | None = None,
        resolve_checks_immediately: bool = True,
    ) -> None:
        self.session_id = session_id or "local"
        self.agent_color = agent_color
        self.resolve_checks_immediately = resolve_checks_immediately
        self.revision = 0
        self.request_cache: dict[str, dict[str, Any]] = {}
        self.action_log: list[ActionLogEntry] = []
        self.agent_notes: dict[str, str] = {}
        self.public_check_events: list[dict] = []
        self.llm_usage = LLMUsage()
        self.turn_number = 1
        self.pending_message = ""
        self.condition_results: list[bool] | None = None
        self.condition_reveal_count = 0
        self.mat_loser: str | None = None
        self.game_over_reason: str | None = None
        self.finished = False
        self.wish_continuation_path: Path | None = None
        self.wish_save_error: str | None = None
        self.wish_save_directory: str | Path | None = None

        self.interaction = interaction or Interaction()
        self.flow = flow or GameFlow()
        if game is None:
            self.game: Chessboard | None = None
            self.players: dict[str, Playerstate] = {}
        else:
            self.game = game
            self.mat_loser = getattr(game, "mate_loser", None)
            self.players = players if players is not None else make_players_from_board(game.board_matrix)
            if players is None:
                self.refresh_players()
        self.bind_runtime_links()
        if game is not None and self.flow.turn_start_board is None:
            self.flow.turn_start_board = serialize_board(game.board_matrix)

    # ------------------------------------------------------------------
    # Setup and bookkeeping
    # ------------------------------------------------------------------
    @classmethod
    def new_g1(
        cls,
        session_id: str | None = None,
        agent_color: str | None = None,
        resolve_checks_immediately: bool = True,
    ) -> "GameSession":
        board = create_g1_board()
        game = Chessboard(1, board)
        players = make_players_from_board(board)
        session = cls(
            game=game,
            players=players,
            flow=GameFlow(phase=PHASE_CHOOSE_TOKEN),
            interaction=Interaction(),
            session_id=session_id,
            agent_color=agent_color,
            resolve_checks_immediately=resolve_checks_immediately,
        )
        session.set_message(ui_text.G1_STARTED_DIALOG)
        return session

    def bind_runtime_links(self) -> None:
        if self.game is not None:
            self.game.interaction = self.interaction
            self.game.players = self.players

    @classmethod
    def new_g2(cls, session_id=None, agent_color=None, resolve_checks_immediately=True):
        board = create_g2_board()
        session = cls(
            game=Chessboard(2, board), players=make_players_from_board(board),
            flow=GameFlow(phase=PHASE_CHOOSE_TOKEN), interaction=Interaction(),
            session_id=session_id, agent_color=agent_color,
            resolve_checks_immediately=resolve_checks_immediately,
        )
        session.set_message(ui_text.G2_STARTED_DIALOG)
        return session

    def refresh_players(self) -> None:
        if self.game is None:
            return
        for player in self.players.values():
            player.refresh_side(self.game)

    @classmethod
    def new_g3(cls, session_id=None, agent_color=None, resolve_checks_immediately=True):
        board = create_g3_board()
        session = cls(game=Chessboard(3, board), players=make_players_from_board(board),
                      flow=GameFlow(phase=PHASE_CHOOSE_TOKEN), interaction=Interaction(),
                      session_id=session_id, agent_color=agent_color,
                      resolve_checks_immediately=resolve_checks_immediately)
        session.set_message("G3 started. Choose the initial time token holder.")
        return session

    def _record_g3_action(self, move=False, resources_changed=False):
        if self.game.gamemode == 3:
            self.game.g3_turn.move_count += int(move)
            self.game.g3_turn.resources_changed |= resources_changed

    def can_start_new_game_plus(self) -> bool:
        """NG+ follows a completed G2/G3 wish that destroyed the time token."""
        return (
            self.game is not None
            and self.game.gamemode in {2, 3}
            and self.flow.phase == PHASE_GAME_OVER
            and self.game_over_reason == "time_wish"
            and self.game.time_token_owner is None
            and self.result()["winner"] in {"black", "white"}
            and any(piece is not None and piece.is_piece and piece.owner in {"black", "white"}
                    for row in self.game.board_matrix for piece in row)
        )

    @classmethod
    def new_game(cls, gamemode: int, previous_session=None, **kwargs):
        """Start the chosen base position, using NG+ when its source qualifies."""
        if gamemode not in {1, 2, 3}:
            raise EngineError(ERR_INVALID_ARGUMENT, "gamemode must be 1, 2 or 3")
        factory = {1: cls.new_g1, 2: cls.new_g2, 3: cls.new_g3}[gamemode]
        session = factory(**kwargs)
        if previous_session is not None and previous_session.can_start_new_game_plus():
            session.flow.phase = PHASE_PLAYING
            session.set_message(f"Game {gamemode}+ started. Black starts without the time token.")
        return session

    def set_message(self, text: str) -> None:
        self.pending_message = text
        self.interaction.set_dialog_text(text)

    def _touch(self) -> None:
        self.revision += 1

    def _log(
        self,
        actor: str | None,
        action_type: str,
        params: dict[str, Any],
        request_id: str | None,
        revision_before: int,
        ok: bool,
        error_code: str | None = None,
        detail: str = "",
        checks_before: int | None = None,
    ) -> None:
        self.action_log.append(
            ActionLogEntry(
                revision_before=revision_before,
                revision_after=self.revision,
                actor=actor,
                action_type=action_type,
                params=dict(params),
                request_id=request_id,
                ok=ok,
                error_code=error_code,
                detail=detail,
            )
        )
        if len(self.action_log) > self.MAX_LOG_ENTRIES:
            del self.action_log[: len(self.action_log) - self.MAX_LOG_ENTRIES]
        # Immediate checks run inside the action handler, before its log entry.
        # Complete their evidence now and keep this action out of the next turn.
        entry = self.action_log[-1].as_dict()
        public_action = {key: entry[key] for key in ("actor", "action_type", "params", "revision_after")}
        completed = self.public_check_events[checks_before:] if checks_before is not None else []
        if completed:
            for event in completed:
                event["actions"].append(public_action)
        else:
            self.flow.turn_actions.append(public_action)

    def recent_log(self, limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        return [entry.as_dict() for entry in self.action_log[-limit:]]

    def _cache_request(self, request_id: str, action_id: str, result: dict[str, Any]) -> None:
        self.request_cache[request_id] = {"action_id": action_id, "result": dict(result)}
        while len(self.request_cache) > self.MAX_REQUEST_CACHE:
            oldest = next(iter(self.request_cache))
            del self.request_cache[oldest]

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------
    def is_over(self) -> bool:
        return self.flow.phase == PHASE_GAME_OVER or self.finished

    def actor(self) -> str | None:
        """Who must decide the next step, or None when nothing is pending."""
        game = self.game
        if game is None:
            return None
        phase = self.flow.phase
        if phase == PHASE_CHOOSE_TOKEN:
            return "initial_token_choice"
        if phase == PHASE_TIME_WISH:
            return game.time_token_owner or "time_token_holder"
        if phase == PHASE_PENDING_PIECE_LIMIT:
            return self.flow.pending_piece_limit_owner
        if phase == PHASE_PENDING_REFUND:
            return self.flow.pending_refund_owner or game.current_player
        if phase in {PHASE_GAME_OVER, PHASE_MENU, PHASE_CHECKING_WIN}:
            return None
        return game.current_player

    def _empty_actionable_positions(self) -> list[Position]:
        game = self.game
        if game is None:
            return []
        return sorted(get_empty_accessible_cells(game))

    def _piece_limit_targets(self, owner: str | None) -> list[Position]:
        game = self.game
        if game is None or owner is None:
            return []
        targets: list[Position] = []
        for y, row in enumerate(game.board_matrix):
            for x, piece in enumerate(row):
                if (piece is not None and piece.owner == owner and piece.is_piece
                        and piece.kind != "tree"):
                    targets.append((x, y))
        return sorted(targets)

    def _buy_cost_targets(self) -> list[Position]:
        game = self.game
        if game is None:
            return []
        selected = {tuple(pos) for pos in self.interaction.selected_cost}
        targets: list[Position] = []
        for y, row in enumerate(game.board_matrix):
            for x, piece in enumerate(row):
                if (
                    piece is not None
                    and piece.owner == game.current_player
                    and piece.kind == "squirrel"
                    and (x, y) not in selected
                ):
                    targets.append((x, y))
        return sorted(targets)

    def _elephant_bonus_targets(self) -> list[Position]:
        game = self.game
        bonus_position = self.interaction.elephant_start_position
        if game is None or bonus_position is None:
            return []
        if is_empty_accessible_cell(game, bonus_position):
            return [tuple(bonus_position)]
        return []

    def _move_time_token_allowed(self) -> bool:
        game = self.game
        if game is None:
            return False
        if game.time_token_owner not in {"black", "white"}:
            return False
        if game.current_ap < 1:
            return False
        return not any(player.num_pieces > 7 for player in self.players.values())

    # ------------------------------------------------------------------
    # Legal actions (dynamic whitelist, recomputed from the board)
    # ------------------------------------------------------------------
    def legal_actions(self) -> list[dict[str, Any]]:
        """The immediate next-step whitelist for the current revision."""
        game = self.game
        actions: list[dict[str, Any]] = []
        if game is None:
            return actions
        phase = self.flow.phase

        if phase == PHASE_CHOOSE_TOKEN:
            for side in ("black", "white"):
                actions.append(self._action(ACTION_TYPE_CHOOSE_INITIAL_TOKEN, {"side": side}))
            return self._sorted(actions)

        if phase in {PHASE_GAME_OVER, PHASE_CHECKING_WIN}:
            return actions

        if phase == PHASE_PENDING_PIECE_LIMIT:
            for position in self._piece_limit_targets(self.flow.pending_piece_limit_owner):
                actions.append(self._action(ACTION_TYPE_REMOVE_PIECE_LIMIT, {"at": list(position)}))
            return self._sorted(actions)

        if phase == PHASE_TIME_WISH:
            owner = game.time_token_owner
            for side in ("black", "white"):
                if side == owner:
                    actions.append(self._action(ACTION_TYPE_TIME_WISH, {"side": side}))
            return self._sorted(actions)

        if phase == PHASE_PENDING_ELEPHANT_BONUS:
            for position in self._elephant_bonus_targets():
                actions.append(self._action(ACTION_TYPE_PLACE_ELEPHANT_BONUS, {"at": list(position)}))
            actions.append(self._action(ACTION_TYPE_SKIP_ELEPHANT_BONUS))
            return self._sorted(actions)

        if phase == PHASE_PENDING_REFUND:
            for position in self._empty_actionable_positions():
                actions.append(self._action(ACTION_TYPE_PLACE_REFUND, {"at": list(position)}))
            if self._can_cancel():
                actions.append(self._action(ACTION_TYPE_CANCEL))
            return self._sorted(actions)

        if phase == PHASE_SELECT_COST:
            kind = self.interaction.selected_resource
            for position in self._buy_cost_targets():
                actions.append(
                    self._action(ACTION_TYPE_SELECT_COST, {"kind": kind, "at": list(position)})
                )
            if self._can_cancel():
                actions.append(self._action(ACTION_TYPE_CANCEL))
            return self._sorted(actions)

        if phase == PHASE_SELECT_TARGET:
            selected = self.interaction.selected_pos
            if selected is not None:
                for target in get_legal_moves_for_piece(game, self.players, selected):
                    actions.append(
                        self._action(ACTION_TYPE_MOVE, {"from": list(selected), "to": list(target)})
                    )
                # A selected own special piece can also be sold from here, the
                # same way the reserve button sells it in the window.
                piece = game.get_piece(selected)
                tree_type = tree_action(game, tuple(selected))
                if tree_type:
                    actions.append(self._action(tree_type, {"at": list(selected)}))
                if can_start_extra_turn(game, tuple(selected)):
                    actions.append(self._action("butterfly_extra_turn", {"at": list(selected)}))
                if (
                    piece is not None
                    and piece.owner == game.current_player
                    and piece.kind in SELL_REFUND
                    and self.can_sell_special(selected, piece.kind)
                ):
                    actions.append(
                        self._action(f"sell_{piece.kind}", {"at": list(selected)})
                    )
            if self._can_cancel():
                actions.append(self._action(ACTION_TYPE_CANCEL))
            return self._sorted(actions)

        if phase == PHASE_SELECT_RESOURCE and self.interaction.selected_resource in {
            "squirrel",
            "elephant",
            "lion",
            "mole",
            "butterfly",
        }:
            kind = self.interaction.selected_resource
            action_type = f"buy_{kind}" if kind in BUY_COST else ACTION_TYPE_PLACE_SQUIRREL
            for position in self._empty_actionable_positions():
                actions.append(self._action(action_type, {"at": list(position)}))
            if self._can_cancel():
                actions.append(self._action(ACTION_TYPE_CANCEL))
            return self._sorted(actions)

        if phase == PHASE_PLAYING:
            for position in self._selectable_pieces():
                actions.append(self._action(ACTION_TYPE_SELECT_PIECE, {"at": list(position)}))
                tree_type = tree_action(game, position)
                if tree_type:
                    actions.append(self._action(tree_type, {"at": list(position)}))
                if can_start_extra_turn(game, position):
                    actions.append(self._action("butterfly_extra_turn", {"at": list(position)}))
            if can_select_resource(game, self.players, self.interaction, "squirrel"):
                for position in self._empty_actionable_positions():
                    actions.append(self._action(ACTION_TYPE_PLACE_SQUIRREL, {"at": list(position)}))
            for kind in MODE_SPECIALS[game.gamemode]:
                if can_buy_special(game, self.players, kind):
                    for position in self._empty_actionable_positions():
                        actions.append(self._action(f"buy_{kind}", {"at": list(position)}))
            for position, piece in self._sellable_specials():
                actions.append(self._action(f"sell_{piece.kind}", {"at": list(position)}))
            if self._move_time_token_allowed():
                if can_select_resource(game, self.players, self.interaction, "time_token"):
                    actions.append(self._action(ACTION_TYPE_FAST_TIME_TOKEN))
                actions.append(self._action(ACTION_TYPE_MOVE_TIME_TOKEN))
            return self._sorted(actions)

        return actions

    def has_pending_decision(self) -> bool:
        return bool(self.legal_actions())

    def is_stuck(self) -> bool:
        """True when the session needs a decision but offers no legal action.

        This can only happen when the board has no free actionable cell and no
        other action is available (for example a full board with a time-token
        holder that is over the seven-piece limit).  It is reported to the
        caller instead of silently blocking: no rule is invented here.
        """
        if self.game is None or self.is_over():
            return False
        if self.flow.phase in {PHASE_MENU, PHASE_CHECKING_WIN}:
            return False
        return not self.legal_actions()

    def _can_cancel(self) -> bool:
        """Cancel exists only where the interface accepts a right-click."""
        phase = self.flow.phase
        if phase in {
            PHASE_PENDING_PIECE_LIMIT,
            PHASE_TIME_WISH,
            PHASE_CHOOSE_TOKEN,
            PHASE_GAME_OVER,
            PHASE_CHECKING_WIN,
            PHASE_MENU,
        }:
            return False
        return True

    def _selectable_pieces(self) -> list[Position]:
        game = self.game
        if game is None:
            return []
        positions: list[Position] = []
        for y, row in enumerate(game.board_matrix):
            for x, piece in enumerate(row):
                if piece is not None and can_select_piece(game, self.players, (x, y)):
                    positions.append((x, y))
        return sorted(positions)

    def _sellable_specials(self) -> list[tuple[Position, Resource]]:
        """Own special pieces the current player could sell right now."""
        game = self.game
        if game is None:
            return []
        found: list[tuple[Position, Resource]] = []
        for y, row in enumerate(game.board_matrix):
            for x, piece in enumerate(row):
                if (
                    piece is not None
                    and piece.owner == game.current_player
                    and piece.kind in SELL_REFUND
                    and self.can_sell_special((x, y), piece.kind)
                ):
                    found.append(((x, y), piece))
        return sorted(found, key=lambda item: item[0])

    def _action(self, action_type: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(params or {})
        return {
            "action_id": encode_action_id(action_type, payload),
            "type": action_type,
            "params": payload,
            "actor": self.actor(),
            "label": action_label(action_type, payload),
        }

    def _sorted(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(actions, key=lambda item: (item["type"], item["action_id"]))

    def action_id_is_legal(self, action_id: str) -> bool:
        return any(action["action_id"] == action_id for action in self.legal_actions())

    def find_action(self, action_id: str) -> dict[str, Any] | None:
        for action in self.legal_actions():
            if action["action_id"] == action_id:
                return action
        return None

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------
    def submit(
        self,
        action_id: str,
        revision: int,
        request_id: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Validate and execute one action id.  Raises EngineError on rejection."""
        if not isinstance(action_id, str) or not action_id:
            raise EngineError(ERR_INVALID_ARGUMENT, "action_id must be a non-empty string")
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise EngineError(ERR_INVALID_ARGUMENT, "revision must be an integer")
        if request_id is not None and (not isinstance(request_id, str) or not request_id):
            raise EngineError(ERR_INVALID_ARGUMENT, "request_id must be a non-empty string when given")
        if actor is not None and actor not in {"black", "white"}:
            raise EngineError(ERR_INVALID_ARGUMENT, "actor must be 'black' or 'white'")
        if actor is not None:
            owner = self.actor()
            if owner in {"black", "white"} and actor != owner:
                raise EngineError(
                    ERR_WRONG_ACTOR,
                    f"{actor!r} is not the deciding side ({owner!r})",
                    expected_actor=owner,
                    received_actor=actor,
                    phase=self.flow.phase,
                )

        if request_id is not None:
            cached = self.request_cache.get(request_id)
            if cached is not None:
                if cached["action_id"] != action_id or cached["result"].get("input_revision") != revision:
                    raise EngineError(
                        ERR_REQUEST_CONFLICT,
                        f"request_id {request_id!r} was already used for a different request",
                        request_id=request_id,
                        original_action_id=cached["action_id"],
                    )
                replay = dict(cached["result"])
                replay["replayed"] = True
                return replay

        if revision != self.revision:
            raise EngineError(
                ERR_REVISION_MISMATCH,
                f"revision {revision} is stale; the current revision is {self.revision}",
                expected_revision=self.revision,
                received_revision=revision,
            )
        if self.is_over():
            raise EngineError(ERR_SESSION_ENDED, "the game is already over")

        action_type, params = decode_action_id(action_id)
        self._check_actor(actor)
        if not self.action_id_is_legal(action_id):
            raise EngineError(
                ERR_ILLEGAL_ACTION,
                f"action {action_id!r} is not legal in phase {self.flow.phase!r}",
                phase=self.flow.phase,
                actor=self.actor(),
            )

        revision_before = self.revision
        checks_before = len(self.public_check_events)
        code = self._dispatch(action_type, params)
        self._touch()
        self._log(actor, action_type, params, request_id, revision_before, True, detail=code,
                  checks_before=checks_before)
        result = {
            "ok": True,
            "replayed": False,
            "request_id": request_id,
            "action_id": action_id,
            "action_type": action_type,
            "params": params,
            "code": code,
            "input_revision": revision,
            "revision": self.revision,
            "phase": self.flow.phase,
            "actor": self.actor(),
            "message": self.pending_message or code,
            "state": self.snapshot(),
        }
        if request_id is not None:
            self._cache_request(request_id, action_id, result)
        return result

    def _check_actor(self, actor: str | None) -> None:
        if actor is None:
            return
        owner = self.actor()
        if owner not in {"black", "white"}:
            return
        if actor != owner:
            raise EngineError(
                ERR_WRONG_ACTOR,
                f"{actor!r} is not the deciding side ({owner!r})",
                expected_actor=owner,
                received_actor=actor,
                phase=self.flow.phase,
            )

    # ------------------------------------------------------------------
    # Interface-level selections
    #
    # The window forwards clicks here.  Unlike ``submit`` (the agent path) a
    # click is only a request, so this entry point checks the phase itself and
    # refuses anything that would cut across an open transaction.  Both paths
    # end in ``_dispatch``, so the window and the agent share one rule engine.
    # ------------------------------------------------------------------
    # Which action types an interface click may perform, and in which phases.
    CLICK_PHASES: dict[str, set[str]] = {
        ACTION_TYPE_SELECT_PIECE: {PHASE_PLAYING, PHASE_SELECT_TARGET},
        ACTION_TYPE_PLACE_SQUIRREL: {PHASE_PLAYING, PHASE_SELECT_RESOURCE},
        ACTION_TYPE_BUY_ELEPHANT: {PHASE_SELECT_RESOURCE},
        ACTION_TYPE_BUY_LION: {PHASE_SELECT_RESOURCE},
        "buy_mole": {PHASE_SELECT_RESOURCE},
        "buy_butterfly": {PHASE_SELECT_RESOURCE},
        "sell_butterfly": {PHASE_PLAYING, PHASE_SELECT_TARGET},
        "butterfly_extra_turn": {PHASE_PLAYING, PHASE_SELECT_TARGET},
        "sell_mole": {PHASE_PLAYING, PHASE_SELECT_TARGET},
        "uproot_tree": {PHASE_PLAYING, PHASE_SELECT_TARGET},
        "plant_tree": {PHASE_PLAYING, PHASE_SELECT_TARGET},
        ACTION_TYPE_SELECT_COST: {PHASE_SELECT_COST},
        ACTION_TYPE_SELL_ELEPHANT: {PHASE_PLAYING, PHASE_SELECT_TARGET},
        ACTION_TYPE_SELL_LION: {PHASE_PLAYING, PHASE_SELECT_TARGET},
        ACTION_TYPE_PLACE_REFUND: {PHASE_PENDING_REFUND},
        ACTION_TYPE_PLACE_ELEPHANT_BONUS: {PHASE_PENDING_ELEPHANT_BONUS},
        ACTION_TYPE_REMOVE_PIECE_LIMIT: {PHASE_PENDING_PIECE_LIMIT},
        ACTION_TYPE_MOVE_TIME_TOKEN: {PHASE_PLAYING},
        ACTION_TYPE_FAST_TIME_TOKEN: {PHASE_PLAYING},
        ACTION_TYPE_CHOOSE_INITIAL_TOKEN: {PHASE_CHOOSE_TOKEN},
        ACTION_TYPE_TIME_WISH: {PHASE_TIME_WISH},
        ACTION_TYPE_CANCEL: {
            PHASE_PLAYING,
            PHASE_SELECT_TARGET,
            PHASE_SELECT_COST,
            PHASE_SELECT_RESOURCE,
            PHASE_PENDING_REFUND,
            PHASE_PENDING_ELEPHANT_BONUS,
        },
    }

    def apply_action(self, action_type: str, params: dict[str, Any] | None = None) -> str:
        """Run one interface click: validate the phase, execute, record it."""
        if not self.click_is_allowed(action_type):
            self.set_message(ui_text.ACTION_UNAVAILABLE_DIALOG)
            return "unavailable"
        revision_before = self.revision
        checks_before = len(self.public_check_events)
        code = self._dispatch(action_type, dict(params or {}))
        if self.revision == revision_before:
            # A click that succeeded is a state change, so the revision must
            # move for the window exactly as it does for the agent.
            self._touch()
        self._log(None, action_type, dict(params or {}), None, revision_before, True, detail=code,
                  checks_before=checks_before)
        return code

    def click_is_allowed(self, action_type: str) -> bool:
        game = self.game
        if game is None or self.is_over():
            return False
        return self.flow.phase in self.CLICK_PHASES.get(action_type, set())

    def _dispatch(self, action_type: str, params: dict[str, Any] | None = None) -> str:
        """Execute an action by type; used after validation and internally."""
        handler = getattr(self, f"_do_{action_type}", None)
        if handler is None:
            raise EngineError(ERR_UNKNOWN_ACTION, f"unknown action type {action_type!r}")
        return handler(dict(params or {})) or ""

    def select_board(self, position: Position) -> str:
        phase = self.flow.phase
        if phase == PHASE_SELECT_COST:
            return self.apply_action(ACTION_TYPE_SELECT_COST, {"kind": self.interaction.selected_resource, "at": list(position)})
        if phase == PHASE_SELECT_RESOURCE:
            kind = self.interaction.selected_resource
            if kind == "squirrel" or kind in BUY_COST:
                return self.apply_action("place_squirrel" if kind == "squirrel" else f"buy_{kind}", {"at": list(position)})
        pending_types = {
            PHASE_PENDING_REFUND: ACTION_TYPE_PLACE_REFUND,
            PHASE_PENDING_ELEPHANT_BONUS: ACTION_TYPE_PLACE_ELEPHANT_BONUS,
            PHASE_PENDING_PIECE_LIMIT: ACTION_TYPE_REMOVE_PIECE_LIMIT,
        }
        if phase in pending_types:
            return self.apply_action(pending_types[phase], {"at": list(position)})
        return self.apply_action(ACTION_TYPE_SELECT_PIECE, {"at": list(position)})

    def select_resource(self, resource_id: str) -> str:
        game = self.game
        if game is None:
            self.set_message(ui_text.START_OR_LOAD_DIALOG)
            return "no_game"
        if self.is_over():
            self.set_message(ui_text.GAME_OVER_DIALOG)
            return "game_over"
        phase = self.flow.phase
        if phase in {PHASE_CHOOSE_TOKEN, PHASE_TIME_WISH}:
            self.set_message(ui_text.ACTION_UNAVAILABLE_DIALOG)
            return "unavailable"
        if resource_id == "time_token":
            return self.apply_action(ACTION_TYPE_FAST_TIME_TOKEN)
        if not can_select_resource(game, self.players, self.interaction, resource_id):
            self.set_message(ui_text.RESOURCE_ILLEGAL_DIALOG)
            return "illegal"
        if self._selected_own_special(resource_id) is not None:
            # The window sells by clicking an owned special piece and then its
            # reserve button; that click is legal while the piece is selected.
            if self.flow.phase == PHASE_SELECT_TARGET:
                self.flow.phase = PHASE_PLAYING
            return self.apply_action(f"sell_{resource_id}")
        if resource_id == "squirrel":
            if not self.click_is_allowed(ACTION_TYPE_PLACE_SQUIRREL):
                self.set_message(ui_text.ACTION_UNAVAILABLE_DIALOG)
                return "unavailable"
            self._start_squirrel_placement()
            return "place_squirrel"
        if phase not in {PHASE_PLAYING, PHASE_SELECT_TARGET} or resource_id not in BUY_COST:
            self.set_message(ui_text.ACTION_UNAVAILABLE_DIALOG)
            return "unavailable"
        self._start_buy_special(resource_id)
        return f"buy_{resource_id}"

    def select_camp(self, side: str) -> str:
        game = self.game
        if game is None:
            self.set_message(ui_text.START_OR_LOAD_DIALOG)
            return "no_game"
        if self.flow.phase == PHASE_CHOOSE_TOKEN:
            return self.apply_action(ACTION_TYPE_CHOOSE_INITIAL_TOKEN, {"side": side})
        if self.flow.phase == PHASE_TIME_WISH:
            return self.apply_action(ACTION_TYPE_TIME_WISH, {"side": side})
        if self.is_over():
            self.set_message(ui_text.GAME_OVER_DIALOG)
            return "game_over"
        return self.apply_action(ACTION_TYPE_MOVE_TIME_TOKEN, {"side": side})

    def cancel(self) -> str:
        return self.apply_action(ACTION_TYPE_CANCEL)

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------
    def _do_choose_initial_token(self, params: dict[str, Any]) -> str:
        game = self._require_game()
        side = params.get("side")
        if side not in {"black", "white"}:
            raise EngineError(ERR_INVALID_ARGUMENT, "side must be 'black' or 'white'")
        game.set_time_token_owner(side)
        self.flow.phase = PHASE_PLAYING
        self.flow.pending_action = None
        self.set_message(ui_text.token_assigned(side, game.current_player))
        self._check_mate_if_stuck()
        return "token_assigned"

    def _do_select_piece(self, params: dict[str, Any]) -> str:
        game = self._require_game()
        position = self._position(params, "at")
        if self.flow.phase == PHASE_CHOOSE_TOKEN:
            self.set_message(ui_text.CHOOSE_TIME_TOKEN_DIALOG)
            return "choose_token"
        if self.flow.phase == PHASE_TIME_WISH:
            self.set_message(ui_text.TIME_WISH_DIALOG)
            return "time_wish"
        if self.flow.phase == PHASE_GAME_OVER:
            self.set_message(ui_text.GAME_OVER_DIALOG)
            return "game_over"
        if self.flow.phase == PHASE_PENDING_PIECE_LIMIT:
            return self._dispatch(ACTION_TYPE_REMOVE_PIECE_LIMIT, {"at": list(position)})
        if self.flow.phase == PHASE_PENDING_REFUND:
            return self._dispatch(ACTION_TYPE_PLACE_REFUND, {"at": list(position)})
        if self.flow.phase == PHASE_PENDING_ELEPHANT_BONUS:
            return self._dispatch(ACTION_TYPE_PLACE_ELEPHANT_BONUS, {"at": list(position)})
        if self.flow.phase == PHASE_SELECT_COST:
            return self._dispatch(
                ACTION_TYPE_SELECT_COST,
                {"kind": self.interaction.selected_resource, "at": list(position)},
            )
        if self.flow.pending_action in {ACTION_PLACE_SQUIRREL, *REVERSED_BUY.values()}:
            kind = self.interaction.selected_resource
            if kind == "squirrel":
                return self._dispatch(ACTION_TYPE_PLACE_SQUIRREL, {"at": list(position)})
            return self._dispatch(f"buy_{kind}", {"at": list(position)})

        selected = self.interaction.selected_pos
        if selected is None:
            return self._select_piece(position)
        if tuple(position) == tuple(selected):
            tree_type = tree_action(game, position)
            if tree_type:
                return self._dispatch(tree_type, {"at": list(position)})
            if can_start_extra_turn(game, position):
                return self._dispatch("butterfly_extra_turn", {"at": list(position)})
            return self.cancel()
        if self._try_move_selected_piece(tuple(selected), position):
            return "moved"
        if can_select_piece(game, self.players, position):
            return self._select_piece(position)
        return self.cancel()

    def _select_piece(self, position: Position) -> str:
        game = self._require_game()
        if not can_select_piece(game, self.players, position):
            self.set_message(ui_text.NO_LEGAL_ACTION_DIALOG)
            return "illegal"
        piece = game.get_piece(position)
        self.interaction.selected_pos = tuple(position)
        self.flow.phase = PHASE_SELECT_TARGET
        self.flow.pending_action = ACTION_MOVE
        self.refresh_selection_message()
        return "selected"

    def refresh_selection_message(self):
        if self.flow.phase != PHASE_SELECT_TARGET or self.interaction.selected_pos is None:
            return
        game = self._require_game()
        position = tuple(self.interaction.selected_pos)
        piece = game.get_piece(position)
        if piece is None:
            return
        ability = {"uproot_tree": "uproot the tree", "plant_tree": "plant the tree"}.get(tree_action(game, position))
        if can_start_extra_turn(game, position):
            ability = "activate the butterfly's extra-turn ability"
        self.set_message(ui_text.selected_piece(
            piece.kind, bool(get_legal_moves_for_piece(game, self.players, position)),
            self.can_sell_special(position, piece.kind), ability))

    def _do_move(self, params: dict[str, Any]) -> str:
        start = self._position(params, "from")
        end = self._position(params, "to")
        if not self._try_move_selected_piece(start, end):
            raise EngineError(
                ERR_ILLEGAL_ACTION,
                f"the piece at {list(start)} cannot move to {list(end)}",
                phase=self.flow.phase,
            )
        return "moved"

    def _try_move_selected_piece(self, start: Position, end: Position) -> bool:
        game = self._require_game()
        piece = game.get_piece(start) if game.is_inside_board(start) else None
        if piece is None:
            self.interaction.selected_pos = None
            self.set_message(ui_text.SELECTED_PIECE_MISSING_DIALOG)
            return False
        if tuple(end) not in get_legal_moves_for_piece(game, self.players, start):
            self.set_message(ui_text.ILLEGAL_TARGET_DIALOG)
            return False
        cost = 2 if piece.owner != game.current_player else 1
        if piece.kind == "elephant":
            if not game.elephant_push(start, end):
                self.set_message(ui_text.ILLEGAL_TARGET_DIALOG)
                return False
            game.spend_ap(cost)
            self._record_g3_action(move=True)
            self.refresh_players()
            self._clear_selection()
            self.flow.phase = PHASE_PENDING_ELEPHANT_BONUS
            self.interaction.after_elephant_push = True
            self.interaction.elephant_start_position = tuple(start)
            self.set_message(ui_text.ELEPHANT_BONUS_DIALOG)
            return True

        moved_piece = game.place_piece(start, None)
        game.place_piece(end, moved_piece)
        if game.gamemode == 2 and piece.kind == "tree" and start in TREE_STARTS.values():
            game.tree_markers.add(start)
        game.move_new_piece_mark(start, end)
        game.spend_ap(cost)
        self._record_g3_action(move=True)
        self.refresh_players()
        self._finish_action(f"{piece.kind.capitalize()} moved.")
        return True

    def _change_tree_state(self, params, action_type):
        game = self._require_game()
        position = self._position(params, "at")
        if tree_action(game, position) != action_type:
            raise EngineError(ERR_ILLEGAL_ACTION, "the tree cannot change state here")
        cost = tree_action_cost(game, position)
        game.get_piece(position).rooted = action_type == "plant_tree"
        shift_corner(game, position, expand=action_type == "uproot_tree")
        game.spend_ap(cost)
        self.refresh_players()
        self._finish_action("Tree planted." if action_type == "plant_tree" else "Tree uprooted.")
        return action_type

    def _do_uproot_tree(self, params):
        return self._change_tree_state(params, "uproot_tree")

    def _do_plant_tree(self, params):
        return self._change_tree_state(params, "plant_tree")

    def _do_place_squirrel(self, params: dict[str, Any]) -> str:
        position = self._position(params, "at")
        if self.flow.phase == PHASE_PENDING_REFUND:
            return self._dispatch(ACTION_TYPE_PLACE_REFUND, {"at": list(position)})
        if self.flow.phase == PHASE_PENDING_ELEPHANT_BONUS:
            return self._dispatch(ACTION_TYPE_PLACE_ELEPHANT_BONUS, {"at": list(position)})
        game = self._require_game()
        if game.current_ap < 3:
            self.set_message(ui_text.NOT_ENOUGH_AP_SQUIRREL_DIALOG)
            return "not_enough_ap"
        if not is_empty_accessible_cell(game, position):
            self.set_message(ui_text.ILLEGAL_SQUIRREL_TARGET_DIALOG)
            return "illegal_target"
        game.place_piece(position, Resource(game.current_player, "squirrel"))
        game.mark_new_piece(position)
        game.spend_ap(3)
        self._record_g3_action(resources_changed=True)
        self.refresh_players()
        self._finish_action(ui_text.SQUIRREL_PLACED_DIALOG)
        return "squirrel_placed"

    def _start_squirrel_placement(self) -> None:
        self._clear_selection()
        self.interaction.selected_resource = "squirrel"
        self.flow.phase = PHASE_SELECT_RESOURCE
        self.flow.pending_action = ACTION_PLACE_SQUIRREL
        self.set_message(ui_text.PLACE_SQUIRREL_DIALOG)

    def _do_buy_elephant(self, params: dict[str, Any]) -> str:
        return self._place_bought_special(params, "elephant")

    def _do_buy_lion(self, params: dict[str, Any]) -> str:
        return self._place_bought_special(params, "lion")

    def _do_buy_mole(self, params: dict[str, Any]) -> str:
        return self._place_bought_special(params, "mole")

    def _do_buy_butterfly(self, params):
        return self._place_bought_special(params, "butterfly")

    def _do_butterfly_extra_turn(self, params):
        game = self._require_game()
        position = self._position(params, "at")
        if not can_start_extra_turn(game, position):
            raise EngineError(ERR_ILLEGAL_ACTION, "this butterfly cannot start an extra turn")
        game.spend_ap(1)
        game.g3_turn.pending_extra_turn = True
        self._clear_selection()
        self.begin_victory_check("Butterfly used 1 AP to end this turn.")
        return "butterfly_extra_turn"

    def _place_bought_special(self, params: dict[str, Any], kind: str) -> str:
        position = self._position(params, "at")
        game = self._require_game()
        if any(piece is not None and piece.owner == game.current_player and piece.kind == kind
               for row in game.board_matrix for piece in row):
            raise EngineError(ERR_ILLEGAL_ACTION, "This side already owns this kind of special piece.")
        # Remember what is being paid for; completing an action clears the
        # selection, so the cost steps must not depend on the pending action.
        self.interaction.selected_resource = kind
        cost_positions = list(self.interaction.selected_cost)
        if len(cost_positions) < BUY_COST[kind]:
            self.flow.phase = PHASE_SELECT_COST
            self.set_message(
                f"Choose squirrels to buy {kind}. Cost selected: {len(cost_positions)}/{BUY_COST[kind]}."
            )
            return "need_cost"
        if not is_empty_accessible_cell(game, position):
            self.set_message(ui_text.ILLEGAL_TARGET_DIALOG)
            return "illegal_target"
        game.place_piece(position, Resource(game.current_player, kind))
        game.mark_new_piece(position)
        game.spend_ap(1)
        self._commit_temporary_transaction()
        self._record_g3_action(resources_changed=True)
        self.refresh_players()
        self._finish_action(f"{kind.capitalize()} bought.")
        return f"{kind}_bought"

    def _do_sell_elephant(self, params: dict[str, Any]) -> str:
        return self._do_sell_special(params, "elephant")

    def _do_sell_lion(self, params: dict[str, Any]) -> str:
        return self._do_sell_special(params, "lion")

    def _do_sell_mole(self, params: dict[str, Any]) -> str:
        return self._do_sell_special(params, "mole")

    def _do_sell_butterfly(self, params):
        return self._do_sell_special(params, "butterfly")

    def _do_sell_special(self, params: dict[str, Any], kind: str) -> str:
        game = self._require_game()
        position = self._position(params, "at") if "at" in params else self.interaction.selected_pos
        if position is None:
            raise EngineError(ERR_INVALID_ARGUMENT, "selling needs the position of an own special piece")
        if not self.can_sell_special(position, kind):
            self.set_message(ui_text.RESOURCE_ILLEGAL_DIALOG)
            return "illegal"
        piece = game.get_piece(position) if game.is_inside_board(position) else None
        if piece is None or piece.owner != game.current_player or piece.kind != kind:
            self.set_message(ui_text.RESOURCE_ILLEGAL_DIALOG)
            return "illegal"
        self._temporarily_remove_piece(position)
        self.refresh_players()
        self._clear_selection()
        self.flow.pending_action = ACTION_SELL_PIECE
        self.flow.pending_refund_owner = game.current_player
        self.flow.pending_refund_count = SELL_REFUND[kind]
        self.flow.pending_refund_kind = kind
        self.flow.phase = PHASE_PENDING_REFUND
        self.set_message(ui_text.choose_refund(self.flow.pending_refund_count))
        return "selling"

    def _start_buy_special(self, kind: str) -> None:
        self._clear_selection()
        self.interaction.selected_resource = kind
        self.interaction.selected_cost = []
        self.flow.phase = PHASE_SELECT_COST
        self.flow.pending_action = REVERSED_BUY[kind]
        self.set_message(ui_text.choose_cost(kind, 0, BUY_COST[kind]))

    def _do_select_cost(self, params: dict[str, Any]) -> str:
        game = self._require_game()
        kind = params.get("kind") or self.interaction.selected_resource
        if kind not in BUY_COST:
            raise EngineError(ERR_INVALID_ARGUMENT, "kind must be 'elephant', 'mole' or 'lion'")
        if self.interaction.selected_resource not in BUY_COST:
            self.interaction.selected_resource = kind
        position = self._position(params, "at")
        piece = game.get_piece(position) if game.is_inside_board(position) else None
        if (
            piece is None
            or piece.owner != game.current_player
            or piece.kind != "squirrel"
            or any(tuple(pos) == position for pos in self.interaction.selected_cost)
        ):
            self.set_message(ui_text.CHOOSE_BUY_COST_DIALOG)
            return "illegal_cost"
        self.interaction.selected_cost.append(position)
        required = BUY_COST[kind]
        selected = len(self.interaction.selected_cost)
        if selected < required:
            self.set_message(ui_text.choose_cost(kind, selected, required))
            return "cost_selected"
        self._remove_selected_cost_pieces()
        self.flow.phase = PHASE_SELECT_RESOURCE
        self.flow.pending_action = REVERSED_BUY[kind]
        self.set_message(ui_text.BUY_TARGET_DIALOG)
        return "cost_paid"

    def _do_place_refund(self, params: dict[str, Any]) -> str:
        game = self._require_game()
        owner = self.flow.pending_refund_owner
        if owner is None:
            raise EngineError(ERR_ILLEGAL_ACTION, "there is no pending refund to place")
        position = self._position(params, "at")
        if not is_empty_accessible_cell(game, position):
            self.set_message(ui_text.ILLEGAL_SQUIRREL_TARGET_DIALOG)
            return "illegal_target"
        game.place_piece(position, Resource(owner, "squirrel"))
        game.mark_new_piece(position)
        self.flow.temp_placed.append(position)
        self.flow.pending_refund_count -= 1
        self.refresh_players()
        if self.flow.pending_refund_count <= 0:
            sold_kind = self.flow.pending_refund_kind or "special piece"
            self.flow.pending_refund_owner = None
            self.flow.pending_refund_kind = None
            game.spend_ap(1)
            self._commit_temporary_transaction()
            self._record_g3_action(resources_changed=True)
            self._finish_action(f"{sold_kind.capitalize()} sold.")
            return "sold"
        self.set_message(ui_text.choose_refund(self.flow.pending_refund_count))
        return "refund_placed"

    def _do_place_elephant_bonus(self, params: dict[str, Any]) -> str:
        game = self._require_game()
        position = self._position(params, "at")
        bonus_position = self.interaction.elephant_start_position
        if bonus_position is None:
            raise EngineError(ERR_ILLEGAL_ACTION, "there is no pending elephant bonus")
        if tuple(position) != tuple(bonus_position) or not is_empty_accessible_cell(game, position):
            self.set_message(ui_text.ILLEGAL_SQUIRREL_TARGET_DIALOG)
            return "illegal_target"
        game.place_piece(position, Resource(game.current_player, "squirrel"))
        game.mark_new_piece(position)
        self.refresh_players()
        self._finish_action(ui_text.ELEPHANT_BONUS_PLACED_DIALOG)
        return "bonus_placed"

    def _do_skip_elephant_bonus(self, params: dict[str, Any]) -> str:
        if self.flow.phase != PHASE_PENDING_ELEPHANT_BONUS:
            raise EngineError(ERR_ILLEGAL_ACTION, "there is no pending elephant bonus to skip")
        self._finish_action(ui_text.ELEPHANT_BONUS_SKIPPED_DIALOG)
        return "bonus_skipped"

    def _do_remove_piece_limit(self, params: dict[str, Any]) -> str:
        game = self._require_game()
        owner = self.flow.pending_piece_limit_owner
        if owner is None:
            self.flow.phase = PHASE_PLAYING
            return "no_limit"
        position = self._position(params, "at")
        piece = game.get_piece(position) if game.is_inside_board(position) else None
        if (piece is None or piece.owner != owner or not piece.is_piece
                or piece.kind == "tree"):
            self.set_message(ui_text.PIECE_LIMIT_TARGET_DIALOG)
            return "illegal_target"
        game.remove_piece(position)
        self.refresh_players()
        owner_state = self.players.get(owner)
        if owner_state is not None and owner_state.num_pieces <= 7:
            self.flow.pending_piece_limit_owner = None
            self._finish_action(ui_text.PIECE_LIMIT_RESOLVED_DIALOG)
            return "limit_resolved"
        self.set_message(ui_text.PIECE_LIMIT_DIALOG)
        return "removed"

    def _do_move_time_token(self, params: dict[str, Any]) -> str:
        game = self._require_game()
        side = params.get("side", game.current_player)
        if side != game.current_player:
            self.set_message(ui_text.CURRENT_PANEL_ONLY_DIALOG)
            return "wrong_side"
        if game.time_token_owner not in {"black", "white"}:
            self.set_message(ui_text.ASSIGN_TOKEN_FIRST_DIALOG)
            return "no_token"
        if game.current_ap < 1:
            self.set_message(ui_text.NOT_ENOUGH_AP_TOKEN_DIALOG)
            return "not_enough_ap"
        self.flow.pending_action = ACTION_MOVE_TIME_TOKEN
        game.set_time_token_owner(other_player(game.time_token_owner))
        game.spend_ap(1)
        self._enter_piece_limit_or_finish(ui_text.TOKEN_MOVED_ONCE_DIALOG)
        return "token_moved"

    def _do_fast_time_token(self, params: dict[str, Any]) -> str:
        game = self._require_game()
        if not can_select_resource(game, self.players, self.interaction, "time_token"):
            self.set_message(ui_text.RESOURCE_ILLEGAL_DIALOG)
            return "illegal"
        spent_ap = game.current_ap
        if game.time_token_owner in {"black", "white"} and spent_ap % 2 == 1:
            game.set_time_token_owner(other_player(game.time_token_owner))
        game.spend_ap(spent_ap)
        self._finish_action(ui_text.TOKEN_FAST_MOVED_DIALOG)
        return "token_fast_moved"

    def _do_time_wish(self, params: dict[str, Any]) -> str:
        game = self._require_game()
        if game.gamemode not in {2, 3}:
            raise EngineError(ERR_ILLEGAL_ACTION, "time wishes are available in G2 and G3")
        side = params.get("side", game.time_token_owner)
        if side != game.time_token_owner:
            self.set_message(ui_text.TIME_WISH_DIALOG)
            return "wrong_side"
        game.set_time_token_owner(None)
        game.current_ap = 0
        self.flow.phase = PHASE_GAME_OVER
        self.flow.pending_action = None
        self.game_over_reason = "time_wish"
        self.finished = True
        self.mat_loser = other_player(self.flow.time_wish_winner) if self.flow.time_wish_winner else None
        game.mate_loser = self.mat_loser
        self.set_message(ui_text.TIME_WISH_RESOLVED_DIALOG)
        return "time_wish"

    def _do_cancel(self, params: dict[str, Any]) -> str:
        return self.cancel_current_action()

    # ------------------------------------------------------------------
    # Transactions: a pending transaction is legal and reversible, a failed
    # action never leaves a partial change behind.
    # ------------------------------------------------------------------
    def _temporarily_remove_piece(self, position: Position) -> Resource | None:
        game = self._require_game()
        if any(tuple(pos) == tuple(position) for pos, _piece in self.flow.temp_removed):
            return None
        piece = game.get_piece(position) if game.is_inside_board(position) else None
        if piece is None:
            return None
        if self.flow.temp_new_pieces is None:
            self.flow.temp_new_pieces = [tuple(pos) for pos in game.new_piece]
        self.flow.temp_removed.append((tuple(position), piece))
        game.remove_piece(position)
        self._remove_new_piece_mark(position)
        return piece

    def _remove_selected_cost_pieces(self) -> None:
        for position in list(self.interaction.selected_cost):
            self._temporarily_remove_piece(position)
        self.refresh_players()

    def restore_temporary_transaction(self) -> None:
        """Undo every partial change of an unfinished purchase or sale."""
        game = self.game
        if game is None:
            return
        for position in reversed(self.flow.temp_placed):
            if game.is_inside_board(position):
                game.remove_piece(position)
                self._remove_new_piece_mark(position)
        for position, piece in reversed(self.flow.temp_removed):
            if game.is_inside_board(position) and game.get_piece(position) is None:
                game.place_piece(position, piece)
        if self.flow.temp_new_pieces is not None:
            game.new_piece = list(self.flow.temp_new_pieces)
        self.flow.temp_new_pieces = None
        self.flow.temp_removed = []
        self.flow.temp_placed = []
        self.flow.pending_refund_owner = None
        self.flow.pending_refund_count = 0
        self.flow.pending_refund_kind = None
        self.refresh_players()

    def _commit_temporary_transaction(self) -> None:
        self.flow.temp_removed = []
        self.flow.temp_placed = []
        self.flow.temp_new_pieces = None

    def _remove_new_piece_mark(self, position: Position) -> None:
        game = self.game
        if game is None:
            return
        game.new_piece = [pos for pos in game.new_piece if tuple(pos) != tuple(position)]

    # ------------------------------------------------------------------
    # Action completion, turn resolution and victory handling
    # ------------------------------------------------------------------
    def _finish_action(self, action_message: str) -> None:
        game = self._require_game()
        self._clear_selection()
        if game.current_ap > 0:
            if self._check_mate_if_stuck():
                return
            self.flow.phase = PHASE_PLAYING
            self.set_message(ui_text.action_complete(action_message, game.current_ap))
            return
        self.begin_victory_check(action_message)

    def begin_victory_check(self, action_message: str = "") -> None:
        """Enter the victory-check step.  Headless sessions resolve it at once."""
        game = self._require_game()
        playerstate = self.players.get(game.current_player)
        if playerstate is None:
            return
        check_conditions = MODE_CONDITIONS[game.gamemode]
        results = check_conditions(playerstate, game)
        self.flow.phase = PHASE_CHECKING_WIN
        self.flow.last_condition_results = results
        self.flow.checking_action_message = action_message
        self.condition_results = results
        self.condition_reveal_count = 0
        self.set_message(ui_text.CHECKING_WIN_DIALOG)
        if self.resolve_checks_immediately:
            self.resolve_checks()

    def resolve_checks(self) -> None:
        """Resolve the victory-check step without any animation delay."""
        if self.flow.phase != PHASE_CHECKING_WIN:
            return
        results = self.flow.last_condition_results or []
        self.condition_reveal_count = len(results)
        self.condition_results = results
        game = self._require_game()
        event = {
            "turn_number": self.turn_number, "checked_player": game.current_player,
            "gamemode": game.gamemode, "conditions": list(results),
            "time_token_owner": game.time_token_owner,
            "start_board": self.flow.turn_start_board,
            "end_board": serialize_board(game.board_matrix),
            "trigger": self.flow.checking_action_message,
            "actions": list(self.flow.turn_actions),
        }
        self.flow.last_public_check = event
        self.flow.public_check_archive.append(event)
        self.public_check_events.append(event)
        self.flow.turn_actions = []
        self.resolve_turn_end(self.flow.checking_action_message)

    def normalize_after_load(self) -> None:
        """Keep a restored session in a phase that can actually continue.

        Saved action phases are authoritative, including play without a token.
        A save taken mid victory-check finishes that check without animation.
        """
        game = self.game
        if game is None:
            return
        if self.flow.phase == PHASE_TIME_WISH and game.gamemode == 1:
            self.end_session("check_victory", other_player(self.flow.time_wish_winner or game.current_player))
            game.current_ap = 0
            return
        if self.flow.phase == PHASE_TIME_WISH and game.time_token_owner is None:
            self.flow.phase = PHASE_GAME_OVER
            self.finished = True
            self.game_over_reason = self.game_over_reason or "time_wish"
            return
        if self.flow.phase == PHASE_CHECKING_WIN:
            self.condition_reveal_count = 0
            self.resolve_checks()
            return

    def resolve_turn_end(self, action_message: str = "") -> None:
        game = self._require_game()
        results = self.flow.last_condition_results
        if results is None:
            playerstate = self.players.get(game.current_player)
            if playerstate is None:
                return
            check_conditions = MODE_CONDITIONS[game.gamemode]
            results = check_conditions(playerstate, game)
            self.flow.last_condition_results = results
            self.condition_results = results
            self.condition_reveal_count = len(results)
        if all(results):
            if game.gamemode == 1:
                winner = game.current_player
                game.current_ap = 0
                self.end_session("check_victory", other_player(winner))
                self.set_message(f"{winner.capitalize()} wins. Game 1 is over.")
                return
            self.flow.phase = PHASE_TIME_WISH
            self.flow.time_wish_winner = game.current_player
            token_owner = game.time_token_owner
            if token_owner in {"black", "white"}:
                game.current_player = token_owner
            game.max_ap = 1
            game.current_ap = 1
            self.set_message(ui_text.time_wish_prompt(self.flow.time_wish_winner, token_owner))
            return
        if game.gamemode == 3 and game.g3_turn.pending_extra_turn and game.current_ap > 0:
            game.new_piece = []
            game.g3_turn = G3Turn.capture(game, extra_turn=True)
            turn_message = f"{game.current_player.capitalize()}'s butterfly extra turn. {game.current_ap} AP remaining."
        else:
            game.new_turn()
            if game.gamemode == 3:
                game.g3_turn = G3Turn.capture(game)
            turn_message = f"{game.current_player.capitalize()}'s turn."
        self.refresh_players()
        self.flow.phase = PHASE_PLAYING
        self.flow.pending_action = None
        self.turn_number += 1
        self.flow.turn_start_board = serialize_board(game.board_matrix)
        self.set_message(f"{action_message} {turn_message}" if action_message else turn_message)
        self._check_mate_if_stuck()

    def _check_mate_if_stuck(self) -> bool:
        game = self.game
        if game is None or game.current_ap <= 0 or game.time_token_owner is not None:
            return False
        if self._sellable_specials():
            return False
        empty_interaction = Interaction()
        if get_legal_board_targets(game, self.players, empty_interaction):
            return False
        if get_legal_resource_targets(game, self.players, empty_interaction):
            return False
        if get_legal_camp_targets(game, self.players, empty_interaction):
            return False
        winner = other_player(game.current_player)
        self.mat_loser = game.current_player
        game.mate_loser = self.mat_loser
        self.flow.phase = PHASE_GAME_OVER
        self.flow.pending_action = None
        self.game_over_reason = "mate"
        self.finished = True
        self.set_message(
            f"{game.current_player.capitalize()} has no legal actions. {winner.capitalize()} wins by mate."
        )
        return True

    def _enter_piece_limit_or_finish(self, message: str) -> None:
        game = self._require_game()
        holder = game.time_token_owner
        holder_state = self.players.get(holder) if holder is not None else None
        if holder_state is not None and holder_state.num_pieces > 7:
            self.flow.phase = PHASE_PENDING_PIECE_LIMIT
            self.flow.pending_piece_limit_owner = holder
            self.set_message(ui_text.PIECE_LIMIT_DIALOG)
            return
        self._finish_action(message)

    def cancel_current_action(self) -> str:
        if self.is_over():
            self.set_message(ui_text.GAME_OVER_DIALOG)
            return "game_over"
        if self.flow.phase == PHASE_CHECKING_WIN:
            # The turn is being resolved; cancelling would freeze the turn.
            self.set_message(ui_text.CHECKING_WIN_DIALOG)
            return "unavailable"
        if self.flow.phase == PHASE_PENDING_ELEPHANT_BONUS:
            self._finish_action(ui_text.ELEPHANT_BONUS_SKIPPED_DIALOG)
            return "bonus_skipped"
        if self.flow.phase == PHASE_PENDING_REFUND or self.flow.temp_removed or self.flow.temp_placed:
            self.restore_temporary_transaction()
            self._clear_selection()
            self.flow.phase = PHASE_PLAYING
            self.set_message(ui_text.SELECTION_CLEARED_DIALOG)
            return "transaction_restored"
        if self.flow.phase in {PHASE_PENDING_PIECE_LIMIT, PHASE_TIME_WISH}:
            self.set_message(ui_text.ACTION_UNAVAILABLE_DIALOG)
            return "unavailable"
        self._clear_selection()
        if self.game is None:
            self.flow.phase = PHASE_MENU
        elif self.flow.phase != PHASE_CHOOSE_TOKEN:
            self.flow.phase = PHASE_PLAYING
        self.set_message(ui_text.SELECTION_CLEARED_DIALOG)
        return "selection_cleared"

    def _clear_selection(self) -> None:
        self.interaction.selected_pos = None
        self.interaction.selected_resource = None
        self.interaction.selected_cost = []
        self.interaction.after_elephant_push = False
        self.interaction.elephant_start_position = None
        self.flow.pending_action = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_game(self) -> Chessboard:
        if self.game is None:
            raise EngineError(ERR_NO_ACTIVE_GAME, "no active game in this session")
        return self.game

    def _position(self, params: dict[str, Any], key: str) -> Position:
        value = params.get(key)
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or not all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        ):
            raise EngineError(ERR_INVALID_ARGUMENT, f"{key} must be [x, y] with two integers")
        return (int(value[0]), int(value[1]))

    def _selected_own_special(self, kind: str) -> Position | None:
        game = self.game
        selected = self.interaction.selected_pos
        if game is None or selected is None:
            return None
        piece = game.get_piece(selected)
        if piece is not None and piece.owner == game.current_player and piece.kind == kind:
            return tuple(selected)
        return None

    def can_sell_special(self, position: Position, kind: str) -> bool:
        """Selling needs the piece, 1 AP and room for the refunded squirrels.

        ``can_select_resource`` also accepts a special piece when it could be
        *bought*, so it must not be used to decide what can be sold.
        """
        return can_sell_special(self.game, position, kind)

    def end_session(self, reason: str = "ended_by_caller", loser: str | None = None) -> dict[str, Any]:
        """Close the session without touching the board."""
        if not self.is_over():
            self.finished = True
            self.game_over_reason = reason
            if loser in {"black", "white"}:
                self.mat_loser = loser
                if self.game is not None:
                    self.game.mate_loser = loser
            self.flow.phase = PHASE_GAME_OVER
            self.flow.pending_action = None
            self.set_message(f"Session ended ({reason}).")
            self._touch()
        return self.snapshot()

    # ------------------------------------------------------------------
    # Snapshot: a detached copy, never an internal object
    # ------------------------------------------------------------------
    def snapshot(self, include_board: bool = True) -> dict[str, Any]:
        game = self.game
        players: dict[str, Any] = {}
        for color in ("black", "white"):
            state = self.players.get(color)
            players[color] = {
                "num_pieces": int(state.num_pieces) if state else 0,
                "num_squirrels": int(state.num_squirrels) if state else 0,
                "has_elephant": bool(state.has_elephant) if state else False,
                "has_lion": bool(state.has_lion) if state else False,
                "has_mole": bool(state.has_mole) if state else False,
                "has_butterfly": bool(state.has_butterfly) if state else False,
            }
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "revision": self.revision,
            "gamemode": game.gamemode if game else None,
            "phase": self.flow.phase,
            "actor": self.actor(),
            "current_player": game.current_player if game else None,
            "time_token_owner": game.time_token_owner if game else None,
            "current_ap": game.current_ap if game else 0,
            "max_ap": game.max_ap if game else 0,
            "players": players,
            "pending": self.pending_details(),
            "result": self.result(),
            "condition_results": list(self.flow.last_condition_results)
            if self.flow.last_condition_results is not None
            else None,
            "message": self.pending_message or self.interaction.dialog_text,
            "new_pieces": [[int(x), int(y)] for x, y in game.new_piece] if game else [],
            "finished": self.is_over(),
            "new_game_plus_available": self.can_start_new_game_plus(),
            "wish_continuation": str(self.wish_continuation_path)
            if self.wish_continuation_path is not None
            else None,
        }
        if game and game.gamemode == 2:
            payload.update(tree_markers=[list(pos) for pos in sorted(game.tree_markers)],
                           skip_token_selection=game.skip_token_selection,
                           expanded_corners=sorted(game.expanded_corners))
        if game and game.gamemode == 3:
            payload["g3_turn"] = game.g3_turn.to_dict()
        if include_board:
            payload["board"] = serialize_board(game.board_matrix) if game else []
        return payload

    def pending_details(self) -> dict[str, Any]:
        flow = self.flow
        interaction = self.interaction
        return {
            "pending_action": flow.pending_action,
            "selected_pos": serialize_position(interaction.selected_pos),
            "selected_resource": interaction.selected_resource,
            "selected_cost": [list(pos) for pos in interaction.selected_cost],
            "temporary_removed": [
                {"position": list(pos), "piece": serialize_piece(piece)}
                for pos, piece in flow.temp_removed
            ],
            "temporary_placed": [list(pos) for pos in flow.temp_placed],
            "pending_refund_owner": flow.pending_refund_owner,
            "pending_refund_count": flow.pending_refund_count,
            "pending_refund_kind": flow.pending_refund_kind,
            "pending_piece_limit_owner": flow.pending_piece_limit_owner,
            "time_wish_winner": flow.time_wish_winner,
            "elephant_start_position": serialize_position(interaction.elephant_start_position),
        }

    def result(self) -> dict[str, Any]:
        game = self.game
        if game is None:
            return {"status": "no_game", "winner": None, "loser": None, "reason": None}
        if self.flow.phase == PHASE_TIME_WISH:
            return {
                "status": "pending_time_wish",
                "winner": self.flow.time_wish_winner,
                "loser": None,
                "reason": "check_victory",
            }
        if self.flow.phase == PHASE_GAME_OVER or self.finished:
            winner = other_player(self.mat_loser) if self.mat_loser else (
                self.flow.time_wish_winner if self.flow.time_wish_winner in {"black", "white"} else None
            )
            return {
                "status": "game_over",
                "winner": winner,
                "loser": self.mat_loser,
                "reason": self.game_over_reason,
                "wish_continuation": str(self.wish_continuation_path)
                if self.wish_continuation_path is not None
                else None,
                "wish_save_error": self.wish_save_error,
            }
        return {"status": "playing", "winner": None, "loser": None, "reason": None}


# ----------------------------------------------------------------------
# Action-id encoding shared by the engine, the agent helper and the GUI
# ----------------------------------------------------------------------
def encode_action_id(action_type: str, params: dict[str, Any] | None = None) -> str:
    """Build the stable action id: ``type`` or ``type:key=value;key=value``."""
    payload = params or {}
    if not payload:
        return action_type
    parts = []
    for key in sorted(payload):
        value = payload[key]
        if isinstance(value, (list, tuple)):
            rendered = ",".join(str(int(item)) for item in value)
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return f"{action_type}:{';'.join(parts)}"


def decode_action_id(action_id: str) -> tuple[str, dict[str, Any]]:
    """Parse an action id back into ``(type, params)``.  Raises EngineError."""
    if not isinstance(action_id, str) or not action_id:
        raise EngineError(ERR_INVALID_ARGUMENT, "action_id must be a non-empty string")
    if ":" not in action_id:
        return action_id, {}
    action_type, _, raw = action_id.partition(":")
    params: dict[str, Any] = {}
    for chunk in raw.split(";"):
        if not chunk:
            continue
        key, sep, value = chunk.partition("=")
        if not sep or not key:
            raise EngineError(ERR_INVALID_ARGUMENT, f"cannot parse action id segment {chunk!r}")
        if key in {"at", "from", "to"}:
            numbers = value.split(",")
            if len(numbers) != 2:
                raise EngineError(ERR_INVALID_ARGUMENT, f"{key} needs two coordinates in {action_id!r}")
            try:
                params[key] = [int(numbers[0]), int(numbers[1])]
            except ValueError as exc:
                raise EngineError(ERR_INVALID_ARGUMENT, f"{key} needs integer coordinates") from exc
        else:
            params[key] = value
    return action_type, params


def action_label(action_type: str, params: dict[str, Any] | None = None) -> str:
    payload = params or {}

    def at() -> str:
        value = payload.get("at")
        return f" at {value}" if value else ""

    def move() -> str:
        return f" from {payload.get('from')} to {payload.get('to')}"

    labels = {
        ACTION_TYPE_MOVE: lambda: f"Move piece{move()}",
        ACTION_TYPE_PLACE_SQUIRREL: lambda: f"Place squirrel{at()}",
        ACTION_TYPE_BUY_ELEPHANT: lambda: f"Buy elephant{at()}",
        ACTION_TYPE_BUY_LION: lambda: f"Buy lion{at()}",
        "buy_butterfly": lambda: f"Buy butterfly{at()}",
        "sell_butterfly": lambda: f"Sell butterfly{at()}",
        "butterfly_extra_turn": lambda: f"Start butterfly extra turn{at()}",
        "buy_mole": lambda: f"Buy mole{at()}",
        ACTION_TYPE_SELL_ELEPHANT: lambda: f"Sell elephant{at()}",
        ACTION_TYPE_SELL_LION: lambda: f"Sell lion{at()}",
        "sell_mole": lambda: f"Sell mole{at()}",
        "uproot_tree": lambda: f"Uproot tree{at()}",
        "plant_tree": lambda: f"Plant tree using an own mole{at()}",
        ACTION_TYPE_SELECT_COST: lambda: f"Pay squirrel{at()}",
        ACTION_TYPE_PLACE_REFUND: lambda: f"Place refunded squirrel{at()}",
        ACTION_TYPE_PLACE_ELEPHANT_BONUS: lambda: f"Place free squirrel{at()}",
        ACTION_TYPE_SKIP_ELEPHANT_BONUS: lambda: "Skip the free squirrel",
        ACTION_TYPE_CHOOSE_INITIAL_TOKEN: lambda: f"Give the time token to {payload.get('side')}",
        ACTION_TYPE_TIME_WISH: lambda: f"Spend the time token ({payload.get('side')})",
        ACTION_TYPE_MOVE_TIME_TOKEN: lambda: "Move the time token once",
        ACTION_TYPE_FAST_TIME_TOKEN: lambda: "Move the time token until AP runs out",
        ACTION_TYPE_REMOVE_PIECE_LIMIT: lambda: f"Remove over-limit piece{at()}",
        ACTION_TYPE_SELECT_PIECE: lambda: f"Select piece{at()}",
        ACTION_TYPE_CANCEL: lambda: "Cancel the current selection",
    }
    builder = labels.get(action_type)
    return builder() if builder else action_type


# ----------------------------------------------------------------------
# Session persistence helpers; agent sessions get their own folder
# ----------------------------------------------------------------------
AGENT_SAVES_DIRNAME = "agent"


def load_session_file(path: str | Path, session_id: str | None = None, **kwargs: Any) -> GameSession:
    """Load a session from a JSON save, keeping phases and pending steps."""
    from sl_func import load_game

    loaded = load_game(path)
    session = GameSession(
        game=loaded["game"],
        players=loaded["players"],
        flow=loaded.get("flow_object"),
        interaction=loaded.get("interaction_object"),
        session_id=session_id or Path(path).parent.name or Path(path).stem,
        resolve_checks_immediately=kwargs.pop("resolve_checks_immediately", True),
    )
    session.revision = int(loaded.get("revision", 0))
    # request_id de-duplication guards retries inside one run.  Results are not
    # replayed after a reload, because a replayed snapshot would describe an
    # older revision than the live session.
    session.request_cache = {}
    session.action_log = []
    session.game_over_reason = loaded.get("game_over_reason")
    session.agent_notes = loaded.get("agent_notes") or {}
    session.llm_usage = LLMUsage.from_dict(loaded.get("llm_stats"))
    session.turn_number = max(1, int(loaded.get("turn_number") or 1))
    session.finished = bool(loaded.get("finished", False))
    session.condition_results = session.flow.last_condition_results
    session.condition_reveal_count = len(session.condition_results) if session.condition_results else 0
    session.pending_message = loaded.get("message", "") or session.interaction.dialog_text
    session.normalize_after_load()
    return session


def save_session_file(session: GameSession, path: str | Path | None = None) -> Path:
    """Persist a session to its own JSON file (never a human save slot)."""
    from sl_func import save_game

    game = session._require_game()
    target = resolve_session_path(session.session_id, path)
    return save_game(
        game,
        session.players,
        flow=session.flow,
        interaction=session.interaction,
        directory=target.parent,
        filename=target.name,
        session_id=session.session_id,
        revision=session.revision,
        agent_notes=session.agent_notes,
        llm_stats=session.llm_usage.to_dict(),
        turn_number=session.turn_number,
        game_over_reason=session.game_over_reason,
        finished=session.finished,
        message=session.pending_message,
    )


def resolve_session_path(session_id: str, path: str | Path | None = None) -> Path:
    """Where a session file lives; honours the agent helper's configuration."""
    if path is not None:
        return Path(path)
    try:
        import agenthelper

        return Path(agenthelper.session_config(session_id).session_file(session_id))
    except Exception:  # pragma: no cover - fall back when the helper is absent
        return default_session_path(session_id)


def default_session_path(session_id: str, base_dir: str | Path | None = None) -> Path:
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent / "saves" / AGENT_SAVES_DIRNAME
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in session_id)
    return Path(base_dir) / safe / "session.json"


def monotonic_ms() -> int:
    """Animation clock for interfaces that reveal conditions over time."""
    return int(time.monotonic() * 1000)


def describe_coordinates() -> dict[str, Any]:
    """Coordinate contract shared by every interface."""
    return {
        "system": "[x, y]",
        "origin": "top-left cell of the 9x7 logical board",
        "x_axis": "increases to the right, 0..8",
        "y_axis": "increases downward, 0..6",
        "board_size": {"width": 9, "height": 7},
        "gamemode_1_actionable": {
            "x": [2, 6],
            "y": [1, 5],
            "description": "only these 5x5 cells accept pieces, targets and placements in G1",
        },
        "gamemode_3_actionable": {"x": [2, 6], "y": [1, 5], "description": "central 5x5 board"},
        "gamemode_2_actionable": {
            "x": [2, 6], "y": [1, 5],
            "description": "25 cells: uprooting at the left marker shifts [2,5] to [2,6]; at the right marker shifts [6,1] to [6,0]. Vacated corners are rifts.",
        },
    }
