
from __future__ import annotations


Position = tuple[int, int]


class Resource:
    normal_resource = ['pine']
    piece_resource = ['squirrel','lion','elephant']

    def __init__(self, owner, kind):
        self.owner = owner
        self.kind = kind
        self.is_piece = kind in self.piece_resource             # Whether this resource can be moved by actions.


class Playerstate:
    def __init__(self, color, num_pieces, num_squirrels):
        self.color = color
        self.num_pieces = num_pieces
        self.num_squirrels = num_squirrels
        self.has_elephant = False
        self.has_lion = False

    def refresh_side(self, board: Chessboard):
        """Refresh this player's piece counts and special-piece flags."""
        self.num_pieces = 0
        self.num_squirrels = 0
        self.has_elephant = False
        self.has_lion = False
        for row in board.board_matrix:
            for cell in row:
                if cell is None:
                    continue
                elif cell.owner == self.color and cell.is_piece:
                    self.num_pieces += 1
                else:
                    continue
                if cell.kind == 'squirrel':
                    self.num_squirrels += 1
                elif cell.kind == 'lion':
                    self.has_lion = True
                elif cell.kind == 'elephant':
                    self.has_elephant = True



class Chessboard:
    def __init__(self, gamemode,board):
        self.gamemode = gamemode               # int 1, 2, 3
        self.board_matrix = board               # logic 9*7
        # Keep 7 logical rows for future modes where special pieces may expand
        # the lower-left and upper-right board areas into rows 0 and 6.
        self.current_player = 'black'
        self.time_token_owner = None                # if not load special save, need set
        self.current_ap = 1
        self.max_ap = 1
        self.new_piece = []

    def spend_ap(self, cost) -> bool:
        """Spend AP if the current player has enough."""
        if self.current_ap >= cost:
            self.current_ap -= cost
            return True
        return False

    def get_piece(self, log_pos) -> Resource | None:
        log_x, log_y = log_pos
        return self.board_matrix[log_y][log_x]

    def set_piece(self, log_pos, piece):
        """Set a board cell directly."""
        log_x, log_y = log_pos
        self.board_matrix[log_y][log_x] = piece

    def place_piece(self, log_pos, piece) -> Resource:
        """Place a piece and return the piece previously on that cell."""
        log_x, log_y = log_pos
        old_piece = self.board_matrix[log_y][log_x]
        self.board_matrix[log_y][log_x] = piece
        return old_piece

    def is_inside_board(self, log_pos) -> bool:
        log_x, log_y = log_pos
        return (
            0 <= log_y < len(self.board_matrix)
            and 0 <= log_x < len(self.board_matrix[log_y])
        )

    def move_new_piece_mark(self, old_pos, new_pos):
        """Move the new-piece marker when a newly placed piece is pushed or moved."""
        for index, pos in enumerate(self.new_piece):
            if tuple(pos) == tuple(old_pos):
                self.new_piece[index] = new_pos
                return

    def elephant_push(self, ele_pos, dir_pos) -> bool:
        """Move an elephant one step and push pieces in that direction into an empty cell."""
        ele_x, ele_y = ele_pos
        dir_x, dir_y = dir_pos
        dx, dy = dir_x - ele_x, dir_y - ele_y
        if dx == 0 and dy == 0:
            return False
        if abs(dx) + abs(dy) != 1:
            return False

        def next_pos(log_pos):
            log_x, log_y = log_pos
            return log_x + dx, log_y + dy

        def can_push_in(log_pos):
            return is_player_accessible(self, log_pos)

        def push_piece(from_pos) -> bool:
            to_pos = next_pos(from_pos)
            if not can_push_in(from_pos) or not can_push_in(to_pos):
                return False

            moving_piece = self.get_piece(from_pos)
            if moving_piece is None:
                return True

            if self.get_piece(to_pos) is not None and not push_piece(to_pos):
                return False

            self.place_piece(to_pos, moving_piece)
            self.place_piece(from_pos, None)
            self.move_new_piece_mark(from_pos, to_pos)
            return True

        elephant = self.get_piece(ele_pos)
        if elephant is None or elephant.kind != 'elephant':
            return False
        return push_piece(ele_pos)


    def remove_piece(self, log_pos):
        log_x, log_y = log_pos
        self.board_matrix[log_y][log_x] = None

    def mark_new_piece(self, log_pos):
        self.new_piece.append(log_pos)

    def is_new_piece(self, log_pos) -> bool:
        return any(tuple(pos) == tuple(log_pos) for pos in self.new_piece)

    def new_turn(self):
        """Switch players, increase max AP, refill AP, and clear new-piece markers."""
        if self.current_player == 'black':
            self.current_player = 'white'
        elif self.current_player == 'white':
            self.current_player = 'black'
        self.max_ap += 1
        self.current_ap = self.max_ap
        self.new_piece = []

    def set_time_token_owner(self, new_owner):
        self.time_token_owner = new_owner

