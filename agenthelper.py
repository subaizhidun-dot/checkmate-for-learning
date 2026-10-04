"""Agent-facing interface for CheckMate.

Every function here is callable from plain Python, returns JSON-convertible
data, and never opens a Pygame window or simulates a mouse click.  The module
is a thin, restricted layer on top of :mod:`gameengine`: it validates inputs,
computes the dynamic action whitelist, checks the revision, deduplicates
retries through ``request_id`` and turns engine errors into stable error codes.

Typical use::

    create_game(session_id="demo")
    state = get_state("demo")
    legal = get_legal_actions("demo")
    apply_action("demo", legal["actions"][0]["action_id"],
                 revision=legal["revision"], request_id="r-1")
    save_session("demo")

Action ids always come from :func:`get_legal_actions` for the current revision.
The engine recomputes the whitelist on every call and rejects anything else, so
a caller cannot bypass the rules by inventing parameters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from pathlib import Path
from typing import Any

import gameengine as engine
from gameengine import GameSession
from save_policy import AutosavePolicy


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_AGENT_SAVES_DIR = BASE_DIR / "saves" / "agent"


@dataclass
class AgentSessionConfig:
    """Where one session keeps its files and whether it autosaves."""

    directory: Path = DEFAULT_AGENT_SAVES_DIR
    autosave: bool = True
    save_policy: AutosavePolicy = field(default_factory=AutosavePolicy)

    def session_file(self, session_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(session_id))
        if not safe:
            safe = "session"
        return Path(self.directory) / safe / "session.json"


# Default settings, used when a call passes no directory of its own.
CONFIG = AgentSessionConfig()

# session_id -> live session, and session_id -> that session's own settings.
# A session keeps its directory, so one session cannot redirect another.
_SESSIONS: dict[str, GameSession] = {}
_SESSION_CONFIGS: dict[str, AgentSessionConfig] = {}


def configure(directory: str | Path | None = None, autosave: bool | None = None) -> dict[str, Any]:
    """Change the defaults used by sessions created without explicit settings."""
    if directory is not None:
        CONFIG.directory = Path(directory).resolve()
    if autosave is not None:
        CONFIG.autosave = bool(autosave)
    return {"directory": str(CONFIG.directory), "autosave": CONFIG.autosave}


def new_config(directory: str | Path | None = None, autosave: bool | None = None) -> AgentSessionConfig:
    """Build a fresh per-session configuration."""
    return AgentSessionConfig(
        directory=Path(directory).resolve() if directory is not None else CONFIG.directory,
        autosave=CONFIG.autosave if autosave is None else bool(autosave),
    )


def session_config(session_id: str) -> AgentSessionConfig:
    """The settings bound to a session id (defaults for unknown ids)."""
    return _SESSION_CONFIGS.get(session_id, CONFIG)


def to_jsonable(value: Any) -> Any:
    """Convert an interface result into plain JSON types."""
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def dumps(value: Any, indent: int | None = None) -> str:
    """Serialise an interface result with ``json.dumps``."""
    return json.dumps(to_jsonable(value), indent=indent, sort_keys=False)


def _ok(**payload: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True}
    result.update(payload)
    return result


def _fail(error: Exception) -> dict[str, Any]:
    if isinstance(error, engine.EngineError):
        payload: dict[str, Any] = {"ok": False}
        payload.update(error.as_dict())
        return payload
    return {"ok": False, "code": "internal_error", "message": str(error)}


def _require_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise engine.EngineError(engine.ERR_INVALID_ARGUMENT, f"{name} must be a non-empty string")
    return value


def _require_actor(session: GameSession, actor: str | None) -> str | None:
    if actor is None:
        return None
    if actor not in {"black", "white"}:
        raise engine.EngineError(engine.ERR_INVALID_ARGUMENT, "actor must be 'black' or 'white'")
    if session.agent_color is not None and actor != session.agent_color:
        raise engine.EngineError(
            engine.ERR_WRONG_ACTOR,
            f"this session is owned by {session.agent_color!r}",
            expected_actor=session.agent_color,
            received_actor=actor,
        )
    return actor


# ----------------------------------------------------------------------
# Session lifecycle
# ----------------------------------------------------------------------
def create_game(
    session_id: str | None = None,
    agent_color: str | None = None,
    save_directory: str | Path | None = None,
    gamemode: int = 1,
) -> dict[str, Any]:
    """Create a new G1, G2 or G3 session and return its first snapshot.

    ``agent_color`` restricts :func:`apply_action` to one side; leave it None
    to drive both sides.  ``save_directory`` isolates this session's saves.
    """
    try:
        if not isinstance(gamemode, int) or isinstance(gamemode, bool) or gamemode not in {1, 2, 3}:
            raise engine.EngineError(engine.ERR_INVALID_ARGUMENT, "gamemode must be 1, 2 or 3")
        if agent_color is not None:
            _require_str(agent_color, "agent_color")
        if agent_color not in {None, "black", "white"}:
            raise engine.EngineError(engine.ERR_INVALID_ARGUMENT, "agent_color must be 'black' or 'white'")
        if session_id is None:
            session_id = datetime.now().strftime("s%Y%m%d_%H%M%S_%f")
        else:
            _require_str(session_id, "session_id")
        # Settings belong to this session only.
        settings = new_config(directory=save_directory)
        _SESSION_CONFIGS[session_id] = settings
        factory = {1: GameSession.new_g1, 2: GameSession.new_g2, 3: GameSession.new_g3}[gamemode]
        session = factory(session_id=session_id, agent_color=agent_color)
        _SESSIONS[session_id] = session
        if settings.autosave:
            save_session(session_id)
        return _ok(
            session_id=session_id,
            agent_color=agent_color,
            save_directory=str(settings.directory),
            state=session.snapshot(),
            legal_actions=session.legal_actions(),
            coordinates=engine.describe_coordinates(),
        )
    except Exception as error:  # noqa: BLE001 - returned as a structured error
        return _fail(error)


def get_session(session_id: str) -> GameSession:
    """Return the live session, reloading it from disk when necessary."""
    _require_str(session_id, "session_id")
    session = _SESSIONS.get(session_id)
    if session is not None:
        return session
    config = session_config(session_id)
    path = config.session_file(session_id)
    if not path.exists():
        raise engine.EngineError(
            engine.ERR_UNKNOWN_SESSION,
            f"no live session {session_id!r} and no saved session at {path}",
            session_id=session_id,
            path=str(path),
        )
    try:
        session = engine.load_session_file(path, session_id=session_id)
    except Exception as error:  # noqa: BLE001
        raise engine.EngineError(engine.ERR_LOAD_FAILED, f"could not restore {session_id!r}: {error}")
    session.wish_save_directory = path.parent / "wish"
    _SESSIONS[session_id] = session
    return session


def save_session(session_id: str) -> dict[str, Any]:
    """Write this session to its own agent save file."""
    try:
        session = get_session(session_id)
        path = engine.save_session_file(session)
        session_config(session_id).save_policy.mark_saved(session)
        return _ok(session_id=session_id, path=str(path), revision=session.revision)
    except Exception as error:  # noqa: BLE001
        return _fail(error)


def configure_agent_play_mode(session_id: str, enabled: bool, llm_colors=("white",)) -> dict[str, Any]:
    """Controller setting, not a model tool. Defer LLM saves to human turns."""
    try:
        get_session(session_id)
        session_config(session_id).save_policy.configure(enabled, llm_colors)
        return _ok(agent_play_mode=bool(enabled), llm_colors=list(llm_colors))
    except Exception as error:
        return _fail(error)


def load_session(
    session_id: str,
    path: str | Path | None = None,
    save_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Restore a session from a JSON save, including special pending phases."""
    try:
        _require_str(session_id, "session_id")
        settings = new_config(directory=save_directory)
        _SESSION_CONFIGS[session_id] = settings
        source = Path(path) if path is not None else settings.session_file(session_id)
        if not source.exists():
            raise engine.EngineError(
                engine.ERR_LOAD_FAILED, f"no session file at {source}", path=str(source)
            )
        session = engine.load_session_file(source, session_id=session_id)
        session.wish_save_directory = source.parent / "wish"
        _SESSIONS[session_id] = session
        settings.save_policy.mark_saved(session)
        return _ok(
            session_id=session_id,
            path=str(source),
            state=session.snapshot(),
            legal_actions=session.legal_actions(),
        )
    except Exception as error:  # noqa: BLE001
        return _fail(error)


