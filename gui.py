from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pygame


BASE_DIR = Path(__file__).resolve().parent
PIC_DIR = BASE_DIR / "pic"

WINDOW_W = 1200
WINDOW_H = 880
FPS = 60

CELL = 88
VISIBLE_GRID_W = 5
VISIBLE_GRID_H = 5
LOGICAL_GRID_W = 9
LOGICAL_GRID_H = 7
VISIBLE_OFFSET_COL = 2
VISIBLE_OFFSET_ROW = 1

BOARD_W = CELL * VISIBLE_GRID_W
BOARD_H = CELL * VISIBLE_GRID_H
BOARD_X = (WINDOW_W - BOARD_W) // 2
BOARD_Y = 184

CONDITION_Y = 58
CONDITION_GAP = 22

MENU_X = 24
MENU_Y = 12
MENU_W = 126
MENU_H = 34
MENU_GAP = 8
DROPDOWN_W = 300
DROPDOWN_ROW_H = 34

DIALOG_W = 1080
DIALOG_H = 88
DIALOG_X = (WINDOW_W - DIALOG_W) // 2
DIALOG_Y = BOARD_Y + BOARD_H + 24

BUTTON_W = CELL * 2
BUTTON_H = CELL
BUTTON_GAP = 28
BUTTON_ROW_W = BUTTON_W * 4 + BUTTON_GAP * 3
BUTTON_X = (WINDOW_W - BUTTON_ROW_W) // 2
BUTTON_Y = DIALOG_Y + DIALOG_H + 20

LEFT_CAMP_RECT = pygame.Rect(64, BOARD_Y - 14, CELL * 2, CELL * 2)
RIGHT_CAMP_RECT = pygame.Rect(WINDOW_W - 64 - CELL * 2, BOARD_Y - 14, CELL * 2, CELL * 2)
LEFT_STATUS_RECT = pygame.Rect(64, BOARD_Y + CELL * 3 + 20, CELL * 2, 128)
RIGHT_STATUS_RECT = pygame.Rect(WINDOW_W - 64 - CELL * 2, BOARD_Y + CELL * 3 + 20, CELL * 2, 128)

LEGAL_HINT_COLOR = (58, 220, 82)
LEGAL_HINT_WIDTH = 5
LEGAL_HINT_INSET = 8
LEGAL_HINT_ARM = 20


@dataclass(frozen=True)
class ClickRegion:
    name: str
    rect: pygame.Rect