class Interaction:
    def __init__(self):
        self.selected_pos = None
        self.selected_resource = None
        self.selected_cost = []
        self.trading_kind = None
        self.trading_piece = None
        self.remaining_trading_count = 0
        self.tooltip_text = ""
        self.dialog_text = ""
        self.after_elephant_push = False
        self.elephant_start_position = None

    def reset(self):
        self.selected_pos = None
        self.selected_resource = None
        self.selected_cost = []
        self.trading_kind = None
        self.trading_piece = None
        self.remaining_trading_count = 0
        self.tooltip_text = ""
        self.dialog_text = ""
        self.after_elephant_push = False
        self.elephant_start_position = None

    trade_dict = {
        'buy':{
            'lion':4,
            'elephant':2
        },
        'sell':{
            'lion':2,
            'elephant':1
        }
    }
    def start_trade(self, kind, piece):
        self.trading_kind = kind
        self.trading_piece = piece
        self.remaining_trading_count = Interaction.trade_dict[kind][piece.kind]

    def finish_trade(self):
        if self.remaining_trading_count == 0:
            self.trading_kind = None
            self.trading_piece = None
            return True
        return False

    def set_dialog_text(self, text):
        self.dialog_text = text

    def reset_dialog_text(self):
        self.dialog_text = ""

    def add_line_tooltip_text(self, text):
        self.tooltip_text += '- ' + text + '\n'

    def reset_tooltip_text(self, text):
        self.tooltip_text = text


def pos_logic_to_display(log_pos) -> (int, int):
    x, y = log_pos
    dis_x = x - 1
    dis_pos = (dis_x, y)
    return dis_pos


def is_player_accessible(board: Chessboard, log_pos: Position) -> bool:
    """Return whether a logical cell is inside the player-action area."""
    x, y = log_pos
    if board.gamemode == 1:
        return 2 <= x <= 6 and 1 <= y <= 5
    return board.is_inside_board(log_pos)


def is_empty_accessible_cell(board: Chessboard, log_pos: Position) -> bool:
    return is_player_accessible(board, log_pos) and board.get_piece(log_pos) is None


def get_empty_accessible_cells(board: Chessboard) -> set[Position]:
    cells: set[Position] = set()
    for y, row in enumerate(board.board_matrix):
        for x, piece in enumerate(row):
            pos = (x, y)
            if piece is None and is_player_accessible(board, pos):
                cells.add(pos)
    return cells


def get_legal_moves_for_piece(
    board: Chessboard,
    players: dict[str, Playerstate],
    log_pos: Position,
) -> set[Position]:
    piece = board.get_piece(log_pos) if board.is_inside_board(log_pos) else None
    if piece is None or not piece.is_piece:
        return set()

    current_player = board.current_player
    playerstate = players.get(current_player)
    if playerstate is None:
        return set()

    own_piece = piece.owner == current_player
    lion_control = (
        not own_piece
        and piece.kind in {"squirrel", "elephant"}
        and playerstate.has_lion
        and board.current_ap >= 2
    )
    if not own_piece and not lion_control:
        return set()
    if own_piece and board.current_ap < 1:
        return set()
    if own_piece and board.is_new_piece(log_pos):
        return set()
    if not is_player_accessible(board, log_pos):
        return set()

    targets: set[Position] = set()
    for target in orthogonal_neighbors(log_pos):
        if not is_player_accessible(board, target):
            continue
        if piece.kind == "elephant":
            if elephant_has_open_line(board, log_pos, target):
                targets.add(target)
        elif piece.kind in {"squirrel", "lion"} and is_empty_accessible_cell(board, target):
            targets.add(target)
    return targets