def list_sessions() -> dict[str, Any]:
    """List agent session ids found in the agent save directory."""
    directory = Path(CONFIG.directory)
    sessions: list[dict[str, Any]] = []
    if directory.exists():
        for folder in sorted(directory.iterdir()):
            save_file = folder / "session.json"
            if folder.is_dir() and save_file.exists():
                sessions.append({"session_id": folder.name, "path": str(save_file)})
    return _ok(directory=str(directory), sessions=sessions)


def close_session(session_id: str, reason: str = "closed_by_caller") -> dict[str, Any]:
    """Forget the live session; save it first when autosave is on."""
    try:
        _require_str(session_id, "session_id")
        session = _SESSIONS.pop(session_id, None)
        saved_path = None
        if session is not None and session_config(session_id).autosave and session_config(session_id).save_policy.should_save(session):
            # Save the live session itself; do not reload a stale file.
            saved_path = engine.save_session_file(session)
        return _ok(
            session_id=session_id,
            reason=reason,
            closed=session is not None,
            path=str(saved_path) if saved_path is not None else None,
        )
    except Exception as error:  # noqa: BLE001
        return _fail(error)


def end_game(
    session_id: str,
    reason: str = "ended_by_caller",
    loser: str | None = None,
    request_id: str | None = None,
    revision: int | None = None,
) -> dict[str, Any]:
    """Finish or resign a session.  Pass the current revision to guard staleness."""
    try:
        session = get_session(session_id)
        if loser is not None and loser not in {"black", "white"}:
            raise engine.EngineError(engine.ERR_INVALID_ARGUMENT, "loser must be 'black' or 'white'")
        if request_id is not None:
            cached = session.request_cache.get(request_id)
            if cached is not None and cached.get("kind") == "end_game":
                replay = dict(cached["result"])
                replay["replayed"] = True
                return replay
        if session.is_over():
            # Already finished: ending again is a no-op with the current state.
            snapshot = session.snapshot()
            return _ok(session_id=session_id, request_id=request_id, replayed=False,
                       already_ended=True, state=snapshot, result=snapshot["result"])
        if revision is not None and revision != session.revision:
            raise engine.EngineError(
                engine.ERR_REVISION_MISMATCH,
                f"revision {revision} is stale; the current revision is {session.revision}",
                expected_revision=session.revision,
                received_revision=revision,
            )
        if loser is not None:
            reason = "resignation"
        snapshot = session.end_session(reason=reason, loser=loser)
        settings = session_config(session_id)
        if settings.autosave and not settings.save_policy.agent_play_mode:
            save_session(session_id)
        payload = _ok(session_id=session_id, request_id=request_id, replayed=False,
                      state=snapshot, result=snapshot["result"])
        if request_id is not None:
            session.request_cache[request_id] = {"kind": "end_game", "result": dict(payload)}
            while len(session.request_cache) > session.MAX_REQUEST_CACHE:
                session.request_cache.pop(next(iter(session.request_cache)))
        return payload
    except Exception as error:  # noqa: BLE001
        return _fail(error)


