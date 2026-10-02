from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pygame

import ui_text
from basicgame import (
    Chessboard,
    Interaction,
    Playerstate,
    Resource,
    can_select_piece,
    can_select_resource,
    get_empty_accessible_cells,
    get_legal_board_targets,
    get_legal_camp_targets,
    get_legal_moves_for_piece,
    get_legal_resource_targets,
    is_empty_accessible_cell,
)
from game1 import create_g1_board, get_g1_condition_results
from gui import CheckMateGui, FPS
from sl_func import SAVES_DIR, list_save_slots, load_game, save_game


Position = tuple[int, int]
CONDITION_REVEAL_MS = 500

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

ACTION_MOVE = "move"
ACTION_BUY_ELEPHANT = "buy_elephant"
ACTION_BUY_LION = "buy_lion"
ACTION_PLACE_SQUIRREL = "place_squirrel"
ACTION_SELL_PIECE = "sell_piece"
ACTION_MOVE_TIME_TOKEN = "move_time_token"

BUY_COST = {"elephant": 2, "lion": 4}
SELL_REFUND = {"elephant": 1, "lion": 2}


@dataclass
class AppState:
    interaction: Interaction = field(default_factory=Interaction)
    game: Chessboard | None = None
    players: dict[str, Playerstate] = field(default_factory=dict)
    condition_results: list[bool] | None = None
    condition_reveal_count: int = 0
    mate_loser: str | None = None

    @property
    def board_matrix(self) -> list[list[Any]]:
        return self.game.board_matrix if self.game is not None else []

    @property
    def current_player(self) -> str | None:
        return self.game.current_player if self.game is not None else None

    @property
    def time_token_owner(self) -> str | None:
        return self.game.time_token_owner if self.game is not None else None

    @property
    def current_ap(self) -> int | str:
        return self.game.current_ap if self.game is not None else "-"

    @property
    def max_ap(self) -> int | str:
        return self.game.max_ap if self.game is not None else "-"

    def bind_runtime_links(self) -> None:
        if self.game is not None:
            self.game.interaction = self.interaction
            self.game.players = self.players


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
    checking_started_at: int = 0
    temp_removed: list[tuple[Position, Resource]] = field(default_factory=list)
    temp_placed: list[Position] = field(default_factory=list)


def make_players_from_board(board: list[list[Any]]) -> dict[str, Playerstate]:
    counts = {
        "black": {"pieces": 0, "squirrels": 0, "has_elephant": False, "has_lion": False},
        "white": {"pieces": 0, "squirrels": 0, "has_elephant": False, "has_lion": False},
    }
    for row in board:
        for piece in row:
            if piece is None or piece.owner not in counts or not piece.is_piece:
                continue
            counts[piece.owner]["pieces"] += 1
            if piece.kind == "squirrel":
                counts[piece.owner]["squirrels"] += 1
            elif piece.kind == "elephant":
                counts[piece.owner]["has_elephant"] = True
            elif piece.kind == "lion":
                counts[piece.owner]["has_lion"] = True

    players: dict[str, Playerstate] = {}
    for color, values in counts.items():
        player = Playerstate(color, values["pieces"], values["squirrels"])
        player.has_elephant = values["has_elephant"]
        player.has_lion = values["has_lion"]
        players[color] = player
    return players


def autosave_if_needed(state: AppState, flow: GameFlow | None = None) -> None:
    if state.game is not None:
        save_game(state.game, state.players, flow=flow, interaction=state.interaction, autosave=True)