def can_select_piece(
    board: Chessboard,
    players: dict[str, Playerstate],
    log_pos: Position,
) -> bool:
    return bool(get_legal_moves_for_piece(board, players, log_pos))


def can_select_resource(
    board: Chessboard,
    players: dict[str, Playerstate],
    interaction: Interaction,
    resource_id: str,
) -> bool:
    playerstate = players.get(board.current_player)
    if playerstate is None:
        return False

    empty_cells = get_empty_accessible_cells(board)
    selected_pos = interaction.selected_pos
    selected_piece = board.get_piece(selected_pos) if selected_pos and board.is_inside_board(selected_pos) else None

    if resource_id == "squirrel":
        return board.current_ap >= 3 and bool(empty_cells)

    if resource_id == "elephant":
        if (
            selected_piece is not None
            and selected_piece.owner == board.current_player
            and selected_piece.kind == "elephant"
        ):
            return board.current_ap >= 1
        return (
            board.time_token_owner == board.current_player
            and playerstate.num_squirrels >= 2
            and board.current_ap >= 1
        )

    if resource_id == "lion":
        if (
            selected_piece is not None
            and selected_piece.owner == board.current_player
            and selected_piece.kind == "lion"
        ):
            return board.current_ap >= 1 and len(empty_cells) >= 1
        return (
            board.time_token_owner == board.current_player
            and playerstate.num_squirrels >= 4
            and board.current_ap >= 1
        )

    if resource_id == "time_token":
        return (
            board.time_token_owner in {"black", "white"}
            and board.current_ap >= 1
            and not any(player.num_pieces > 7 for player in players.values())
        )

    return False


def get_legal_board_targets(
    board: Chessboard,
    players: dict[str, Playerstate],
    interaction: Interaction,
) -> set[Position]:
    if interaction.selected_pos is not None:
        return get_legal_moves_for_piece(board, players, interaction.selected_pos)
    if interaction.selected_resource in {"squirrel", "elephant", "lion"}:
        return get_empty_accessible_cells(board)

    targets: set[Position] = set()
    for y, row in enumerate(board.board_matrix):
        for x, piece in enumerate(row):
            if piece is not None and can_select_piece(board, players, (x, y)):
                targets.add((x, y))
    return targets


def get_legal_resource_targets(
    board: Chessboard,
    players: dict[str, Playerstate],
    interaction: Interaction,
) -> set[str]:
    return {
        resource_id
        for resource_id in ("squirrel", "elephant", "lion", "time_token")
        if can_select_resource(board, players, interaction, resource_id)
    }


def get_legal_camp_targets(
    board: Chessboard,
    players: dict[str, Playerstate],
    interaction: Interaction,
) -> set[str]:
    if board.time_token_owner not in {"black", "white"}:
        return set()
    if board.current_ap < 1:
        return set()
    if interaction.selected_pos is not None or interaction.selected_resource is not None:
        return set()
    return {board.current_player}


def orthogonal_neighbors(log_pos: Position) -> tuple[Position, Position, Position, Position]:
    x, y = log_pos
    return (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)


def elephant_has_open_line(board: Chessboard, start: Position, first_target: Position) -> bool:
    dx = first_target[0] - start[0]
    dy = first_target[1] - start[1]
    if abs(dx) + abs(dy) != 1:
        return False

    current = first_target
    while is_player_accessible(board, current):
        if board.get_piece(current) is None:
            return True
        current = (current[0] + dx, current[1] + dy)
    return False




















if __name__ == '__main__':
    pass
