"""Save and load helpers.

Human save slots and autosaves live in ``saves/``.  Agent sessions use their
own directory (``saves/agent/<session_id>/``) so a scripted agent can never
overwrite a human save.

A new file is written completely before older files of the same slot are
removed; a failed write leaves the previous save untouched.
"""

from __future__ import annotations

import json
from dataclasses import fields as dataclass_fields
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from basicgame import Chessboard, Interaction, Playerstate, Resource, SPECIAL_KINDS
from game2 import G2_CORNER_LAYOUT


BASE_DIR = Path(__file__).resolve().parent
SAVES_DIR = BASE_DIR / "saves"
SAVE_VERSION = 1
# How many post-wish continuation saves to keep per directory.
WISH_SAVES_KEPT = 1

FLOW_FIELDS = (
    "phase",
    "pending_action",
    "pending_piece_limit_owner",
    "pending_refund_owner",
    "pending_refund_count",
    "pending_refund_kind",
    "time_wish_winner",
    "checking_action_message",
    "last_public_check",
    "public_check_archive",
    "turn_start_board",
    "turn_actions",
    "agent_intents",
)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_game(
    game: Chessboard,
    players: dict[str, Playerstate],
    flow: Any | None = None,
    interaction: Any | None = None,
    slot: int | None = None,
    autosave: bool = False,
    directory: str | Path | None = None,
    filename: str | None = None,
    prefix: str | None = None,
    session_id: str | None = None,
    revision: int | None = None,
    request_cache: dict[str, Any] | None = None,
    action_log: list[dict[str, Any]] | None = None,
    game_over_reason: str | None = None,
    finished: bool | None = None,
    message: str | None = None,
    agent_notes: dict[str, str] | None = None,
    llm_stats: dict[str, Any] | None = None,
    turn_number: int = 1,
) -> Path:
    target_dir = Path(directory) if directory is not None else SAVES_DIR
    if filename is not None:
        destination = target_dir / filename
        cleanup_pattern = None
    elif prefix is not None:
        # A dedicated file family that no slot or autosave cleanup touches.
        destination = target_dir / f"{prefix}_{timestamp()}.json"
        cleanup_pattern = None
    elif autosave:
        destination = target_dir / f"autosave_{timestamp()}.json"
        cleanup_pattern = "autosave_*.json"
    else:
        if slot not in {1, 2, 3}:
            raise ValueError("slot must be 1, 2, or 3")
        destination = target_dir / f"save{slot}_{timestamp()}.json"
        cleanup_pattern = f"save{slot}_*.json"

    data = {
        "version": SAVE_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "gamemode": game.gamemode,
        "current_player": game.current_player,
        "time_token_owner": game.time_token_owner,
        "current_ap": game.current_ap,
        "max_ap": game.max_ap,
        "new_piece": [list(pos) for pos in game.new_piece],
        "board_matrix": serialize_board(game.board_matrix),
        "players": serialize_players(players),
        "mate_loser": getattr(game, "mate_loser", None),
        "flow": serialize_flow(flow),
        "interaction": serialize_interaction(interaction),
        "session_id": session_id,
        "revision": revision,
        "game_over_reason": game_over_reason,
        "finished": finished,
        "message": message,
        "agent_notes": agent_notes or {},
        "llm_stats": llm_stats or {},
        "turn_number": turn_number,
    }
    if game.gamemode == 2:
        data["g2_corner_layout"] = G2_CORNER_LAYOUT
        data.update(tree_markers=[list(pos) for pos in sorted(game.tree_markers)],
                    skip_token_selection=game.skip_token_selection,
                    expanded_corners=sorted(game.expanded_corners))
    elif game.gamemode == 3:
        data["g3_turn"] = game.g3_turn.to_dict()
    # Publish a complete file before removing any previous save, including
    # when two saves share the same timestamp and destination filename.
    contents = json.dumps(data, indent=2)
    target_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=target_dir,
                                suffix=".tmp", delete=False) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(contents)
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    # A named agent session keeps its own file; only slot and autosave
    # patterns retire older files, and only once the new file is in place.
    if cleanup_pattern is not None:
        for old_path in target_dir.glob(cleanup_pattern):
            if old_path != destination and old_path.is_file():
                old_path.unlink(missing_ok=True)
    if prefix is not None:
        retire_older_files(f"{prefix}_*.json", target_dir, keep=WISH_SAVES_KEPT)
    return destination