class CheckMateApp:
    def __init__(self) -> None:
        self.state = AppState()
        self.state.interaction.set_dialog_text(ui_text.STARTUP_DIALOG)
        self.flow = GameFlow()
        self.gui = CheckMateGui()

    def run(self) -> None:
        running = True
        while running:
            save_slots = list_save_slots()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    autosave_if_needed(self.state, self.flow)
                    running = False
                    break
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    autosave_if_needed(self.state, self.flow)
                    running = False
                    break
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
                    self.cancel_current_action()
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self.handle_click(event.pos)

            self.update_victory_check()
            self.gui.save_slots = save_slots
            self.gui.build_click_regions()
            self.update_tooltip(self.gui.find_region_name(pygame.mouse.get_pos()))
            board_targets, resource_targets, camp_targets = self.get_legal_targets()
            self.gui.draw(
                self.state,
                legal_board_targets=board_targets,
                legal_resource_targets=resource_targets,
                legal_camp_targets=camp_targets,
                tooltip_text=self.state.interaction.tooltip_text,
                save_slots=save_slots,
            )
            pygame.display.flip()
            self.gui.clock.tick(FPS)

        pygame.quit()

    def handle_click(self, pos: Position) -> None:
        name = self.gui.find_region_name(pos)
        if name is None:
            self.gui.open_menu = None
            self.state.interaction.set_dialog_text(ui_text.NO_BUTTON_DIALOG)
            return

        if name.startswith("menu:"):
            self.handle_menu_click(name)
        elif self.flow.phase == PHASE_CHECKING_WIN:
            self.state.interaction.set_dialog_text(ui_text.CHECKING_WIN_DIALOG)
        elif name.startswith("camp:"):
            self.handle_camp_click(name.removeprefix("camp:"))
        elif name.startswith("board:"):
            logical_x, logical_y = (int(value) for value in name.removeprefix("board:").split(","))
            self.handle_board_click((logical_x, logical_y))
        elif name.startswith("resource:"):
            self.handle_resource_click(name.removeprefix("resource:"))
        elif name.startswith("condition:"):
            self.state.interaction.set_dialog_text(ui_text.CONDITION_PANEL_DIALOG)

    def handle_menu_click(self, name: str) -> None:
        if name in {"menu:start", "menu:save", "menu:load"}:
            menu_name = name.removeprefix("menu:")
            self.gui.open_menu = None if self.gui.open_menu == menu_name else menu_name
            messages = {
                "start": ui_text.START_MENU_DIALOG,
                "save": ui_text.SAVE_MENU_DIALOG,
                "load": ui_text.LOAD_MENU_DIALOG,
            }
            self.state.interaction.set_dialog_text(messages[menu_name])
            return

        self.gui.open_menu = None
        if name == "menu:start_g1":
            self.start_g1()
            return
        if name.startswith("menu:save_slot_"):
            self.save_slot(int(name.removeprefix("menu:save_slot_")))
            return
        if name.startswith("menu:load:"):
            self.load_from_file(name.removeprefix("menu:load:"))
            return
        if name.startswith("menu:disabled"):
            self.state.interaction.set_dialog_text(ui_text.NO_SAVE_FILE_DIALOG)

    def start_g1(self) -> None:
        board = create_g1_board()
        self.state.game = Chessboard(1, board)
        self.state.players = make_players_from_board(board)
        self.state.interaction = Interaction()
        self.state.bind_runtime_links()
        self.refresh_players()
        self.flow = GameFlow(phase=PHASE_CHOOSE_TOKEN)
        self.state.condition_results = None
        self.state.condition_reveal_count = 0
        self.state.mate_loser = None
        self.state.interaction.set_dialog_text(ui_text.G1_STARTED_DIALOG)

    def save_slot(self, slot: int) -> Path | None:
        if self.state.game is None:
            self.state.interaction.set_dialog_text(ui_text.NO_GAME_TO_SAVE_DIALOG)
            return None
        path = save_game(
            self.state.game,
            self.state.players,
            flow=self.flow,
            interaction=self.state.interaction,
            slot=slot,
        )
        self.state.interaction.set_dialog_text(ui_text.saved(path.name))
        return path

    def load_from_file(self, filename: str) -> None:
        path = SAVES_DIR / filename
        try:
            loaded = load_game(path)
        except FileNotFoundError:
            self.state.interaction.set_dialog_text(ui_text.NO_SAVE_FILE_DIALOG)
            return
        except ValueError as exc:
            self.state.interaction.set_dialog_text(ui_text.load_failed(exc))
            return

        self.state.game = loaded["game"]
        self.state.players = loaded["players"]
        self.state.mate_loser = getattr(self.state.game, "mate_loser", None)
        self.state.interaction = Interaction()
        self.restore_interaction(loaded.get("interaction", {}))
        self.state.bind_runtime_links()
        self.refresh_players()
        self.restore_flow(loaded.get("flow", {}))
        if not self.state.interaction.dialog_text:
            self.state.interaction.set_dialog_text(ui_text.loaded(path.name))

    def restore_flow(self, data: dict[str, Any]) -> None:
        if not data:
            self.after_load_phase_refresh()
            return
        self.flow = GameFlow(
            phase=data.get("phase") or PHASE_PLAYING,
            pending_action=data.get("pending_action"),
            pending_piece_limit_owner=data.get("pending_piece_limit_owner"),
            pending_refund_owner=data.get("pending_refund_owner"),
            pending_refund_count=int(data.get("pending_refund_count") or 0),
            pending_refund_kind=data.get("pending_refund_kind"),
            last_condition_results=data.get("last_condition_results"),
            time_wish_winner=data.get("time_wish_winner"),
            checking_action_message=data.get("checking_action_message", ""),
            checking_started_at=int(data.get("checking_started_at") or pygame.time.get_ticks()),
            temp_removed=[
                (
                    tuple(item["position"]),
                    Resource(item["piece"]["owner"], item["piece"]["kind"]),
                )
                for item in data.get("temp_removed", [])
                if item.get("position") is not None and item.get("piece") is not None
            ],
            temp_placed=[tuple(pos) for pos in data.get("temp_placed", [])],
        )
        self.state.condition_results = self.flow.last_condition_results
        self.state.condition_reveal_count = (
            0 if self.flow.phase == PHASE_CHECKING_WIN else len(self.flow.last_condition_results or [])
        )
        if self.flow.phase == PHASE_CHECKING_WIN:
            self.flow.checking_started_at = pygame.time.get_ticks()
            self.state.interaction.set_dialog_text(ui_text.CHECKING_WIN_DIALOG)
        if self.flow.phase != PHASE_GAME_OVER:
            self.state.mate_loser = None

    def restore_interaction(self, data: dict[str, Any]) -> None:
        interaction = self.state.interaction
        selected_pos = data.get("selected_pos")
        elephant_start = data.get("elephant_start_position")
        trading_piece = data.get("trading_piece")
        interaction.selected_pos = tuple(selected_pos) if selected_pos is not None else None
        interaction.selected_resource = data.get("selected_resource")
        interaction.selected_cost = [tuple(pos) for pos in data.get("selected_cost", [])]
        interaction.trading_kind = data.get("trading_kind")
        interaction.trading_piece = (
            Resource(trading_piece["owner"], trading_piece["kind"]) if trading_piece else None
        )
        interaction.remaining_trading_count = int(data.get("remaining_trading_count") or 0)
        interaction.dialog_text = data.get("dialog_text", "")
        interaction.after_elephant_push = bool(data.get("after_elephant_push", False))
        interaction.elephant_start_position = tuple(elephant_start) if elephant_start is not None else None

    def after_load_phase_refresh(self) -> None:
        game = self.state.game
        if game is None:
            self.flow = GameFlow(phase=PHASE_MENU)
        elif game.time_token_owner is None:
            self.flow = GameFlow(phase=PHASE_CHOOSE_TOKEN)
            self.state.interaction.set_dialog_text(ui_text.LOADED_CHOOSE_TOKEN_DIALOG)
        else:
            self.flow = GameFlow(phase=PHASE_PLAYING)

    def handle_camp_click(self, side: str) -> None:
        game = self.state.game
        if game is None:
            self.state.interaction.set_dialog_text(ui_text.START_OR_LOAD_DIALOG)
            return

        if self.flow.phase == PHASE_CHOOSE_TOKEN:
            game.set_time_token_owner(side)
            self.flow.phase = PHASE_PLAYING
            self.flow.pending_action = None
            self.state.interaction.set_dialog_text(ui_text.token_assigned(side, game.current_player))
            self.check_mate_if_stuck()
            return

        if self.flow.phase == PHASE_TIME_WISH:
            self.resolve_time_wish(side)
            return

        if self.flow.phase == PHASE_GAME_OVER:
            self.state.interaction.set_dialog_text(ui_text.GAME_OVER_DIALOG)
            return

        if side != game.current_player:
            self.state.interaction.set_dialog_text(ui_text.CURRENT_PANEL_ONLY_DIALOG)
            return
        if game.time_token_owner not in {"black", "white"}:
            self.state.interaction.set_dialog_text(ui_text.ASSIGN_TOKEN_FIRST_DIALOG)
            return
        if game.current_ap < 1:
            self.state.interaction.set_dialog_text(ui_text.NOT_ENOUGH_AP_TOKEN_DIALOG)
            return

        self.flow.pending_action = ACTION_MOVE_TIME_TOKEN
        game.set_time_token_owner(other_player(game.time_token_owner))
        game.spend_ap(1)
        self.enter_piece_limit_or_finish(ui_text.TOKEN_MOVED_ONCE_DIALOG)

    def resolve_time_wish(self, side: str) -> None:
        game = self.state.game
        if game is None:
            return
        if side != game.time_token_owner:
            self.state.interaction.set_dialog_text(ui_text.TIME_WISH_DIALOG)
            return
        game.set_time_token_owner(None)
        game.current_ap = 0
        self.flow.phase = PHASE_GAME_OVER
        self.flow.pending_action = None
        self.state.interaction.set_dialog_text(ui_text.TIME_WISH_RESOLVED_DIALOG)
        path = self.save_time_wish_g1_start()
        self.state.interaction.set_dialog_text(ui_text.time_wish_saved(path.name))

    def save_time_wish_g1_start(self) -> Path:
        board = create_g1_board()
        game = Chessboard(1, board)
        game.current_player = "black"
        game.time_token_owner = None
        game.current_ap = 1
        game.max_ap = 1
        game.new_piece = []
        players = make_players_from_board(board)
        for player in players.values():
            player.refresh_side(game)
        flow = GameFlow(phase=PHASE_PLAYING)
        interaction = Interaction()
        interaction.set_dialog_text(ui_text.STARTUP_DIALOG)
        return save_game(game, players, flow=flow, interaction=interaction, autosave=True)

    def handle_resource_click(self, resource_id: str) -> None:
        game = self.state.game
        if game is None:
            self.state.interaction.set_dialog_text(ui_text.START_OR_LOAD_DIALOG)
            return
        if self.flow.phase in {PHASE_CHOOSE_TOKEN, PHASE_GAME_OVER, PHASE_TIME_WISH}:
            self.state.interaction.set_dialog_text(ui_text.ACTION_UNAVAILABLE_DIALOG)
            return
        if resource_id == "time_token":
            self.handle_time_token_button()
            return
        if not can_select_resource(game, self.state.players, self.state.interaction, resource_id):
            self.state.interaction.set_dialog_text(ui_text.RESOURCE_ILLEGAL_DIALOG)
            return

        selected = self.state.interaction.selected_pos
        selected_piece = self.get_piece(selected) if selected is not None else None
        if selected_piece is not None and selected_piece.owner == game.current_player and selected_piece.kind == resource_id:
            self.start_sell_special(selected, selected_piece)
            return

        if resource_id == "squirrel":
            self.clear_selection(keep_dialog=True)
            self.state.interaction.selected_resource = resource_id
            self.flow.phase = PHASE_SELECT_RESOURCE
            self.flow.pending_action = ACTION_PLACE_SQUIRREL
            self.state.interaction.set_dialog_text(ui_text.PLACE_SQUIRREL_DIALOG)
            return

        self.start_buy_special(resource_id)

    def start_buy_special(self, kind: str) -> None:
        self.clear_selection(keep_dialog=True)
        self.state.interaction.selected_resource = kind
        self.state.interaction.selected_cost = []
        self.flow.phase = PHASE_SELECT_COST
        self.flow.pending_action = ACTION_BUY_ELEPHANT if kind == "elephant" else ACTION_BUY_LION
        self.state.interaction.set_dialog_text(ui_text.choose_cost(kind, 0, BUY_COST[kind]))

    def start_sell_special(self, position: Position, piece: Resource) -> None:
        game = self.state.game
        if game is None:
            return
        self.temporarily_remove_piece(position)
        self.refresh_players()
        self.clear_selection(keep_dialog=True)
        self.flow.pending_action = ACTION_SELL_PIECE
        self.flow.pending_refund_owner = game.current_player
        self.flow.pending_refund_count = SELL_REFUND[piece.kind]
        self.flow.pending_refund_kind = piece.kind
        self.flow.phase = PHASE_PENDING_REFUND
        self.state.interaction.set_dialog_text(ui_text.choose_refund(self.flow.pending_refund_count))

    def handle_time_token_button(self) -> None:
        game = self.state.game
        if game is None:
            self.state.interaction.set_dialog_text(ui_text.START_OR_LOAD_DIALOG)
            return
        if not can_select_resource(game, self.state.players, self.state.interaction, "time_token"):
            self.state.interaction.set_dialog_text(ui_text.RESOURCE_ILLEGAL_DIALOG)
            return

        spent_ap = game.current_ap
        if game.time_token_owner in {"black", "white"} and spent_ap % 2 == 1:
            game.set_time_token_owner(other_player(game.time_token_owner))
        game.spend_ap(spent_ap)
        self.finish_action(ui_text.TOKEN_FAST_MOVED_DIALOG)

    def handle_board_click(self, position: Position) -> None:
        game = self.state.game
        if game is None:
            self.state.interaction.set_dialog_text(ui_text.START_OR_LOAD_DIALOG)
            return
        if self.flow.phase == PHASE_CHOOSE_TOKEN:
            self.state.interaction.set_dialog_text(ui_text.CHOOSE_TIME_TOKEN_DIALOG)
            return
        if self.flow.phase == PHASE_TIME_WISH:
            self.state.interaction.set_dialog_text(ui_text.TIME_WISH_DIALOG)
            return
        if self.flow.phase == PHASE_GAME_OVER:
            self.state.interaction.set_dialog_text(ui_text.GAME_OVER_DIALOG)
            return
        if self.flow.phase == PHASE_PENDING_PIECE_LIMIT:
            self.handle_piece_limit_removal(position)
            return
        if self.flow.phase == PHASE_PENDING_REFUND:
            self.place_refund_squirrel(position)
            return
        if self.flow.phase == PHASE_PENDING_ELEPHANT_BONUS:
            self.place_elephant_bonus(position)
            return
        if self.flow.phase == PHASE_SELECT_COST:
            self.select_buy_cost(position)
            return
        if self.flow.pending_action in {ACTION_PLACE_SQUIRREL, ACTION_BUY_ELEPHANT, ACTION_BUY_LION}:
            self.place_selected_resource(position)
            return

        selected = self.state.interaction.selected_pos
        if selected is None:
            self.try_select_piece(position)
            return
        if position == selected:
            self.cancel_current_action()
            return
        if self.try_move_selected_piece(selected, position):
            return
        if can_select_piece(game, self.state.players, position):
            self.try_select_piece(position)
            return
        self.cancel_current_action()

    def select_buy_cost(self, position: Position) -> None:
        game = self.state.game
        kind = self.state.interaction.selected_resource
        piece = self.get_piece(position)
        if game is None or kind not in BUY_COST:
            return
        if (
            piece is None
            or piece.owner != game.current_player
            or piece.kind != "squirrel"
            or position in self.state.interaction.selected_cost
        ):
            self.state.interaction.set_dialog_text(ui_text.CHOOSE_BUY_COST_DIALOG)
            return
        self.state.interaction.selected_cost.append(position)
        required = BUY_COST[kind]
        selected = len(self.state.interaction.selected_cost)
        if selected < required:
            self.state.interaction.set_dialog_text(ui_text.choose_cost(kind, selected, required))
            return
        self.remove_selected_cost_pieces()
        self.flow.phase = PHASE_SELECT_RESOURCE
        self.state.interaction.set_dialog_text(ui_text.BUY_TARGET_DIALOG)

    def place_selected_resource(self, position: Position) -> bool:
        kind = self.state.interaction.selected_resource
        if kind == "squirrel":
            return self.place_paid_squirrel(position)
        if kind in {"elephant", "lion"}:
            return self.place_bought_special(position, kind)
        return False

    def temporarily_remove_piece(self, position: Position) -> Resource | None:
        game = self.state.game
        if game is None:
            return None
        if any(pos == position for pos, _piece in self.flow.temp_removed):
            return None
        piece = game.get_piece(position) if game.is_inside_board(position) else None
        if piece is None:
            return None
        self.flow.temp_removed.append((position, piece))
        game.remove_piece(position)
        self.remove_new_piece_mark(position)
        return piece

    def remove_selected_cost_pieces(self) -> None:
        for position in list(self.state.interaction.selected_cost):
            self.temporarily_remove_piece(position)
        self.refresh_players()

    def restore_temporary_transaction(self) -> None:
        game = self.state.game
        if game is None:
            return
        for position in reversed(self.flow.temp_placed):
            if game.is_inside_board(position):
                game.remove_piece(position)
                self.remove_new_piece_mark(position)
        for position, piece in reversed(self.flow.temp_removed):
            if game.is_inside_board(position) and game.get_piece(position) is None:
                game.place_piece(position, piece)
        self.flow.temp_removed = []
        self.flow.temp_placed = []
        self.flow.pending_refund_owner = None
        self.flow.pending_refund_count = 0
        self.flow.pending_refund_kind = None
        self.refresh_players()

    def commit_temporary_transaction(self) -> None:
        self.flow.temp_removed = []
        self.flow.temp_placed = []

    def remove_new_piece_mark(self, position: Position) -> None:
        game = self.state.game
        if game is None:
            return
        game.new_piece = [pos for pos in game.new_piece if tuple(pos) != tuple(position)]

    def place_paid_squirrel(self, position: Position) -> bool:
        game = self.state.game
        if game is None:
            return False
        if game.current_ap < 3:
            self.state.interaction.set_dialog_text(ui_text.NOT_ENOUGH_AP_SQUIRREL_DIALOG)
            return False
        if not is_empty_accessible_cell(game, position):
            self.state.interaction.set_dialog_text(ui_text.ILLEGAL_SQUIRREL_TARGET_DIALOG)
            return False
        game.place_piece(position, Resource(game.current_player, "squirrel"))
        game.mark_new_piece(position)
        game.spend_ap(3)
        self.refresh_players()
        self.finish_action(ui_text.SQUIRREL_PLACED_DIALOG)
        return True

    def place_bought_special(self, position: Position, kind: str) -> bool:
        game = self.state.game
        if game is None:
            return False
        cost_positions = list(self.state.interaction.selected_cost)
        if len(cost_positions) < BUY_COST[kind]:
            self.flow.phase = PHASE_SELECT_COST
            self.state.interaction.set_dialog_text(ui_text.choose_cost(kind, len(cost_positions), BUY_COST[kind]))
            return False
        if not is_empty_accessible_cell(game, position):
            self.state.interaction.set_dialog_text(ui_text.ILLEGAL_TARGET_DIALOG)
            return False
        game.place_piece(position, Resource(game.current_player, kind))
        game.mark_new_piece(position)
        game.spend_ap(1)
        self.commit_temporary_transaction()
        self.refresh_players()
        self.finish_action(ui_text.special_bought(kind))
        return True

    def place_refund_squirrel(self, position: Position) -> None:
        game = self.state.game
        owner = self.flow.pending_refund_owner
        if game is None or owner is None:
            return
        if not is_empty_accessible_cell(game, position):
            self.state.interaction.set_dialog_text(ui_text.ILLEGAL_SQUIRREL_TARGET_DIALOG)
            return
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
            self.commit_temporary_transaction()
            self.finish_action(ui_text.special_sold(sold_kind))
        else:
            self.state.interaction.set_dialog_text(ui_text.choose_refund(self.flow.pending_refund_count))

    def place_elephant_bonus(self, position: Position) -> None:
        game = self.state.game
        if game is None:
            return
        bonus_position = self.state.interaction.elephant_start_position
        if position != bonus_position or not is_empty_accessible_cell(game, position):
            self.state.interaction.set_dialog_text(ui_text.ILLEGAL_SQUIRREL_TARGET_DIALOG)
            return
        game.place_piece(position, Resource(game.current_player, "squirrel"))
        game.mark_new_piece(position)
        self.refresh_players()
        self.finish_action(ui_text.ELEPHANT_BONUS_PLACED_DIALOG)

    def try_select_piece(self, position: Position) -> bool:
        game = self.state.game
        if game is None or not can_select_piece(game, self.state.players, position):
            self.state.interaction.set_dialog_text(ui_text.NO_LEGAL_ACTION_DIALOG)
            return False
        piece = self.get_piece(position)
        self.state.interaction.selected_pos = position
        self.flow.phase = PHASE_SELECT_TARGET
        self.flow.pending_action = ACTION_MOVE
        self.state.interaction.set_dialog_text(ui_text.selected_piece(piece.kind))
        return True

    def try_move_selected_piece(self, start: Position, end: Position) -> bool:
        game = self.state.game
        if game is None:
            return False
        piece = self.get_piece(start)
        if piece is None:
            self.cancel_current_action()
            self.state.interaction.set_dialog_text(ui_text.SELECTED_PIECE_MISSING_DIALOG)
            return False
        if end not in get_legal_moves_for_piece(game, self.state.players, start):
            self.state.interaction.set_dialog_text(ui_text.ILLEGAL_TARGET_DIALOG)
            return False
        cost = 2 if piece.owner != game.current_player else 1
        if piece.kind == "elephant":
            if not game.elephant_push(start, end):
                self.state.interaction.set_dialog_text(ui_text.ILLEGAL_TARGET_DIALOG)
                return False
            game.spend_ap(cost)
            self.refresh_players()
            self.clear_selection(keep_dialog=True)
            self.flow.phase = PHASE_PENDING_ELEPHANT_BONUS
            self.state.interaction.after_elephant_push = True
            self.state.interaction.elephant_start_position = start
            self.state.interaction.set_dialog_text(ui_text.ELEPHANT_BONUS_DIALOG)
            return True

        moved_piece = game.place_piece(start, None)
        game.place_piece(end, moved_piece)
        game.move_new_piece_mark(start, end)
        game.spend_ap(cost)
        self.refresh_players()
        self.finish_action(ui_text.moved_piece(piece.kind))
        return True

    def handle_piece_limit_removal(self, position: Position) -> None:
        game = self.state.game
        owner = self.flow.pending_piece_limit_owner
        piece = self.get_piece(position)
        if game is None or owner is None:
            self.flow.phase = PHASE_PLAYING
            return
        if piece is None or piece.owner != owner or not piece.is_piece:
            self.state.interaction.set_dialog_text(ui_text.PIECE_LIMIT_TARGET_DIALOG)
            return
        game.remove_piece(position)
        self.refresh_players()
        owner_state = self.state.players.get(owner)
        if owner_state is not None and owner_state.num_pieces <= 7:
            self.flow.pending_piece_limit_owner = None
            self.finish_action(ui_text.PIECE_LIMIT_RESOLVED_DIALOG)
        else:
            self.state.interaction.set_dialog_text(ui_text.PIECE_LIMIT_DIALOG)

    def enter_piece_limit_or_finish(self, message: str) -> None:
        game = self.state.game
        if game is None:
            return
        holder = game.time_token_owner
        holder_state = self.state.players.get(holder) if holder is not None else None
        if holder_state is not None and holder_state.num_pieces > 7:
            self.flow.phase = PHASE_PENDING_PIECE_LIMIT
            self.flow.pending_piece_limit_owner = holder
            self.state.interaction.set_dialog_text(ui_text.PIECE_LIMIT_DIALOG)
            return
        self.finish_action(message)

    def finish_action(self, action_message: str) -> None:
        game = self.state.game
        if game is None:
            return
        self.clear_selection(keep_dialog=True)
        if game.current_ap > 0:
            if self.check_mate_if_stuck():
                return
            self.flow.phase = PHASE_PLAYING
            self.state.interaction.set_dialog_text(ui_text.action_complete(action_message, game.current_ap))
            return
        self.begin_victory_check(action_message)

    def begin_victory_check(self, action_message: str = "") -> None:
        game = self.state.game
        if game is None:
            return
        playerstate = self.state.players.get(game.current_player)
        if playerstate is None:
            return
        results = get_g1_condition_results(playerstate, game)
        self.flow.phase = PHASE_CHECKING_WIN
        self.flow.last_condition_results = results
        self.flow.checking_action_message = action_message
        self.flow.checking_started_at = pygame.time.get_ticks()
        self.state.condition_results = results
        self.state.condition_reveal_count = 0
        self.state.mate_loser = None
        self.state.interaction.set_dialog_text(ui_text.CHECKING_WIN_DIALOG)

    def update_victory_check(self) -> None:
        if self.flow.phase != PHASE_CHECKING_WIN:
            return
        results = self.flow.last_condition_results or []
        if not results:
            self.resolve_turn_end(self.flow.checking_action_message)
            return
        elapsed = pygame.time.get_ticks() - self.flow.checking_started_at
        reveal_count = min(len(results), elapsed // CONDITION_REVEAL_MS)
        self.state.condition_reveal_count = int(reveal_count)
        self.state.interaction.set_dialog_text(ui_text.CHECKING_WIN_DIALOG)
        if elapsed >= (len(results) + 1) * CONDITION_REVEAL_MS:
            self.state.condition_reveal_count = len(results)
            self.resolve_turn_end(self.flow.checking_action_message)

    def resolve_turn_end(self, action_message: str = "") -> None:
        game = self.state.game
        if game is None:
            return
        results = self.flow.last_condition_results
        if results is None:
            playerstate = self.state.players.get(game.current_player)
            if playerstate is None:
                return
            results = get_g1_condition_results(playerstate, game)
            self.flow.last_condition_results = results
            self.state.condition_results = results
            self.state.condition_reveal_count = len(results)
        if all(results):
            self.flow.phase = PHASE_TIME_WISH
            self.flow.time_wish_winner = game.current_player
            token_owner = game.time_token_owner
            if token_owner in {"black", "white"}:
                game.current_player = token_owner
            game.max_ap = 1
            game.current_ap = 1
            self.state.interaction.set_dialog_text(
                f"{ui_text.time_wish_prompt(self.flow.time_wish_winner, token_owner)} "
                f"{ui_text.time_wish_turn(token_owner)}"
            )
            return
        game.new_turn()
        self.refresh_players()
        self.flow.phase = PHASE_PLAYING
        self.state.mate_loser = None
        turn_message = ui_text.turn_started(game.current_player)
        self.state.interaction.set_dialog_text(
            f"{action_message} {turn_message}" if action_message else turn_message
        )
        self.check_mate_if_stuck()

    def check_mate_if_stuck(self) -> bool:
        game = self.state.game
        if game is None or game.current_ap <= 0 or game.time_token_owner is not None:
            return False
        if self.has_legal_actions_for_current_player():
            return False
        winner = other_player(game.current_player)
        self.state.mate_loser = game.current_player
        self.flow.phase = PHASE_GAME_OVER
        self.state.interaction.set_dialog_text(ui_text.mate_loss(game.current_player, winner))
        return True

    def has_legal_actions_for_current_player(self) -> bool:
        game = self.state.game
        if game is None:
            return False
        empty_interaction = Interaction()
        if get_legal_board_targets(game, self.state.players, empty_interaction):
            return True
        if get_legal_resource_targets(game, self.state.players, empty_interaction):
            return True
        if get_legal_camp_targets(game, self.state.players, empty_interaction):
            return True
        return False

    def cancel_current_action(self) -> None:
        if self.flow.phase == PHASE_PENDING_ELEPHANT_BONUS:
            self.finish_action(ui_text.ELEPHANT_BONUS_SKIPPED_DIALOG)
            return
        if self.flow.phase == PHASE_PENDING_REFUND or self.flow.temp_removed or self.flow.temp_placed:
            self.restore_temporary_transaction()
            self.clear_selection()
            self.flow.phase = PHASE_PLAYING
            self.state.interaction.set_dialog_text(ui_text.SELECTION_CLEARED_DIALOG)
            return
        if self.flow.phase in {PHASE_PENDING_PIECE_LIMIT, PHASE_TIME_WISH}:
            self.state.interaction.set_dialog_text(ui_text.ACTION_UNAVAILABLE_DIALOG)
            return
        self.clear_selection()
        if self.state.game is None:
            self.flow.phase = PHASE_MENU
        elif self.flow.phase != PHASE_CHOOSE_TOKEN:
            self.flow.phase = PHASE_PLAYING
        self.state.interaction.set_dialog_text(ui_text.SELECTION_CLEARED_DIALOG)

    def clear_selection(self, keep_dialog: bool = False) -> None:
        old_dialog = self.state.interaction.dialog_text
        self.state.interaction.selected_pos = None
        self.state.interaction.selected_resource = None
        self.state.interaction.selected_cost = []
        self.state.interaction.after_elephant_push = False
        self.state.interaction.elephant_start_position = None
        self.flow.pending_action = None
        if keep_dialog:
            self.state.interaction.dialog_text = old_dialog

    def refresh_players(self) -> None:
        game = self.state.game
        if game is None:
            return
        for player in self.state.players.values():
            player.refresh_side(game)

    def update_tooltip(self, hovered: str | None) -> None:
        self.state.interaction.reset_tooltip_text(self.tooltip_for_region(hovered))

    def tooltip_for_region(self, hovered: str | None) -> str:
        game = self.state.game
        if hovered is None:
            return ""
        if hovered.startswith("condition:"):
            index = int(hovered.removeprefix("condition:"))
            result = None
            if self.flow.last_condition_results is not None and index < len(self.flow.last_condition_results):
                result = self.flow.last_condition_results[index]
            return ui_text.condition_tooltip(result)
        if hovered.startswith("camp:"):
            player = hovered.removeprefix("camp:")
            has_token = game is not None and game.time_token_owner == player
            is_current = game is not None and game.current_player == player
            return ui_text.player_panel_tooltip(player, has_token, is_current)
        if hovered.startswith("board:") and game is not None:
            position = tuple(int(value) for value in hovered.removeprefix("board:").split(","))
            piece = self.get_piece(position)
            if piece is None:
                return ""
            return ui_text.piece_tooltip(
                piece,
                piece.owner.capitalize(),
                self.can_lion_control_for_tooltip(position),
                game.is_new_piece(position),
            )
        if hovered.startswith("resource:"):
            return ui_text.resource_button_tooltip(
                hovered.removeprefix("resource:"),
                self.resource_tooltip_context(),
            )
        return ""

    def can_lion_control_for_tooltip(self, position: Position) -> bool:
        game = self.state.game
        if game is None:
            return False
        piece = self.get_piece(position)
        playerstate = self.state.players.get(game.current_player)
        return (
            piece is not None
            and piece.owner != game.current_player
            and piece.kind in {"squirrel", "elephant"}
            and playerstate is not None
            and playerstate.has_lion
            and game.current_ap >= 2
            and bool(get_legal_moves_for_piece(game, self.state.players, position))
        )

    def resource_tooltip_context(self) -> dict[str, Any]:
        game = self.state.game
        selected_pos = self.state.interaction.selected_pos
        selected_piece = self.get_piece(selected_pos) if selected_pos is not None else None
        return {
            "selected_own_elephant": (
                game is not None
                and selected_piece is not None
                and selected_piece.owner == game.current_player
                and selected_piece.kind == "elephant"
            ),
            "selected_own_lion": (
                game is not None
                and selected_piece is not None
                and selected_piece.owner == game.current_player
                and selected_piece.kind == "lion"
            ),
            "current_player_has_token": game is not None and game.time_token_owner == game.current_player,
            "any_player_over_piece_limit": any(player.num_pieces > 7 for player in self.state.players.values()),
            "current_ap": game.current_ap if game is not None else 0,
        }

    def get_legal_targets(self) -> tuple[set[Position], set[str], set[str]]:
        game = self.state.game
        if game is None:
            return set(), set(), set()
        if self.flow.phase == PHASE_CHOOSE_TOKEN:
            return set(), set(), {"black", "white"}
        if self.flow.phase == PHASE_TIME_WISH:
            return set(), set(), {game.time_token_owner} if game.time_token_owner else set()
        if self.flow.phase == PHASE_GAME_OVER:
            return set(), set(), set()
        if self.flow.phase == PHASE_PENDING_PIECE_LIMIT:
            return self.get_piece_limit_targets(self.flow.pending_piece_limit_owner), set(), set()
        if self.flow.phase == PHASE_PENDING_ELEPHANT_BONUS:
            return self.get_elephant_bonus_targets(), set(), set()
        if self.flow.phase == PHASE_PENDING_REFUND:
            return get_empty_accessible_cells(game), set(), set()
        if self.flow.phase == PHASE_SELECT_COST:
            return self.get_buy_cost_targets(), set(), set()
        if self.flow.pending_action in {ACTION_PLACE_SQUIRREL, ACTION_BUY_ELEPHANT, ACTION_BUY_LION}:
            return get_empty_accessible_cells(game), set(), set()
        return (
            get_legal_board_targets(game, self.state.players, self.state.interaction),
            get_legal_resource_targets(game, self.state.players, self.state.interaction),
            get_legal_camp_targets(game, self.state.players, self.state.interaction),
        )

    def get_buy_cost_targets(self) -> set[Position]:
        game = self.state.game
        if game is None:
            return set()
        selected = set(self.state.interaction.selected_cost)
        return {
            (x, y)
            for y, row in enumerate(game.board_matrix)
            for x, piece in enumerate(row)
            if piece is not None
            and piece.owner == game.current_player
            and piece.kind == "squirrel"
            and (x, y) not in selected
        }

    def get_elephant_bonus_targets(self) -> set[Position]:
        game = self.state.game
        bonus_position = self.state.interaction.elephant_start_position
        if game is None or bonus_position is None:
            return set()
        if is_empty_accessible_cell(game, bonus_position):
            return {bonus_position}
        return set()

    def get_piece_limit_targets(self, owner: str | None) -> set[Position]:
        game = self.state.game
        if game is None or owner is None:
            return set()
        return {
            (x, y)
            for y, row in enumerate(game.board_matrix)
            for x, piece in enumerate(row)
            if piece is not None and piece.owner == owner and piece.is_piece
        }

    def get_piece(self, position: Position | None) -> Any | None:
        game = self.state.game
        if game is None or position is None or not game.is_inside_board(position):
            return None
        return game.get_piece(position)


def other_player(player: str) -> str:
    return "white" if player == "black" else "black"


def main() -> None:
    CheckMateApp().run()


"""
Current main-loop draft flow:

Create a board according to gamemode.

while gaming:
    If right-click cancels:
        Clear the current selection.
        Return to waiting for current-turn input.

    If the current player has AP remaining and no legal actions:
        The opponent wins by mate.
        End the game.

    Wait for player input.

    Update selection state from input:
        Select a piece.
        Select a button.
        Select squirrels used as cost.
        Select a target cell.
        Select over-limit pieces to remove.
        Select the elephant bonus squirrel cell.
        Select refund squirrel cells after selling a special piece.

    If the current action is incomplete:
        Return to waiting for current-turn input.

    If the current action is complete:
        Spend AP.
        Resolve action results.

    If the action triggers a mandatory pending step:
        Examples:
        - Elephant movement may place one free squirrel.
        - Selling a special piece must place refunded squirrels.
        - Moving the time token may force the holder down to 7 pieces.
        Enter the pending phase first.
        Do not immediately resolve turn end.

    If the current player still has AP:
        Return to waiting for current-turn input.

    If the current player's AP is exhausted:
        Check victory conditions for the current gamemode.
        If all conditions are satisfied:
            The current player wins by check.
            Enter the time-wish flow.
        Otherwise:
            Switch current player.
            Increase/set next-turn AP.
            Clear this turn's new-piece markers.
            Return to waiting for input.

Victory draft flow:

If check wins:
    The time-token holder enters a 1 AP special turn.
    Wait for input.
    The time-token holder spends the token.
    Output the G1 continuation save.

If mate wins:
    Directly show the winning side.
"""



if __name__ == "__main__":
    main()
