from basicgame import Resource,Playerstate
from fractions import Fraction

def create_g1_board():
    board = []
    for y in range(0,7):
        row = []
        for x in range(0,9):
            row.append(None)
        board.append(row)
    black_pine = Resource('black','pine')
    white_pine = Resource('white','pine')
    black_squirrel = Resource('black','squirrel')
    white_squirrel = Resource('white','squirrel')
    board[3][0] = black_pine
    board[3][8] = white_pine
    board[1][2] = board[3][2] = board[5][2] = black_squirrel
    board[1][6] = board[3][6] = board[5][6] = white_squirrel
    return board

def is_g1_player_access(log_pos) -> bool:
    x,y = log_pos
    if 2 <= x <= 6 and 1 <= y <= 5:
        return True
    return False


'''Victory condition checks.'''
def check_g1_condition0(board) -> bool:
    '''The time token is held by the opponent.'''
    if board.current_player == 'black' and board.time_token_owner == 'white':
        return True
    if board.current_player == 'white' and board.time_token_owner == 'black':
        return True
    return False

def check_g1_condition1(board) -> bool:
    '''No two squirrels share a row or column.'''
    for y in range (1,6):
        for x in range (2,7):
            cell1 = board.board_matrix[y][x]
            if cell1 is None:
                continue
            elif cell1.kind == 'squirrel' and cell1.owner == board.current_player:
                for i in range (1,6):
                    if i == y:
                        continue
                    cell2 = board.board_matrix[i][x]
                    if cell2 is not None and cell2.kind == 'squirrel' and cell2.owner == board.current_player:
                        return False
                for j in range (2,7):
                    if j == x:
                        continue
                    cell2 = board.board_matrix[y][j]
                    if cell2 is not None and cell2.kind == 'squirrel' and cell2.owner == board.current_player:
                        return False
    return True

def check_g1_condition2(board) -> bool:
    '''No two squirrels share a diagonal.'''
    for y in range (1,6):
        for x in range (2,7):
            cell1 = board.board_matrix[y][x]
            if cell1 is None:
                continue
            elif cell1.kind == 'squirrel' and cell1.owner == board.current_player:
                x1, y1 = x + 1, y + 1
                while is_g1_player_access((x1, y1)) == True:
                    cell2 = board.board_matrix[y1][x1]
                    if cell2 is not None and cell2.kind == 'squirrel' and cell2.owner == board.current_player:
                        return False
                    x1, y1 = x1 + 1, y1 + 1

                x1, y1 = x - 1, y - 1
                while is_g1_player_access((x1, y1)) == True:
                    cell2 = board.board_matrix[y1][x1]
                    if cell2 is not None and cell2.kind == 'squirrel' and cell2.owner == board.current_player:
                        return False
                    x1, y1 = x1 - 1, y1 - 1

                x1, y1 = x - 1, y + 1
                while is_g1_player_access((x1, y1)) == True:
                    cell2 = board.board_matrix[y1][x1]
                    if cell2 is not None and cell2.kind == 'squirrel' and cell2.owner == board.current_player:
                        return False
                    x1, y1 = x1 - 1, y1 + 1

                x1, y1 = x + 1, y - 1
                while is_g1_player_access((x1, y1)) == True:
                    cell2 = board.board_matrix[y1][x1]
                    if cell2 is not None and cell2.kind == 'squirrel' and cell2.owner == board.current_player:
                        return False
                    x1, y1 = x1 + 1, y1 - 1
    return True

def check_g1_condition3(playerstate:Playerstate) -> bool:
    '''The player has at least five squirrels.'''
    if playerstate.num_squirrels >= 5:
        return True
    return False

def check_g1_condition4(board) -> bool:
    '''The player's squirrels and pine form axial symmetry.'''
    pine_list = []
    for y in range (0,7):
        for x in range (0,9):
            cell1 = board.board_matrix[y][x]
            if cell1 is not None and (cell1.kind == 'squirrel' or cell1.kind == 'pine') and cell1.owner == board.current_player:
                pine_list.append([x, y])

    def is_symmetric(points, axis):
        a, b, c = axis
        denominator = a * a + b * b
        point_set = {(x, y) for x, y in points}

        for x, y in point_set:
            distance = a * x + b * y + c
            reflected_x = Fraction(x * denominator - 2 * a * distance, denominator)
            reflected_y = Fraction(y * denominator - 2 * b * distance, denominator)

            if reflected_x.denominator != 1 or reflected_y.denominator != 1:
                return False
            if (reflected_x.numerator, reflected_y.numerator) not in point_set:
                return False
        return True

    for i in range(len(pine_list)):
        x1, y1 = pine_list[i]
        for j in range(i + 1, len(pine_list)):
            x2, y2 = pine_list[j]
            dx = x2 - x1
            dy = y2 - y1

            through_axis = (dy, -dx, dx * y1 - dy * x1)
            bisector_axis = (
                2 * dx,
                2 * dy,
                -dx * (x1 + x2) - dy * (y1 + y2)
            )

            if is_symmetric(pine_list, through_axis) or is_symmetric(pine_list, bisector_axis):
                return True

    return False

def check_g1_win_by_check(playerstate,board) -> bool:
    '''Check victory succeeds only when every G1 condition is met.'''
    return all(get_g1_condition_results(playerstate, board))


def get_g1_condition_results(playerstate, board) -> list[bool]:
    '''Return the five G1 check-victory condition results.'''
    return [
        check_g1_condition0(board),
        check_g1_condition1(board),
        check_g1_condition2(board),
        check_g1_condition3(playerstate),
        check_g1_condition4(board),
    ]


if __name__ == '__main__':
    pass