def retire_older_files(pattern: str, directory: str | Path | None = None, keep: int = 1) -> list[Path]:
    """Delete all but the newest ``keep`` files matching ``pattern``.

    Used for the post-wish continuation saves: they are written outside the
    slot/autosave families, so without this they would accumulate forever.
    Only called after the new file is fully in place.
    """
    target_dir = Path(directory) if directory is not None else SAVES_DIR
    files = sorted(
        (path for path in target_dir.glob(pattern) if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed: list[Path] = []
    for old_path in files[max(0, keep):]:
        old_path.unlink(missing_ok=True)
        removed.append(old_path)
    return removed


def load_game(path: str | Path) -> dict[str, Any]:
    """Load a save file into a plain dictionary of game objects and state."""
    save_path = Path(path)
    if not save_path.exists():
        raise FileNotFoundError(save_path)

    try:
        data = json.loads(save_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid save file") from exc

    if data.get("version") != SAVE_VERSION:
        raise ValueError("unsupported save version")
    if int(data.get("gamemode", 1)) == 2:
        if data.get("g2_corner_layout", 1) == 1:
            upgrade_g2_corner_layout(data)
        if data.get("g2_corner_layout") == 2:
            names = {"black": "lower_left", "white": "upper_right"}
            data["expanded_corners"] = [names[owner] for owner in data.get("expanded_corners", [])]
            data["g2_corner_layout"] = G2_CORNER_LAYOUT

    board = deserialize_board(data.get("board_matrix", []))
    game = Chessboard(int(data.get("gamemode", 1)), board)
    game.current_player = data.get("current_player", "black")
    game.time_token_owner = data.get("time_token_owner")
    game.current_ap = int(data.get("current_ap", 1))
    game.max_ap = int(data.get("max_ap", 1))
    game.new_piece = [tuple(pos) for pos in data.get("new_piece", [])]
    if game.gamemode == 2:
        game.tree_markers = {tuple(pos) for pos in data.get("tree_markers", [])}
        game.skip_token_selection = bool(data.get("skip_token_selection", False))
        game.expanded_corners = set(data.get("expanded_corners", []))
    elif game.gamemode == 3:
        from game3 import G3Turn
        game.g3_turn = G3Turn.from_dict(data["g3_turn"]) if "g3_turn" in data else G3Turn.capture(game)
    game.mate_loser = data.get("mate_loser")

    flow_data = dict(data.get("flow") or {})
    if "phase" not in flow_data:
        # Older saves without a phase need an initial phase inferred once.
        if game.mate_loser in {"black", "white"} or data.get("finished"):
            flow_data["phase"] = "game_over"
        elif game.time_token_owner is not None or getattr(game, "skip_token_selection", False):
            flow_data["phase"] = "playing"
        else:
            flow_data["phase"] = "choose_token"

    players = deserialize_players(data.get("players", {}), board)
    return {
        "game": game,
        "players": players,
        "path": save_path,
        "flow": flow_data,
        "flow_object": deserialize_flow(flow_data),
        "interaction": data.get("interaction", {}),
        "interaction_object": deserialize_interaction(data.get("interaction", {})),
        "session_id": data.get("session_id"),
        "revision": int(data.get("revision") or 0),
        "game_over_reason": data.get("game_over_reason"),
        "finished": data.get("finished"),
        "message": data.get("message"),
        "agent_notes": {color: text for color, text in (data.get("agent_notes") or {}).items()
                        if color in {"black", "white"} and isinstance(text, str)},
        "llm_stats": data.get("llm_stats") or {},
        "turn_number": data.get("turn_number") or 1,
    }


def upgrade_g2_corner_layout(data: dict[str, Any]) -> None:
    """Migrate version-1 corner tiles and pending coordinates to layout 2."""
    expanded = set(data.get("expanded_corners", []))
    board = data["board_matrix"]
    moved = {}
    for original, extended, previous_owner, current_owner in (
        ((2, 5), (2, 6), "white", "black"),
        ((6, 1), (6, 0), "black", "white"),
    ):
        start = extended if previous_owner in expanded else original
        end = extended if current_owner in expanded else original
        if start != end:
            board[end[1]][end[0]] = board[start[1]][start[0]]
            board[start[1]][start[0]] = None
            moved[start] = end

    def position(value):
        return list(moved.get(tuple(value), tuple(value))) if value is not None else None

    data["new_piece"] = [position(value) for value in data.get("new_piece", [])]
    interaction = data.get("interaction") or {}
    for name in ("selected_pos", "elephant_start_position"):
        if name in interaction:
            interaction[name] = position(interaction[name])
    interaction["selected_cost"] = [position(value) for value in interaction.get("selected_cost", [])]
    flow = data.get("flow") or {}
    for item in flow.get("temp_removed", []):
        item["position"] = position(item["position"])
    flow["temp_placed"] = [position(value) for value in flow.get("temp_placed", [])]
    if moved:
        flow["last_condition_results"] = None
    data["g2_corner_layout"] = 2


def list_save_slots(directory: str | Path | None = None) -> dict[str, Path | None]:
    target_dir = Path(directory) if directory is not None else SAVES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    return {
        "slot_1": latest_file("save1_*.json", target_dir),
        "slot_2": latest_file("save2_*.json", target_dir),
        "slot_3": latest_file("save3_*.json", target_dir),
        "autosave": latest_file("autosave_*.json", target_dir),
    }


def latest_file(pattern: str, directory: str | Path | None = None) -> Path | None:
    target_dir = Path(directory) if directory is not None else SAVES_DIR
    files = sorted(target_dir.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def serialize_board(board: list[list[Any]]) -> list[list[dict[str, Any] | None]]:
    return [
        [
            serialize_resource(piece)
            for piece in row
        ]
        for row in board
    ]


def deserialize_board(data: list[list[dict[str, Any] | None]]) -> list[list[Resource | None]]:
    board: list[list[Resource | None]] = []
    for row in data:
        board_row: list[Resource | None] = []
        for item in row:
            if item is None:
                board_row.append(None)
            else:
                board_row.append(deserialize_resource(item))
        board.append(board_row)
    return board


def serialize_players(players: dict[str, Playerstate]) -> dict[str, dict[str, Any]]:
    return {
        color: {
            "color": player.color,
            "num_pieces": player.num_pieces,
            "num_squirrels": player.num_squirrels,
            **{f"has_{kind}": bool(getattr(player, f"has_{kind}")) for kind in SPECIAL_KINDS},
        }
        for color, player in players.items()
    }


def serialize_flow(flow: Any | None) -> dict[str, Any]:
    if flow is None:
        return {}
    result: dict[str, Any] = {name: getattr(flow, name, None) for name in FLOW_FIELDS}
    last_condition_results = getattr(flow, "last_condition_results", None)
    result["last_condition_results"] = (
        list(last_condition_results) if last_condition_results is not None else None
    )
    result["temp_removed"] = [
        {"position": list(position), "piece": serialize_resource(piece)}
        for position, piece in getattr(flow, "temp_removed", [])
    ]
    result["temp_placed"] = [list(position) for position in getattr(flow, "temp_placed", [])]
    new_pieces = getattr(flow, "temp_new_pieces", None)
    result["temp_new_pieces"] = None if new_pieces is None else [list(pos) for pos in new_pieces]
    return result


def serialize_interaction(interaction: Any | None) -> dict[str, Any]:
    if interaction is None:
        return {}
    selected_pos = getattr(interaction, "selected_pos", None)
    elephant_start = getattr(interaction, "elephant_start_position", None)
    return {
        "selected_pos": list(selected_pos) if selected_pos is not None else None,
        "selected_resource": getattr(interaction, "selected_resource", None),
        "selected_cost": [list(pos) for pos in getattr(interaction, "selected_cost", [])],
        "trading_kind": getattr(interaction, "trading_kind", None),
        "trading_piece": serialize_resource(getattr(interaction, "trading_piece", None)),
        "remaining_trading_count": getattr(interaction, "remaining_trading_count", 0),
        "tooltip_text": getattr(interaction, "tooltip_text", ""),
        "dialog_text": getattr(interaction, "dialog_text", ""),
        "after_elephant_push": getattr(interaction, "after_elephant_push", False),
        "elephant_start_position": list(elephant_start) if elephant_start is not None else None,
    }


def serialize_resource(piece: Any | None) -> dict[str, Any] | None:
    if piece is None:
        return None
    result: dict[str, Any] = {"owner": str(piece.owner), "kind": str(piece.kind)}
    if piece.kind == "tree":
        result["rooted"] = piece.rooted
    return result


def deserialize_resource(data: dict[str, Any] | None) -> Resource | None:
    if not data:
        return None
    return Resource(str(data.get("owner", "black")), str(data.get("kind", "squirrel")), rooted=bool(data.get("rooted", True)))


def deserialize_flow(data: dict[str, Any] | None) -> Any:
    """Rebuild a GameFlow, tolerating saves written by older versions."""
    try:
        from gameengine import GameFlow
    except ImportError:  # pragma: no cover - only when gameengine is unavailable
        return None
    values = dict(data or {})
    try:
        allowed = {item.name for item in dataclass_fields(GameFlow)}
    except TypeError:  # pragma: no cover - defensive
        allowed = set(FLOW_FIELDS)
    kwargs: dict[str, Any] = {key: value for key, value in values.items() if key in allowed}
    kwargs["public_check_archive"] = values.get("public_check_archive") or (
        [values["last_public_check"]] if values.get("last_public_check") else [])
    kwargs["temp_removed"] = [
        (tuple(item["position"]), deserialize_resource(item.get("piece")))
        for item in values.get("temp_removed", [])
        if item.get("position") is not None and item.get("piece") is not None
    ]
    kwargs["temp_placed"] = [tuple(pos) for pos in values.get("temp_placed", [])]
    new_pieces = values.get("temp_new_pieces")
    kwargs["temp_new_pieces"] = None if new_pieces is None else [tuple(pos) for pos in new_pieces]
    for key in ("pending_refund_count",):
        if key in kwargs and kwargs[key] is None:
            kwargs[key] = 0
    return GameFlow(**kwargs)


def deserialize_interaction(data: dict[str, Any] | None) -> Interaction:
    """Rebuild an Interaction from save data (empty data gives a fresh one)."""
    interaction = Interaction()
    if not data:
        return interaction
    selected_pos = data.get("selected_pos")
    elephant_start = data.get("elephant_start_position")
    interaction.selected_pos = tuple(selected_pos) if selected_pos is not None else None
    interaction.selected_resource = data.get("selected_resource")
    interaction.selected_cost = [tuple(pos) for pos in data.get("selected_cost", [])]
    interaction.trading_kind = data.get("trading_kind")
    interaction.trading_piece = deserialize_resource(data.get("trading_piece"))
    interaction.remaining_trading_count = int(data.get("remaining_trading_count") or 0)
    interaction.tooltip_text = data.get("tooltip_text", "")
    interaction.dialog_text = data.get("dialog_text", "")
    interaction.after_elephant_push = bool(data.get("after_elephant_push", False))
    interaction.elephant_start_position = (
        tuple(elephant_start) if elephant_start is not None else None
    )
    return interaction


def deserialize_players(
    data: dict[str, dict[str, Any]],
    board: list[list[Resource | None]],
) -> dict[str, Playerstate]:
    # The board is authoritative, including for older saves with stale flags.
    players = {color: Playerstate(color, 0, 0) for color in ("black", "white")}
    refresh_players_from_board(players, board)
    return players


def refresh_players_from_board(
    players: dict[str, Playerstate],
    board: list[list[Resource | None]],
) -> None:
    for player in players.values():
        player.refresh_pieces(board)
