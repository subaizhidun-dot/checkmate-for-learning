"""G2 setup and tree actions on the central 5 by 5 board."""

from basicgame import Resource
from game1 import check_g1_condition0


TREE_STARTS = {"black": (2, 3), "white": (6, 3)}
PLANTING_CORNERS = {(2, 3): "lower_left", (6, 3): "upper_right"}
SHIFTED_CORNERS = {"lower_left": ((2, 5), (2, 6)), "upper_right": ((6, 1), (6, 0))}
G2_CORNER_LAYOUT = 3


def create_g2_board():
    board = [[None for _ in range(9)] for _ in range(7)]
    for owner, x in (("black", 2), ("white", 6)):
        for y in (1, 5):
            board[y][x] = Resource(owner, "squirrel")
        board[3][x] = Resource(owner, "tree", rooted=True)
    return board


def is_g2_player_access(position, board=None):
    x, y = position
    if board is not None:
        for corner in board.expanded_corners:
            original, extended = SHIFTED_CORNERS[corner]
            if position == original:
                return False
            if position == extended:
                return True
    return 2 <= x <= 6 and 1 <= y <= 5


def shift_corner(board, planting_position, expand):
    """Carry the corner's occupant and its new-piece marker with the tile."""
    corner = PLANTING_CORNERS[planting_position]
    if expand == (corner in board.expanded_corners):
        return
    original, extended = SHIFTED_CORNERS[corner]
    start, end = (original, extended) if expand else (extended, original)
    board.set_piece(end, board.get_piece(start))
    board.set_piece(start, None)
    board.move_new_piece_mark(start, end)
    if expand:
        board.expanded_corners.add(corner)
    else:
        board.expanded_corners.discard(corner)


def get_g2_condition_results(playerstate, board):
    trees = {}
    for y, row in enumerate(board.board_matrix):
        for x, piece in enumerate(row):
            if piece is not None and piece.kind == "tree":
                trees[piece.owner] = (x, y)
    if "black" not in trees or "white" not in trees:
        return [check_g1_condition0(board)] + [False] * 6
    bx, by = trees["black"]
    wx, wy = trees["white"]
    distance = abs(bx - wx) + abs(by - wy)
    return [check_g1_condition0(board)] + [distance <= limit for limit in range(6, 0, -1)]


def tree_action_cost(board, position):
    tree = board.get_piece(position)
    return 1 if tree.owner == board.current_player else 2


def tree_action(board, position):
    """Return a legal uproot/plant action for the current player."""
    if board.gamemode != 2 or board.current_ap < 1 or not board.is_inside_board(position):
        return None
    tree = board.get_piece(position)
    if tree is None or tree.kind != "tree" or position not in PLANTING_CORNERS:
        return None
    own_kinds = {
        piece.kind
        for row in board.board_matrix for piece in row
        if piece is not None and piece.owner == board.current_player
    }
    if (not (tree.owner == board.current_player or "lion" in own_kinds)
            or board.current_ap < tree_action_cost(board, position)):
        return None
    if tree.rooted:
        return "uproot_tree"
    if (position in board.tree_markers and is_g2_player_access(position, board)
            and "mole" in own_kinds):
        return "plant_tree"
    return None