class CheckMateGui:
    """Lightweight Pygame renderer for the rewritten project.

    This file deliberately avoids importing the old GameState. The caller should
    pass any object that exposes similar data:
    - game.board_matrix or game.board: 7x9 list of pieces or None.
    - game.current_player: "black" or "white".
    - game.time_token_owner: "black", "white", or None.
    - game.message or game.interaction.dialog_text for the dialog.
    - optional game.players for AP display.
    """

    def __init__(self) -> None:
        pygame.init()
        pygame.display.set_caption("CheckMate")
        self.screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
        self.clock = pygame.time.Clock()
        self.small_font = pygame.font.SysFont("consolas", 18)
        self.menu_font = pygame.font.SysFont("consolas", 18, bold=True)
        self.button_font = pygame.font.SysFont("consolas", 20, bold=True)
        self.tooltip_font = pygame.font.SysFont("consolas", 16)

        self.images = self.load_images()
        self.background = self.create_wood_background()
        self.condition_files = ["g1_0.png", "g1_1.png", "g1_2.png", "g1_3.png", "g1_4.png"]
        self.condition_images: dict[str, pygame.Surface] = {}
        self.prepare_condition_images()

        self.resource_buttons = [
            ("squirrel", "Squirrel"),
            ("elephant", "Elephant (2)"),
            ("lion", "Lion (4)"),
            ("time_token", "Time Token"),
        ]
        self.open_menu: str | None = None
        self.save_slots: dict[str, Path | None] = {}
        self.click_regions: list[ClickRegion] = []
        self.build_click_regions()

    def load_images(self) -> dict[str, pygame.Surface]:
        def load(name: str) -> pygame.Surface:
            return pygame.image.load(PIC_DIR / name).convert_alpha()

        images: dict[str, pygame.Surface] = {}
        for name in [
            "board.png",
            "white_chess.png",
            "black_chess.png",
            "squ.png",
            "pine.png",
            "lion.png",
            "elephant.png",
            "time_token_light.png",
            "time_token_dark.png",
            "g1_1.png",
            "g1_2.png",
            "g1_3.png",
            "g1_4.png",
            "g1_0.png",
        ]:
            images[name] = load(name)

        images["board_cell"] = self.create_light_wood_cell()
        images["white_cell"] = pygame.transform.smoothscale(images["white_chess.png"], (CELL, CELL))
        images["black_cell"] = pygame.transform.smoothscale(images["black_chess.png"], (CELL, CELL))
        images["squirrel"] = pygame.transform.scale(images["squ.png"], (56, 56))
        images["acorn"] = pygame.transform.scale(images["pine.png"], (34, 34))
        images["lion_piece"] = pygame.transform.scale(images["lion.png"], (68, 68))
        images["elephant_piece"] = pygame.transform.scale(images["elephant.png"], (68, 68))
        images["time_token_small"] = pygame.transform.scale(images["time_token_light.png"], (40, 60))
        images["time_token_light_panel"] = pygame.transform.scale(images["time_token_light.png"], (60, 90))
        images["time_token_dark_panel"] = pygame.transform.scale(images["time_token_dark.png"], (60, 90))
        return images

    def create_light_wood_cell(self) -> pygame.Surface:
        rng = random.Random(31)
        surface = pygame.Surface((CELL, CELL), pygame.SRCALPHA)
        surface.fill((211, 176, 122, 255))

        for y in range(4, CELL, 6):
            color = rng.choice([(225, 193, 139), (190, 151, 97), (236, 203, 151), (174, 133, 83)])
            points = [(x, y + rng.randint(-2, 2)) for x in range(-8, CELL + 12, 18)]
            pygame.draw.lines(surface, color, False, points, 1)

        pygame.draw.rect(surface, (240, 218, 174), surface.get_rect(), 2)
        pygame.draw.line(surface, (142, 99, 55), (0, CELL - 1), (CELL - 1, CELL - 1), 2)
        pygame.draw.line(surface, (142, 99, 55), (CELL - 1, 0), (CELL - 1, CELL - 1), 2)
        return surface

    def create_wood_background(self) -> pygame.Surface:
        rng = random.Random(12)
        surface = pygame.Surface((WINDOW_W, WINDOW_H))
        surface.fill((128, 82, 42))

        for y in range(12, WINDOW_H, 7):
            color = rng.choice([(98, 58, 28), (151, 95, 48), (111, 67, 34), (170, 110, 58)])
            points = [(x, y + rng.randint(-3, 3)) for x in range(rng.randint(-50, 30), WINDOW_W + 80, 70)]
            if len(points) > 1:
                pygame.draw.lines(surface, color, False, points, rng.choice([1, 1, 2]))

        for _ in range(360):
            x = rng.randrange(0, WINDOW_W)
            y = rng.randrange(12, WINDOW_H)
            w = rng.randrange(18, 80)
            color = rng.choice([(92, 51, 24), (158, 97, 47), (120, 73, 36)])
            pygame.draw.line(surface, color, (x, y), (min(WINDOW_W, x + w), y + rng.randint(-2, 2)), 1)
        return surface

    def prepare_condition_images(self) -> None:
        max_size = CELL - 4
        for name in self.condition_files:
            cropped = self.crop_condition_content_square(self.images[name])
            scale = min(max_size / cropped.get_width(), max_size / cropped.get_height())
            size = (
                max(1, round(cropped.get_width() * scale)),
                max(1, round(cropped.get_height() * scale)),
            )
            self.condition_images[name] = pygame.transform.smoothscale(cropped, size)

    def crop_condition_content_square(self, surface: pygame.Surface) -> pygame.Surface:
        width, height = surface.get_size()
        xs: list[int] = []
        ys: list[int] = []
        for y in range(height):
            for x in range(width):
                r, g, b, a = surface.get_at((x, y))
                if a > 10 and (min(r, g, b) < 185 or max(r, g, b) - min(r, g, b) > 35):
                    xs.append(x)
                    ys.append(y)

        if not xs:
            return surface.copy()

        padding = 5
        left = max(0, min(xs) - padding)
        right = min(width, max(xs) + padding + 1)
        top = max(0, min(ys) - padding)
        bottom = min(height, max(ys) + padding + 1)
        side = max(right - left, bottom - top)
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        square_left = max(0, min(width - side, center_x - side // 2))
        square_top = max(0, min(height - side, center_y - side // 2))
        return surface.subsurface(pygame.Rect(square_left, square_top, side, side)).copy()

    def build_click_regions(self) -> None:
        self.click_regions.clear()
        self.add_menu_click_regions()

        for visible_y in range(VISIBLE_GRID_H):
            for visible_x in range(VISIBLE_GRID_W):
                rect = pygame.Rect(BOARD_X + visible_x * CELL, BOARD_Y + visible_y * CELL, CELL, CELL)
                logical_x = VISIBLE_OFFSET_COL + visible_x
                logical_y = VISIBLE_OFFSET_ROW + visible_y
                self.click_regions.append(ClickRegion(f"board:{logical_x},{logical_y}", rect))

        for index, (resource_id, _label) in enumerate(self.resource_buttons):
            x = BUTTON_X + index * (BUTTON_W + BUTTON_GAP)
            self.click_regions.append(ClickRegion(f"resource:{resource_id}", pygame.Rect(x, BUTTON_Y, BUTTON_W, BUTTON_H)))

        for index in range(len(self.condition_files)):
            rect = self.condition_rect(index)
            self.click_regions.append(ClickRegion(f"condition:{index}", rect))

        self.click_regions.append(ClickRegion("camp:black", LEFT_CAMP_RECT))
        self.click_regions.append(ClickRegion("camp:white", RIGHT_CAMP_RECT))

    def add_menu_click_regions(self) -> None:
        for index, menu_id in enumerate(("start", "save", "load")):
            self.click_regions.append(ClickRegion(f"menu:{menu_id}", self.menu_rect(index)))

        if self.open_menu == "start":
            self.click_regions.append(ClickRegion("menu:start_g1", self.dropdown_rect(0)))
        elif self.open_menu == "save":
            for index in range(3):
                self.click_regions.append(ClickRegion(f"menu:save_slot_{index + 1}", self.dropdown_rect(index)))
        elif self.open_menu == "load":
            for index, (_label, path) in enumerate(self.load_menu_items()):
                name = f"menu:load:{path.name}" if path is not None else f"menu:disabled_load_{index}"
                self.click_regions.append(ClickRegion(name, self.dropdown_rect(index)))

    def menu_rect(self, index: int) -> pygame.Rect:
        return pygame.Rect(MENU_X + index * (MENU_W + MENU_GAP), MENU_Y, MENU_W, MENU_H)

    def dropdown_rect(self, index: int) -> pygame.Rect:
        x = MENU_X + {"start": 0, "save": 1, "load": 2}.get(self.open_menu or "start", 0) * (MENU_W + MENU_GAP)
        y = MENU_Y + MENU_H + 4 + index * DROPDOWN_ROW_H
        return pygame.Rect(x, y, DROPDOWN_W, DROPDOWN_ROW_H)

    def load_menu_items(self) -> list[tuple[str, Path | None]]:
        labels = [
            ("Slot 1", "slot_1", "No save for Slot 1"),
            ("Slot 2", "slot_2", "No save for Slot 2"),
            ("Slot 3", "slot_3", "No save for Slot 3"),
            ("Autosave", "autosave", "No autosave"),
        ]
        items: list[tuple[str, Path | None]] = []
        for label, key, empty_label in labels:
            path = self.save_slots.get(key)
            items.append((f"{label}: {path.name}" if path is not None else empty_label, path))
        return items

    def find_region_name(self, pos: tuple[int, int]) -> str | None:
        for region in self.click_regions:
            if region.rect.collidepoint(pos):
                return region.name
        return None

    def draw(
        self,
        game: Any,
        legal_board_targets: set[tuple[int, int]] | None = None,
        legal_resource_targets: set[str] | None = None,
        legal_camp_targets: set[str] | None = None,
        tooltip_text: str = "",
        mouse_pos: tuple[int, int] | None = None,
        save_slots: dict[str, Path | None] | None = None,
    ) -> None:
        legal_board_targets = legal_board_targets or set()
        legal_resource_targets = legal_resource_targets or set()
        legal_camp_targets = legal_camp_targets or set()
        self.save_slots = save_slots or {}
        self.build_click_regions()
        mouse_pos = mouse_pos or pygame.mouse.get_pos()
        hovered = self.find_region_name(mouse_pos)

        self.screen.blit(self.background, (0, 0))
        self.draw_conditions(game)
        self.draw_side_panels(game, hovered, legal_camp_targets)
        self.draw_board(game, hovered, legal_board_targets)
        self.draw_dialog(self.get_dialog_text(game))
        self.draw_resource_buttons(hovered, legal_resource_targets)
        self.draw_menu_bar(hovered)
        if tooltip_text:
            self.draw_tooltip(tooltip_text, mouse_pos)

    def draw_conditions(self, game: Any) -> None:
        results = getattr(game, "condition_results", None)
        reveal_count = int(getattr(game, "condition_reveal_count", 0) or 0)
        for index, name in enumerate(self.condition_files):
            rect = self.condition_rect(index)
            self.draw_beveled_rect(rect, (188, 188, 184), enabled=False)
            image = self.condition_images[name]
            self.screen.blit(image, image.get_rect(center=rect.center))
            if results is not None and index < len(results) and index < reveal_count:
                color = (40, 210, 72) if results[index] else (224, 48, 48)
                pygame.draw.rect(self.screen, color, rect.inflate(10, 10), 5, border_radius=4)

    def draw_menu_bar(self, hovered: str | None) -> None:
        labels = [("start", "Start"), ("save", "Save"), ("load", "Load")]
        for index, (menu_id, label) in enumerate(labels):
            rect = self.menu_rect(index)
            enabled = hovered == f"menu:{menu_id}" or self.open_menu == menu_id
            self.draw_beveled_rect(rect, (214, 211, 204), enabled=enabled)
            rendered = self.menu_font.render(label, True, (12, 12, 12))
            self.screen.blit(rendered, rendered.get_rect(center=rect.center))

        if self.open_menu == "start":
            self.draw_dropdown_row(0, "Start G1", hovered == "menu:start_g1", enabled=True)
        elif self.open_menu == "save":
            for index in range(3):
                name = f"menu:save_slot_{index + 1}"
                self.draw_dropdown_row(index, f"Slot {index + 1}", hovered == name, enabled=True)
        elif self.open_menu == "load":
            for index, (label, path) in enumerate(self.load_menu_items()):
                name = f"menu:load:{path.name}" if path is not None else f"menu:disabled_load_{index}"
                self.draw_dropdown_row(index, label, hovered == name, enabled=path is not None)

    def draw_dropdown_row(self, index: int, label: str, hovered: bool, enabled: bool) -> None:
        rect = self.dropdown_rect(index)
        fill = (238, 235, 226) if enabled else (166, 163, 156)
        self.draw_beveled_rect(rect, fill, enabled=hovered and enabled)
        color = (18, 18, 18) if enabled else (82, 82, 82)
        label = self.fit_text(label, rect.width - 24, self.small_font)
        text = self.small_font.render(label, True, color)
        self.screen.blit(text, (rect.x + 12, rect.y + 8))

    def condition_rect(self, index: int) -> pygame.Rect:
        row_width = len(self.condition_files) * CELL + max(0, len(self.condition_files) - 1) * CONDITION_GAP
        start_x = (WINDOW_W - row_width) // 2
        return pygame.Rect(start_x + index * (CELL + CONDITION_GAP), CONDITION_Y, CELL, CELL)

    def draw_side_panels(self, game: Any, hovered: str | None, legal_camp_targets: set[str]) -> None:
        token_owner = getattr(game, "time_token_owner", None)
        mate_loser = getattr(game, "mate_loser", None)
        self.draw_player_panel(
            LEFT_CAMP_RECT,
            "black",
            hovered == "camp:black",
            "black" in legal_camp_targets,
            token_owner == "black",
            mate_loser == "black",
        )
        self.draw_player_panel(
            RIGHT_CAMP_RECT,
            "white",
            hovered == "camp:white",
            "white" in legal_camp_targets,
            token_owner == "white",
            mate_loser == "white",
        )
        self.draw_player_status(LEFT_STATUS_RECT, game, "Black", "black")
        self.draw_player_status(RIGHT_STATUS_RECT, game, "White", "white")

    def draw_player_panel(
        self,
        rect: pygame.Rect,
        side: str,
        highlighted: bool,
        legal: bool,
        token_owner: bool,
        mate_loser: bool,
    ) -> None:
        pygame.draw.rect(self.screen, (83, 50, 26), rect.inflate(14, 14), border_radius=4)
        self.draw_beveled_rect(rect, (205, 205, 198), enabled=highlighted)
        cell_name = "black_cell" if side == "black" else "white_cell"
        cell = pygame.transform.smoothscale(self.images[cell_name], (rect.width - 28, rect.height - 28))
        self.screen.blit(cell, (rect.x + 14, rect.y + 14))
        if token_owner:
            token_key = "time_token_dark_panel" if side == "white" else "time_token_light_panel"
            token = self.images[token_key]
            self.screen.blit(token, token.get_rect(center=rect.center))
        if legal:
            self.draw_legal_hint_corners(rect)
        if mate_loser:
            self.draw_failure_cross(rect)

    def draw_failure_cross(self, rect: pygame.Rect) -> None:
        color = (210, 24, 24)
        width = 10
        inset = 18
        pygame.draw.line(
            self.screen,
            color,
            (rect.left + inset, rect.top + inset),
            (rect.right - inset, rect.bottom - inset),
            width,
        )
        pygame.draw.line(
            self.screen,
            color,
            (rect.right - inset, rect.top + inset),
            (rect.left + inset, rect.bottom - inset),
            width,
        )

    def draw_player_status(self, rect: pygame.Rect, game: Any, label: str, player_id: str) -> None:
        pygame.draw.rect(self.screen, (24, 20, 16), rect, border_radius=4)
        pygame.draw.rect(self.screen, (220, 214, 202), rect, 2, border_radius=4)
        current = getattr(game, "current_player", None) == player_id
        name = self.small_font.render(label, True, (255, 222, 96) if current else (232, 232, 224))
        ap_text = self.get_ap_text(game, player_id) if current else "AP - / -"
        ap = self.small_font.render(ap_text, True, (235, 235, 225))
        self.screen.blit(name, (rect.x + 14, rect.y + 12))
        self.screen.blit(ap, (rect.x + 14, rect.y + 42))

    def get_ap_text(self, game: Any, player_id: str) -> str:
        player = self.get_player(game, player_id)
        if player is not None:
            ap = self.get_value(player, "action_points", self.get_value(player, "ap", None))
            max_ap = self.get_value(player, "max_action_points", self.get_value(player, "max_ap", None))
            if ap is not None and max_ap is not None:
                return f"AP {ap} / {max_ap}"
        ap = self.get_value(game, "current_ap", self.get_value(game, "current_action_points", self.get_value(game, "action_points", "-")))
        max_ap = self.get_value(game, "max_ap", self.get_value(game, "max_action_points", "-"))
        return f"AP {ap} / {max_ap}"

    def draw_board(self, game: Any, hovered: str | None, legal_board_targets: set[tuple[int, int]]) -> None:
        board = self.get_board_matrix(game)
        self.draw_outer_logical_pieces(board)
        outer = pygame.Rect(BOARD_X - 4, BOARD_Y - 4, BOARD_W + 8, BOARD_H + 8)
        pygame.draw.rect(self.screen, (28, 28, 28), outer)
        pygame.draw.rect(self.screen, (230, 230, 224), outer, 2)

        for visible_y in range(VISIBLE_GRID_H):
            for visible_x in range(VISIBLE_GRID_W):
                rect = pygame.Rect(BOARD_X + visible_x * CELL, BOARD_Y + visible_y * CELL, CELL, CELL)
                logical_x = VISIBLE_OFFSET_COL + visible_x
                logical_y = VISIBLE_OFFSET_ROW + visible_y
                self.draw_cell(rect, self.piece_at(board, (logical_x, logical_y)))

        for position in legal_board_targets:
            rect = self.rect_for_logical_cell(*position)
            if rect is not None:
                self.draw_legal_hint_corners(rect)

        if hovered and hovered.startswith("board:"):
            rect = self.rect_for_board_region(hovered)
            if rect is not None:
                pygame.draw.rect(self.screen, (255, 245, 180), rect.inflate(-5, -5), 3)

        selected = self.get_selected_position(game)
        if selected is not None:
            rect = self.rect_for_logical_cell(*selected)
            if rect is not None:
                pygame.draw.rect(self.screen, (255, 216, 36), rect.inflate(-8, -8), 5)
                pygame.draw.rect(self.screen, (55, 28, 0), rect.inflate(-14, -14), 2)

    def draw_outer_logical_pieces(self, board: list[list[Any]]) -> None:
        for y, row in enumerate(board):
            for x, piece in enumerate(row):
                if piece is None or self.is_clickable_visible_cell(x, y):
                    continue
                rect = self.rect_for_rendered_outer_cell(x, y)
                if rect is not None and self.screen.get_rect().colliderect(rect):
                    self.draw_cell(rect, piece)

    def draw_cell(self, rect: pygame.Rect, piece: Any | None) -> None:
        self.screen.blit(self.images["board_cell"], rect.topleft)
        if piece is not None:
            self.draw_piece(rect, self.piece_owner(piece), self.piece_kind(piece))
        pygame.draw.rect(self.screen, (32, 32, 32), rect, 2)
        pygame.draw.line(self.screen, (255, 255, 255), rect.topleft, rect.topright, 1)
        pygame.draw.line(self.screen, (255, 255, 255), rect.topleft, rect.bottomleft, 1)

    def draw_piece(self, rect: pygame.Rect, side: str, kind: str) -> None:
        cell_name = "black_cell" if side == "black" else "white_cell"
        self.screen.blit(self.images[cell_name], rect.topleft)
        image_key = {
            "squirrel": "squirrel",
            "acorn": "acorn",
            "pine": "acorn",
            "pinecone": "acorn",
            "lion": "lion_piece",
            "elephant": "elephant_piece",
        }.get(kind)
        if image_key is not None:
            image = self.images[image_key]
            self.screen.blit(image, image.get_rect(center=rect.center))

    def draw_dialog(self, message: str) -> None:
        rect = pygame.Rect(DIALOG_X, DIALOG_Y, DIALOG_W, DIALOG_H)
        pygame.draw.rect(self.screen, (10, 10, 10), rect)
        pygame.draw.rect(self.screen, (230, 230, 230), rect, 3)
        pygame.draw.rect(self.screen, (92, 92, 92), rect.inflate(-8, -8), 1)
        for index, line in enumerate(self.wrap_text(message, rect.width - 40, max_lines=3, font=self.small_font)):
            label = self.small_font.render(line, True, (235, 235, 225))
            self.screen.blit(label, (rect.x + 20, rect.y + 12 + index * 22))

    def draw_resource_buttons(self, hovered: str | None, legal_resource_targets: set[str]) -> None:
        for index, (resource_id, label) in enumerate(self.resource_buttons):
            x = BUTTON_X + index * (BUTTON_W + BUTTON_GAP)
            rect = pygame.Rect(x, BUTTON_Y, BUTTON_W, BUTTON_H)
            self.draw_beveled_rect(rect, (214, 211, 204), enabled=hovered == f"resource:{resource_id}")
            if resource_id == "time_token":
                icon = self.images["time_token_small"]
                self.screen.blit(icon, icon.get_rect(center=(rect.centerx, rect.y + 30)))
            else:
                self.draw_reserve_piece((rect.centerx - 30, rect.y + 27), "black", resource_id)
                self.draw_reserve_piece((rect.centerx + 30, rect.y + 27), "white", resource_id)
            text = self.button_font.render(label, True, (12, 12, 12))
            self.screen.blit(text, text.get_rect(center=(rect.centerx, rect.bottom - 16)))
            if resource_id in legal_resource_targets:
                self.draw_legal_hint_corners(rect)

    def draw_reserve_piece(self, center: tuple[int, int], side: str, kind: str) -> None:
        cell_name = "black_cell" if side == "black" else "white_cell"
        cell = pygame.transform.smoothscale(self.images[cell_name], (42, 42))
        self.screen.blit(cell, cell.get_rect(center=center))
        image_name = {"squirrel": "squ.png", "lion": "lion.png", "elephant": "elephant.png"}.get(kind)
        if image_name is not None:
            size = (26, 26) if kind == "squirrel" else (34, 34)
            piece = pygame.transform.scale(self.images[image_name], size)
            self.screen.blit(piece, piece.get_rect(center=center))

    def draw_tooltip(self, text: str, mouse_pos: tuple[int, int]) -> None:
        padding = 10
        lines = self.wrap_tooltip_text(text, 360, max_lines=8, font=self.tooltip_font)
        rendered = [self.tooltip_font.render(line, True, (22, 18, 12)) for line in lines]
        width = max(line.get_width() for line in rendered) + padding * 2
        height = len(rendered) * 20 + padding * 2
        x = mouse_pos[0] + 16
        y = mouse_pos[1] + 18
        if x + width > WINDOW_W - 8:
            x = mouse_pos[0] - width - 16
        if y + height > WINDOW_H - 8:
            y = mouse_pos[1] - height - 16
        rect = pygame.Rect(x, y, width, height)
        pygame.draw.rect(self.screen, (248, 241, 219), rect, border_radius=4)
        pygame.draw.rect(self.screen, (38, 31, 22), rect, 2, border_radius=4)
        for index, line in enumerate(rendered):
            self.screen.blit(line, (rect.x + padding, rect.y + padding + index * 20))

    def wrap_tooltip_text(self, text: str, max_width: int, max_lines: int, font: pygame.font.Font) -> list[str]:
        lines: list[str] = []
        for raw_line in str(text).splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            prefix = "- " if stripped.startswith("-") else ""
            content = stripped[1:].strip() if prefix else stripped
            wrapped = self.wrap_text(content, max_width - font.size(prefix)[0], max_lines, font)
            for index, line in enumerate(wrapped):
                if len(lines) >= max_lines:
                    break
                lines.append((prefix if index == 0 else "  ") + line)
            if len(lines) >= max_lines:
                break
        return lines or [""]

    def draw_legal_hint_corners(self, rect: pygame.Rect) -> None:
        x0 = rect.left + LEGAL_HINT_INSET
        y0 = rect.top + LEGAL_HINT_INSET
        x1 = rect.right - LEGAL_HINT_INSET
        y1 = rect.bottom - LEGAL_HINT_INSET
        arm = LEGAL_HINT_ARM
        color = LEGAL_HINT_COLOR
        width = LEGAL_HINT_WIDTH
        pygame.draw.line(self.screen, color, (x0, y0), (x0 + arm, y0), width)
        pygame.draw.line(self.screen, color, (x0, y0), (x0, y0 + arm), width)
        pygame.draw.line(self.screen, color, (x1, y0), (x1 - arm, y0), width)
        pygame.draw.line(self.screen, color, (x1, y0), (x1, y0 + arm), width)
        pygame.draw.line(self.screen, color, (x0, y1), (x0 + arm, y1), width)
        pygame.draw.line(self.screen, color, (x0, y1), (x0, y1 - arm), width)
        pygame.draw.line(self.screen, color, (x1, y1), (x1 - arm, y1), width)
        pygame.draw.line(self.screen, color, (x1, y1), (x1, y1 - arm), width)

    def draw_beveled_rect(self, rect: pygame.Rect, fill: tuple[int, int, int], enabled: bool) -> None:
        pygame.draw.rect(self.screen, (34, 34, 34), rect.inflate(6, 6), border_radius=3)
        pygame.draw.rect(self.screen, fill, rect, border_radius=3)
        pygame.draw.line(self.screen, (255, 255, 255), rect.topleft, rect.topright, 3)
        pygame.draw.line(self.screen, (255, 255, 255), rect.topleft, rect.bottomleft, 3)
        pygame.draw.line(self.screen, (88, 88, 88), rect.bottomleft, rect.bottomright, 3)
        pygame.draw.line(self.screen, (88, 88, 88), rect.topright, rect.bottomright, 3)
        if enabled:
            pygame.draw.rect(self.screen, (255, 224, 98), rect.inflate(-8, -8), 3, border_radius=2)

    def wrap_text(self, text: str, max_width: int, max_lines: int, font: pygame.font.Font) -> list[str]:
        words = str(text).split()
        lines: list[str] = []
        current = ""
        truncated = False
        for index, word in enumerate(words):
            candidate = word if not current else f"{current} {word}"
            if font.size(candidate)[0] <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
            if len(lines) >= max_lines:
                truncated = index < len(words)
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if truncated and len(lines) == max_lines:
            while lines[-1] and font.size(lines[-1] + "...")[0] > max_width:
                lines[-1] = lines[-1][:-1]
            lines[-1] += "..."
        return lines or [""]

    def fit_text(self, text: str, max_width: int, font: pygame.font.Font) -> str:
        text = str(text)
        if font.size(text)[0] <= max_width:
            return text
        while text and font.size(text + "...")[0] > max_width:
            text = text[:-1]
        return text + "..."

    def piece_at(self, board: list[list[Any]], position: tuple[int, int]) -> Any | None:
        logical_x, logical_y = position
        if not (0 <= logical_y < len(board)):
            return None
        if not (0 <= logical_x < len(board[logical_y])):
            return None
        return board[logical_y][logical_x]

    def piece_owner(self, piece: Any) -> str:
        return str(self.get_value(piece, "owner", self.get_value(piece, "holder", "black")))

    def piece_kind(self, piece: Any) -> str:
        return str(self.get_value(piece, "kind", self.get_value(piece, "type", "squirrel")))

    def get_selected_position(self, game: Any) -> tuple[int, int] | None:
        interaction = getattr(game, "interaction", None)
        if interaction is not None:
            selected = getattr(interaction, "selected_pos", None)
            if selected is not None:
                return selected
            selected = getattr(interaction, "selected_position", None)
            if selected is not None:
                return selected
        return getattr(game, "selected_position", None)

    def get_board_matrix(self, game: Any) -> list[list[Any]]:
        board = getattr(game, "board_matrix", None)
        if board is not None:
            return board
        return getattr(game, "board", [])

    def get_dialog_text(self, game: Any) -> str:
        interaction = getattr(game, "interaction", None)
        if interaction is not None:
            text = getattr(interaction, "dialog_text", "")
            if text:
                return str(text)
        return str(getattr(game, "message", ""))

    def get_player(self, game: Any, player_id: str) -> Any | None:
        players = getattr(game, "players", None)
        if isinstance(players, dict):
            return players.get(player_id)
        return None

    def get_value(self, obj: Any, name: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    def rect_for_board_region(self, name: str) -> pygame.Rect | None:
        logical_x, logical_y = (int(value) for value in name.removeprefix("board:").split(","))
        return self.rect_for_logical_cell(logical_x, logical_y)

    def rect_for_logical_cell(self, logical_x: int, logical_y: int) -> pygame.Rect | None:
        visible_x = logical_x - VISIBLE_OFFSET_COL
        visible_y = logical_y - VISIBLE_OFFSET_ROW
        if not (0 <= visible_y < VISIBLE_GRID_H and 0 <= visible_x < VISIBLE_GRID_W):
            return None
        return pygame.Rect(BOARD_X + visible_x * CELL, BOARD_Y + visible_y * CELL, CELL, CELL)

    def rect_for_rendered_outer_cell(self, logical_x: int, logical_y: int) -> pygame.Rect | None:
        visible_y = logical_y - VISIBLE_OFFSET_ROW
        if not (0 <= visible_y < VISIBLE_GRID_H):
            return None
        if logical_x < VISIBLE_OFFSET_COL or logical_x >= VISIBLE_OFFSET_COL + VISIBLE_GRID_W:
            visual_x = logical_x - VISIBLE_OFFSET_COL
            return pygame.Rect(BOARD_X + visual_x * CELL, BOARD_Y + visible_y * CELL, CELL, CELL)
        return None

    def is_clickable_visible_cell(self, logical_x: int, logical_y: int) -> bool:
        return (
            VISIBLE_OFFSET_COL <= logical_x < VISIBLE_OFFSET_COL + VISIBLE_GRID_W
            and VISIBLE_OFFSET_ROW <= logical_y < VISIBLE_OFFSET_ROW + VISIBLE_GRID_H
        )


if __name__ == "__main__":
    raise SystemExit("Import CheckMateGui from main.py instead of running gui_.py directly.")
