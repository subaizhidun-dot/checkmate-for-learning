"""G3 setup and records, independent of the other rounds."""
from dataclasses import dataclass, field
from basicgame import Resource

START_PATTERN = {(0, 0), (0, 1), (1, 1), (2, 1), (0, 2), (1, 2), (2, 2)}
END_PATTERN = {(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1), (2, 2)}


def create_g3_board():
    board = [[None for _ in range(9)] for _ in range(7)]
    for owner, x in (("black", 2), ("white", 6)):
        for y in (1, 5):
            board[y][x] = Resource(owner, "squirrel")
        board[3][x] = Resource(owner, "elephant")
    return board


def own_positions(board):
    return {(x, y) for y, row in enumerate(board.board_matrix) for x, piece in enumerate(row)
            if piece is not None and piece.is_piece and piece.owner == board.current_player}


def normalized(points):
    if not points:
        return frozenset()
    left = min(x for x, y in points)
    top = min(y for x, y in points)
    return frozenset((x - left, y - top) for x, y in points)


def matches_pattern(points, pattern):
    candidate = normalized(points)
    rotated = set(pattern)
    for _ in range(4):
        if candidate == normalized(rotated):
            return True
        rotated = {(-y, x) for x, y in rotated}
    return False


@dataclass
class G3Turn:
    start_positions: set = field(default_factory=set)
    move_count: int = 0
    resources_changed: bool = False
    extra_turn: bool = False
    pending_extra_turn: bool = False

    @classmethod
    def capture(cls, board, extra_turn=False):
        return cls(start_positions=own_positions(board), extra_turn=extra_turn)

    def to_dict(self):
        return {"start_positions": [list(pos) for pos in sorted(self.start_positions)],
                "move_count": self.move_count, "resources_changed": self.resources_changed,
                "extra_turn": self.extra_turn, "pending_extra_turn": self.pending_extra_turn}

    @classmethod
    def from_dict(cls, data):
        return cls(start_positions={tuple(pos) for pos in data.get("start_positions", [])},
                   move_count=int(data.get("move_count", 0)),
                   resources_changed=bool(data.get("resources_changed", False)),
                   extra_turn=bool(data.get("extra_turn", False)),
                   pending_extra_turn=bool(data.get("pending_extra_turn", False)))


def can_start_extra_turn(board, position):
    if board.gamemode != 3 or board.current_ap < 1 or board.g3_turn.extra_turn:
        return False
    piece = board.get_piece(position) if board.is_inside_board(position) else None
    return (piece is not None and piece.kind == "butterfly" and piece.owner == board.current_player
            and not board.is_new_piece(position) and 2 <= position[0] <= 6 and 1 <= position[1] <= 5)


def get_g3_condition_results(playerstate, board):
    turn = board.g3_turn
    start_matches = matches_pattern(turn.start_positions, START_PATTERN)
    end_positions = own_positions(board)
    # A qualifying start anchors the ending orientation; translation remains free.
    end_matches = (normalized(end_positions) == normalized({(-x, -y) for x, y in turn.start_positions})
                   if start_matches else matches_pattern(end_positions, END_PATTERN))
    return [board.time_token_owner in {"black", "white"} and board.time_token_owner != board.current_player,
            turn.move_count == 1, not turn.resources_changed,
            start_matches, end_matches]
