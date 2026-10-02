from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from basicgame import Chessboard, Playerstate, Resource


BASE_DIR = Path(__file__).resolve().parent
SAVES_DIR = BASE_DIR / "saves"
SAVE_VERSION = 1


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_game(
    game: Chessboard,
    players: dict[str, Playerstate],
    flow: Any | None = None,
    interaction: Any | None = None,
    slot: int | None = None,
    autosave: bool = False,
) -> Path:
    if autosave:
        prefix = "autosave"
    else:
        if slot not in {1, 2, 3}:
            raise ValueError("slot must be 1, 2, or 3")
        prefix = f"save{slot}"

    filename = f"{prefix}_{timestamp()}.json"
    SAVES_DIR.mkdir(exist_ok=True)
    path = SAVES_DIR / filename
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
    }
    # Publish a complete file before removing any previous saves, including
    # when two saves share the same timestamp and destination filename.
    contents = json.dumps(data, indent=2)
    temporary_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=SAVES_DIR,
                                suffix=".tmp", delete=False) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(contents)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    for old_path in SAVES_DIR.glob(f"{prefix}_*.json"):
        if old_path != path and old_path.is_file():
            old_path.unlink(missing_ok=True)
    return path


def load_game(path: str | Path) -> dict[str, Any]:
    save_path = Path(path)
    if not save_path.exists():
        raise FileNotFoundError(save_path)

    try:
        data = json.loads(save_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid save file") from exc

    if data.get("version") != SAVE_VERSION:
        raise ValueError("unsupported save version")

    board = deserialize_board(data.get("board_matrix", []))
    game = Chessboard(int(data.get("gamemode", 1)), board)
    game.current_player = data.get("current_player", "black")
    game.time_token_owner = data.get("time_token_owner")
    game.current_ap = int(data.get("current_ap", 1))
    game.max_ap = int(data.get("max_ap", 1))
    game.new_piece = [tuple(pos) for pos in data.get("new_piece", [])]
    game.mate_loser = data.get("mate_loser")

    players = deserialize_players(data.get("players", {}), board)
    return {
        "game": game,
        "players": players,
        "path": save_path,
        "flow": data.get("flow", {}),
        "interaction": data.get("interaction", {}),
    }


def list_save_slots() -> dict[str, Path | None]:
    SAVES_DIR.mkdir(exist_ok=True)
    return {
        "slot_1": latest_file("save1_*.json"),
        "slot_2": latest_file("save2_*.json"),
        "slot_3": latest_file("save3_*.json"),
        "autosave": latest_file("autosave_*.json"),
    }


def latest_file(pattern: str) -> Path | None:
    files = sorted(SAVES_DIR.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def serialize_board(board: list[list[Any]]) -> list[list[dict[str, str] | None]]:
    return [
        [
            None if piece is None else {"owner": str(piece.owner), "kind": str(piece.kind)}
            for piece in row
        ]
        for row in board
    ]


def deserialize_board(data: list[list[dict[str, str] | None]]) -> list[list[Resource | None]]:
    board: list[list[Resource | None]] = []
    for row in data:
        board_row: list[Resource | None] = []
        for item in row:
            if item is None:
                board_row.append(None)
            else:
                board_row.append(Resource(item["owner"], item["kind"]))
        board.append(board_row)
    return board


def serialize_players(players: dict[str, Playerstate]) -> dict[str, dict[str, Any]]:
    return {
        color: {
            "color": player.color,
            "num_pieces": player.num_pieces,
            "num_squirrels": player.num_squirrels,
            "has_elephant": player.has_elephant,
            "has_lion": player.has_lion,
        }
        for color, player in players.items()
    }


def serialize_flow(flow: Any | None) -> dict[str, Any]:
    if flow is None:
        return {}
    result: dict[str, Any] = {}
    for name in (
        "phase",
        "pending_action",
        "pending_piece_limit_owner",
        "pending_refund_owner",
        "pending_refund_count",
        "pending_refund_kind",
        "time_wish_winner",
        "checking_action_message",
        "checking_started_at",
    ):
        result[name] = getattr(flow, name, None)
    last_condition_results = getattr(flow, "last_condition_results", None)
    result["last_condition_results"] = list(last_condition_results) if last_condition_results is not None else None
    result["temp_removed"] = [
        {"position": list(position), "piece": serialize_resource(piece)}
        for position, piece in getattr(flow, "temp_removed", [])
    ]
    result["temp_placed"] = [list(position) for position in getattr(flow, "temp_placed", [])]
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
        "dialog_text": getattr(interaction, "dialog_text", ""),
        "after_elephant_push": getattr(interaction, "after_elephant_push", False),
        "elephant_start_position": list(elephant_start) if elephant_start is not None else None,
    }


def serialize_resource(piece: Any | None) -> dict[str, str] | None:
    if piece is None:
        return None
    return {"owner": str(piece.owner), "kind": str(piece.kind)}


def deserialize_players(
    data: dict[str, dict[str, Any]],
    board: list[list[Resource | None]],
) -> dict[str, Playerstate]:
    if data:
        players = {}
        for color, values in data.items():
            player = Playerstate(
                values.get("color", color),
                int(values.get("num_pieces", 0)),
                int(values.get("num_squirrels", 0)),
            )
            player.has_elephant = bool(values.get("has_elephant", False))
            player.has_lion = bool(values.get("has_lion", False))
            players[color] = player
        refresh_players_from_board(players, board)
        return players

    counts = {
        "black": {"pieces": 0, "squirrels": 0, "has_elephant": False, "has_lion": False},
        "white": {"pieces": 0, "squirrels": 0, "has_elephant": False, "has_lion": False},
    }
    for row in board:
        for piece in row:
            if piece is None or piece.owner not in counts:
                continue
            if piece.is_piece:
                counts[piece.owner]["pieces"] += 1
            if piece.kind == "squirrel":
                counts[piece.owner]["squirrels"] += 1
            elif piece.kind == "elephant":
                counts[piece.owner]["has_elephant"] = True
            elif piece.kind == "lion":
                counts[piece.owner]["has_lion"] = True

    players = {}
    for color, values in counts.items():
        player = Playerstate(color, values["pieces"], values["squirrels"])
        player.has_elephant = values["has_elephant"]
        player.has_lion = values["has_lion"]
        players[color] = player
    return players


def refresh_players_from_board(
    players: dict[str, Playerstate],
    board: list[list[Resource | None]],
) -> None:
    for player in players.values():
        player.num_pieces = 0
        player.num_squirrels = 0
        player.has_elephant = False
        player.has_lion = False
    for row in board:
        for piece in row:
            if piece is None or piece.owner not in players or not piece.is_piece:
                continue
            player = players[piece.owner]
            player.num_pieces += 1
            if piece.kind == "squirrel":
                player.num_squirrels += 1
            elif piece.kind == "elephant":
                player.has_elephant = True
            elif piece.kind == "lion":
                player.has_lion = True