# ----------------------------------------------------------------------
# State and legal actions
# ----------------------------------------------------------------------
PIECE_SYMBOLS = {
    ("black", "pine"): "bp",
    ("white", "pine"): "wp",
    ("black", "squirrel"): "bS",
    ("white", "squirrel"): "wS",
    ("black", "lion"): "bL",
    ("white", "lion"): "wL",
    ("black", "elephant"): "bE",
    ("white", "elephant"): "wE",
    ("black", "mole"): "bM",
    ("white", "mole"): "wM",
    ("black", "tree"): "bT",
    ("white", "tree"): "wT",
    ("black", "butterfly"): "bB",
    ("white", "butterfly"): "wB",
}


def board_map(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Text picture of the board plus the list of occupied cells."""
    board = snapshot.get("board") or []
    rows: list[str] = []
    corners = set(snapshot.get("expanded_corners", []))
    from game2 import SHIFTED_CORNERS
    rifts = {SHIFTED_CORNERS[corner][0] for corner in corners}
    extended = {SHIFTED_CORNERS[corner][1] for corner in corners}
    for y in range(7):
        cells: list[str] = []
        for x in range(9):
            piece = None
            if y < len(board) and x < len(board[y]):
                piece = board[y][x]
            if piece is None:
                cells.append("##" if (x, y) in rifts else " ." if (x, y) in extended or 2 <= x <= 6 and 1 <= y <= 5 else "  ")
            else:
                cells.append(PIECE_SYMBOLS.get((piece["owner"], piece["kind"]), "??"))
        rows.append(f"y={y} | " + " ".join(cells))
    occupancies: list[dict[str, Any]] = []
    for y in range(7):
        for x in range(9):
            if y < len(board) and x < len(board[y]) and board[y][x] is not None:
                piece = board[y][x]
                occupancies.append(
                    {"at": [x, y], **piece}
                )
    return {
        "legend": {
            "bS": "black squirrel",
            "wS": "white squirrel",
            "bL": "black lion",
            "wL": "white lion",
            "bE": "black elephant",
            "wE": "white elephant",
            "bp": "black pinecone",
            "wp": "white pinecone",
            "bM": "black mole",
            "wM": "white mole",
            "bT": "black tree (see rooted state in occupied_cells)",
            "wT": "white tree (see rooted state in occupied_cells)",
            "bB": "black butterfly",
            "wB": "white butterfly",
            "##": "rift: cannot move or place here",
            " . ": "empty actionable cell",
            "   ": "outside the actionable area",
        },
        "header": "x=  0  1  2  3  4  5  6  7  8",
        "rows": rows,
        "occupied_cells": occupancies,
    }


def get_state(session_id: str, log_limit: int = 10, include_board_map: bool = True) -> dict[str, Any]:
    """Return a detached snapshot of the session, ready for ``json.dumps``."""
    try:
        session = get_session(session_id)
        if not isinstance(log_limit, int) or isinstance(log_limit, bool) or log_limit < 0:
            raise engine.EngineError(engine.ERR_INVALID_ARGUMENT, "log_limit must be a non-negative integer")
        snapshot = session.snapshot()
        payload: dict[str, Any] = {
            "session_id": session_id,
            "gamemode": snapshot["gamemode"],
            "agent_color": session.agent_color,
            "revision": snapshot["revision"],
            "phase": snapshot["phase"],
            "current_player": snapshot["current_player"],
            "actor": snapshot["actor"],
            "time_token_owner": snapshot["time_token_owner"],
            "current_ap": snapshot["current_ap"],
            "max_ap": snapshot["max_ap"],
            "players": snapshot["players"],
            "pending": snapshot["pending"],
            "condition_results": snapshot["condition_results"],
            "result": snapshot["result"],
            "message": snapshot["message"],
            "new_pieces": snapshot["new_pieces"],
            "finished": snapshot["finished"],
            "new_game_plus_available": snapshot["new_game_plus_available"],
            "coordinates": engine.describe_coordinates(),
            "board": snapshot["board"],
            "log": session.recent_log(log_limit),
        }
        for key in ("tree_markers", "expanded_corners", "skip_token_selection", "g3_turn"):
            if key in snapshot:
                payload[key] = snapshot[key]
        if include_board_map:
            payload["board_map"] = board_map(snapshot)
        return _ok(**payload)
    except Exception as error:  # noqa: BLE001
        return _fail(error)


def get_legal_actions(session_id: str) -> dict[str, Any]:
    """Return the dynamic action whitelist for the current revision."""
    try:
        session = get_session(session_id)
        actions = session.legal_actions()
        owner = session.actor()
        restricted = (
            session.agent_color is not None
            and owner in {"black", "white"}
            and owner != session.agent_color
        )
        stuck = session.is_stuck()
        if stuck:
            note = (
                "no legal action exists in this position (the board has no free "
                "actionable cell); no rule is invented here - end the session with "
                "end_game and a reason chosen by the caller"
            )
        elif restricted:
            note = f"the deciding side is {owner}; this session is limited to {session.agent_color}"
        else:
            note = None
        return _ok(
            session_id=session_id,
            revision=session.revision,
            phase=session.flow.phase,
            actor=owner,
            agent_color=session.agent_color,
            awaiting_other_side=restricted,
            stuck=stuck,
            note=note,
            count=len(actions),
            actions=actions,
        )
    except Exception as error:  # noqa: BLE001
        return _fail(error)


def describe_actions(session_id: str) -> dict[str, Any]:
    """One-line-per-action view, handy for logs and prompts."""
    result = get_legal_actions(session_id)
    if not result.get("ok"):
        return result
    result["lines"] = [f'{item["action_id"]}  ->  {item["label"]}' for item in result["actions"]]
    return result


# ----------------------------------------------------------------------
# Action execution
# ----------------------------------------------------------------------
def apply_action(
    session_id: str,
    action_id: str,
    revision: int,
    request_id: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Execute one action from :func:`get_legal_actions`.

    The engine re-checks the revision, the deciding side, the phase and the
    full rule set.  On rejection nothing changes and a stable error code is
    returned.  Re-sending the same ``request_id`` with the same action and
    revision returns the original result instead of playing twice.
    """
    try:
        _require_str(session_id, "session_id")
        _require_str(action_id, "action_id")
        session = get_session(session_id)
        _require_actor(session, actor)
        expected = session.agent_color if session.agent_color is not None else actor
        result = session.submit(
            action_id=action_id,
            revision=revision,
            request_id=request_id,
            actor=expected,
        )
        settings = session_config(session_id)
        if settings.autosave and not result["replayed"] and settings.save_policy.should_save(session):
            save_session(session_id)
        state = result.pop("state")
        payload = _ok(session_id=session_id, state=state, **result)
        return payload
    except Exception as error:  # noqa: BLE001
        return _fail(error)


def choose_initial_token(session_id: str, side: str, revision: int | None = None, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper for the opening time-token choice."""
    try:
        session = get_session(session_id)
        if revision is None:
            revision = session.revision
        action_id = engine.encode_action_id(engine.ACTION_TYPE_CHOOSE_INITIAL_TOKEN, {"side": side})
        return apply_action(session_id, action_id, revision, **kwargs)
    except Exception as error:  # noqa: BLE001
        return _fail(error)


def cancel_action(session_id: str, revision: int | None = None, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper for the cancel action, when the phase allows it."""
    try:
        session = get_session(session_id)
        if revision is None:
            revision = session.revision
        return apply_action(session_id, engine.ACTION_TYPE_CANCEL, revision, **kwargs)
    except Exception as error:  # noqa: BLE001
        return _fail(error)


# ----------------------------------------------------------------------
# Prompt-friendly views
# ----------------------------------------------------------------------
def summarize(session_id: str, log_limit: int = 5) -> dict[str, Any]:
    """A compact state plus the legal action list, for building agent prompts."""
    try:
        state = get_state(session_id, log_limit=log_limit)
        if not state.get("ok"):
            return state
        legal = get_legal_actions(session_id)
        if not legal.get("ok"):
            return legal
        summary = {
            "session_id": session_id,
            "revision": state["revision"],
            "phase": state["phase"],
            "actor": state["actor"],
            "current_player": state["current_player"],
            "time_token_owner": state["time_token_owner"],
            "current_ap": state["current_ap"],
            "max_ap": state["max_ap"],
            "players": state["players"],
            "pending": state["pending"],
            "result": state["result"],
            "condition_results": state["condition_results"],
            "message": state["message"],
            "coordinates": state["coordinates"],
        }
        return _ok(
            summary=summary,
            board_map=state.get("board_map"),
            actions=legal["actions"],
            action_lines=[f'{item["action_id"]}  ->  {item["label"]}' for item in legal["actions"]],
        )
    except Exception as error:  # noqa: BLE001
        return _fail(error)


def play_action(session_id: str, action_type: str, params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """Execute an action given by type and parameters instead of an id.

    The engine converts the pair into an id and still checks it against the
    current whitelist, so this is exactly as safe as :func:`apply_action`.
    """
    try:
        session = get_session(session_id)
        action_id = engine.encode_action_id(action_type, params)
        revision = kwargs.pop("revision", None)
        if revision is None:
            revision = session.revision
        return apply_action(session_id, action_id, revision, **kwargs)
    except Exception as error:  # noqa: BLE001
        return _fail(error)


def get_coordinates(session_id: str | None = None) -> dict[str, Any]:
    """Explain the coordinate system, the board size and the actionable area."""
    try:
        payload: dict[str, Any] = {"coordinates": engine.describe_coordinates()}
        if session_id is not None:
            session = get_session(session_id)
            payload["session_id"] = session_id
            payload["gamemode"] = session.game.gamemode if session.game else None
            payload["actionable_cells"] = [
                [x, y] for y in range(7) for x in range(9)
                if engine.is_player_accessible(session.game, (x, y))
            ] if session.game is not None else []
        return _ok(**payload)
    except Exception as error:  # noqa: BLE001
        return _fail(error)
