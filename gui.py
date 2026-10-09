from __future__ import annotations

import random
import re
import base64
from io import BytesIO
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pygame
from basicgame import is_player_accessible
from game2 import SHIFTED_CORNERS
from local_settings import default_profile
from settings_widgets import LineEditor, NUMERIC_SETTINGS, read_clipboard
from agent_prompts import SYSTEM_PROMPT, NOTES_PROMPT, NG_PLUS_PROMPT, DEFAULT_PROMPTS


BASE_DIR = Path(__file__).resolve().parent
PIC_DIR = BASE_DIR / "pic"

WINDOW_W = 1200
WINDOW_H = 1000
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
BOARD_Y = 248

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
DIALOG_H = 68
DIALOG_X = (WINDOW_W - DIALOG_W) // 2
DIALOG_Y = BOARD_Y + BOARD_H + CELL + 24

BUTTON_W = CELL * 2
BUTTON_H = CELL
BUTTON_GAP = 28
BUTTON_COUNT = 5
BUTTON_ROW_W = BUTTON_W * BUTTON_COUNT + BUTTON_GAP * (BUTTON_COUNT - 1)
BUTTON_X = (WINDOW_W - BUTTON_ROW_W) // 2
BUTTON_Y = DIALOG_Y + DIALOG_H + 20

LEFT_CAMP_RECT = pygame.Rect(64, BOARD_Y - 14, CELL * 2, CELL * 2)
RIGHT_CAMP_RECT = pygame.Rect(WINDOW_W - 64 - CELL * 2, BOARD_Y - 14, CELL * 2, CELL * 2)
LEFT_STATUS_RECT = pygame.Rect(64, BOARD_Y + CELL * 3 + 20, CELL * 2, 128)
RIGHT_STATUS_RECT = pygame.Rect(WINDOW_W - 64 - CELL * 2, BOARD_Y + CELL * 3 + 20, CELL * 2, 128)

LEGAL_HINT_COLOR = (58, 220, 82)
LLM_LEGAL_HINT_COLOR = (65, 160, 255)
LEGAL_HINT_WIDTH = 5
LEGAL_HINT_INSET = 3
LEGAL_HINT_ARM = 20


@dataclass(frozen=True)
class ClickRegion:
    name: str
    rect: pygame.Rect


@dataclass
class Sidebar:
    title: str
    subtitle: str
    width: int
    expanded: bool = True


@dataclass
class ExposurePane:
    title: str
    placeholder: str
    text: str = ""
    scroll: int = 0
    colors: list = field(default_factory=list)
    messages: list = field(default_factory=list)


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
        self.sidebars = {
            "left": Sidebar("Set", "Configuration", 280),
            "right": Sidebar("LLM Play", "Exposure mode", 320),
        }
        self.exposure_panes = {
            "thinking": ExposurePane("Model Thinking", "Waiting for model output."),
            "notes": ExposurePane("Current Notes", "No condition notes yet."),
        }
        self.exposure_line_cache = {}
        self.chat_layout_cache = None
        self.thinking_turn = None
        self.notes_side = "black"
        self.notes_by_side = {}
        self.notes_summary = ""
        self.notes_scroll_positions = {}
        self.notes_page_initialized = False
        self.thinking_follow_latest = True
        self.thinking_scroll_positions = {}
        self.thinking_turn_editor = LineEditor()
        self.thinking_turn_editing = False
        self.thinking_turn_invalid = False
        self.drag_scrollbar = None
        self.scrollbar_drag_offset = 0
        self.agent_play_mode = False
        self.legal_hint_color = LEGAL_HINT_COLOR
        self.llm_color = "white"
        self.play_control_mode = "step"
        self.player_types = {"black": "human", "white": "llm"}
        self.api_profiles = {side: default_profile() for side in ("black", "white")}
        self.connection_status = {side: "idle" for side in ("black", "white")}
        self.profile_expanded = {"black": True, "white": True}
        self.settings_scroll = 0
        self.active_setting = None
        self.setting_editor = LineEditor()
        self.setting_dropdown = None
        self.dropdown_index = 0
        self.drag_setting_slider = None
        self.settings_error = ""
        self.prompts = dict(DEFAULT_PROMPTS)
        self.prompt_span_cache = {}
        self.system_prompt_expanded = True
        self.notes_prompt_expanded = True
        self.ng_plus_prompt_expanded = True
        self.system_prompt_line_cache = {}
        self.play_state = "idle"
        self.play_message = "Ready"
        self.play_can_start = False
        self.play_received = False
        self.play_paused = False
        self.rule_image_cache = {}
        self.drag_sidebar: str | None = None
        self.screen = pygame.Surface((WINDOW_W, WINDOW_H))
        self.window = pygame.display.set_mode(self.default_window_size(), pygame.RESIZABLE)
        self.center_window()
        self.native_window.maximize()
        pygame.event.pump()
        self.sync_window()
        self.clock = pygame.time.Clock()
        self.small_font = pygame.font.SysFont("consolas", 18)
        self.dialog_font = pygame.font.SysFont("microsoftyahei,simhei", 18)
        self.exposure_font = pygame.font.SysFont("microsoftyahei,simhei,notosanscjksc,wenquanyimicrohei", 16)
        self.menu_font = pygame.font.SysFont("consolas", 18, bold=True)
        self.button_font = pygame.font.SysFont("consolas", 20, bold=True)
        self.tooltip_font = pygame.font.SysFont("consolas", 16)

        self.images = self.load_images()
        self.background = self.create_wood_background()
        self.mode = 0
        self.new_game_plus_available = False
        self.condition_files = []
        self.condition_images: dict[str, pygame.Surface] = {}
        self.prepare_condition_images()

        self.resource_buttons = [(f"placeholder_{index}", "") for index in range(BUTTON_COUNT)]
        self.actionable_cells = {(x, y) for y in range(1, 6) for x in range(2, 7)}
        self.open_menu: str | None = None
        self.save_slots: dict[str, Path | None] = {}
        self.click_regions: list[ClickRegion] = []
        self.build_click_regions()

    @property
    def setting_buffer(self):
        return self.setting_editor.text

    @setting_buffer.setter
    def setting_buffer(self, value):
        self.setting_editor.load(value)

    @property
    def setting_cursor(self):
        return self.setting_editor.cursor

    @setting_cursor.setter
    def setting_cursor(self, value):
        self.setting_editor.cursor = max(0, min(len(self.setting_buffer), value))

    @property
    def setting_select_all(self):
        return self.setting_editor.selection == (0, len(self.setting_buffer)) and bool(self.setting_buffer)

    @setting_select_all.setter
    def setting_select_all(self, value):
        if value:
            self.setting_editor.select_all()
        else:
            self.setting_editor.anchor = None

    def set_mode(self, game, new_game_plus_available=False) -> None:
        self.new_game_plus_available = bool(new_game_plus_available)
        mode = game.gamemode if game is not None else 0
        if mode != self.mode:
            self.mode = mode
            self.condition_files = (
                ["g1_0.png"] + [f"g2_distance_{limit}" for limit in range(6, 0, -1)]
                if mode == 2 else ["g1_0.png", "g3_move", "g3_trade", "g3_start", "g3_end"]
                if mode == 3 else [f"g1_{index}.png" for index in range(5)] if mode == 1 else []
            )
            self.prepare_condition_images()
            special = ("mole", "Mole (2)") if mode == 2 else ("elephant", "Elephant (2)")
            self.resource_buttons = (
                [("squirrel", "Squirrel"), special, ("lion", "Lion (4)"),
                 ("butterfly", "Butterfly (4)") if mode == 3 else ("placeholder_3", ""),
                 ("time_token", "Time Token")]
                if mode else [(f"placeholder_{index}", "") for index in range(BUTTON_COUNT)]
            )
        self.actionable_cells = {
            (x, y) for y in range(7) for x in range(9)
            if (is_player_accessible(game, (x, y)) if game is not None else 2 <= x <= 6 and 1 <= y <= 5)
        }
        self.build_click_regions()

    def desktop_area(self) -> pygame.Rect:
        if pygame.display.get_driver() == "windows":
            import ctypes
            from ctypes import wintypes
            work_area = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
                return pygame.Rect(work_area.left, work_area.top,
                                   work_area.right - work_area.left, work_area.bottom - work_area.top)
        return pygame.Rect((0, 0), pygame.display.get_desktop_sizes()[0])

    def center_window(self) -> None:
        self.bind_native_window()
        area = self.desktop_area()
        window_w, window_h = self.window.get_size()
        self.native_window.position = (area.x + (area.w - window_w) // 2,
                                       area.y + (area.h - window_h) // 2)

    def bind_native_window(self) -> None:
        from pygame._sdl2.video import Window
        # SDL's event system stores a borrowed pointer to this wrapper.
        # Keep it alive for as long as the display window can receive events.
        self.native_window = Window.from_display_module()

    def default_window_size(self) -> tuple[int, int]:
        desktop_w, desktop_h = self.desktop_area().size
        return max(1, desktop_w - 32), max(1, desktop_h - 64)

    def sync_window(self) -> None:
        # Pygame 2 updates the display surface when the OS resizes the window.
        self.window = pygame.display.get_surface()
        self.update_viewport()

    def update_viewport(self) -> None:
        window_w, window_h = self.window.get_size()
        margin = min(8, max(0, window_w // 100), max(0, window_h // 100))
        gap = min(10, max(0, window_w // 100))
        widths = {
            side: min(bar.width, max(1, round(window_w * 0.28))) if bar.expanded else min(34, max(1, window_w // 12))
            for side, bar in self.sidebars.items()
        }
        self.sidebar_rects = {
            "left": pygame.Rect(margin, margin, widths["left"], max(1, window_h - margin * 2)),
            "right": pygame.Rect(window_w - margin - widths["right"], margin, widths["right"], max(1, window_h - margin * 2)),
        }
        self.sidebar_toggle_rects = {}
        self.sidebar_grip_rects = {}
        for side, rect in self.sidebar_rects.items():
            button_w = min(28, rect.w)
            button_x = rect.right - button_w - 4 if self.sidebars[side].expanded else rect.centerx - button_w // 2
            self.sidebar_toggle_rects[side] = pygame.Rect(button_x, rect.y + 6, button_w, min(28, rect.h))
            self.sidebar_grip_rects[side] = pygame.Rect(rect.right - 4 if side == "left" else rect.left - 2,
                                                       rect.y + 42, 6, max(1, rect.h - 42))
        left = self.sidebar_rects["left"].right + gap
        right = self.sidebar_rects["right"].left - gap
        self.game_area = pygame.Rect(left, margin, max(1, right - left), max(1, window_h - margin * 2))
        width, height = self.screen.get_size()
        scale = min(self.game_area.w / width, self.game_area.h / height)
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        self.viewport = pygame.Rect(self.game_area.centerx - size[0] // 2,
                                    self.game_area.centery - size[1] // 2, *size)

    def resize_window(self, size: tuple[int, int]) -> None:
        self.window = pygame.display.set_mode((max(1, size[0]), max(1, size[1])), pygame.RESIZABLE)
        self.bind_native_window()
        self.update_viewport()

    def handle_sidebar_event(self, event: pygame.event.Event) -> bool:
        if self.handle_scrollbar_event(event) or self.handle_thinking_turn_event(event) or self.handle_notes_page_event(event):
            return True
        if event.type == pygame.MOUSEWHEEL:
            position = getattr(event, "pos", pygame.mouse.get_pos())
            amount = -event.y if getattr(event, "flipped", False) else event.y
            return self.scroll_settings(position, -amount * 54) or self.scroll_exposure_pane(position, -amount * 3)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button in {4, 5}:
            return self.scroll_settings(event.pos, -54 if event.button == 4 else 54) or self.scroll_exposure_pane(event.pos, -3 if event.button == 4 else 3)
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.drag_sidebar is not None:
            self.drag_sidebar = None
            return True
        if event.type == pygame.MOUSEMOTION and self.drag_sidebar is not None:
            side = self.drag_sidebar
            rect = self.sidebar_rects[side]
            width = event.pos[0] - rect.left if side == "left" else rect.right - event.pos[0]
            maximum = max(1, min(440, round(self.window.get_width() * 0.28)))
            self.sidebars[side].width = max(min(180, maximum), min(maximum, width))
            self.update_viewport()
            return True
        if event.type != pygame.MOUSEBUTTONDOWN:
            return False
        for side, rect in self.sidebar_rects.items():
            if event.button == 1:
                if self.sidebar_toggle_rects[side].collidepoint(event.pos):
                    self.drag_scrollbar = None
                    self.end_thinking_turn_edit()
                    self.sidebars[side].expanded = not self.sidebars[side].expanded
                    self.update_viewport()
                    return True
                if self.sidebars[side].expanded and self.sidebar_grip_rects[side].collidepoint(event.pos):
                    self.drag_sidebar = side
                    return True
            if rect.collidepoint(event.pos):
                return True
        return False

    def to_logical_position(self, position: tuple[int, int]) -> tuple[int, int] | None:
        if not self.viewport.collidepoint(position):
            return None
        return ((position[0] - self.viewport.x) * self.screen.get_width() // self.viewport.w,
                (position[1] - self.viewport.y) * self.screen.get_height() // self.viewport.h)

    def to_window_position(self, position: tuple[int, int]) -> tuple[int, int]:
        return (self.viewport.x + round(position[0] * self.viewport.w / self.screen.get_width()),
                self.viewport.y + round(position[1] * self.viewport.h / self.screen.get_height()))

    def present(self) -> None:
        self.update_viewport()
        self.window.fill((35, 28, 24))
        self.window.blit(pygame.transform.smoothscale(self.screen, self.viewport.size), self.viewport)
        self.draw_sidebars()

    def draw_sidebars(self) -> None:
        for side, bar in self.sidebars.items():
            rect = self.sidebar_rects[side]
            pygame.draw.rect(self.window, (27, 29, 32), rect, border_radius=5)
            pygame.draw.rect(self.window, (100, 88, 70), rect, 1, border_radius=5)
            button = self.sidebar_toggle_rects[side]
            pygame.draw.rect(self.window, (67, 64, 57), button, border_radius=3)
            arrow = ("<" if side == "left" else ">") if bar.expanded else (">" if side == "left" else "<")
            label = self.menu_font.render(arrow, True, (233, 216, 181))
            self.window.blit(label, label.get_rect(center=button.center))
            if bar.expanded:
                title = self.menu_font.render(bar.title, True, (234, 222, 199))
                self.window.blit(title, (rect.x + 14, rect.y + 10))
                subtitle = self.tooltip_font.render(bar.subtitle, True, (171, 161, 140))
                self.window.blit(subtitle, (rect.x + 14, rect.y + 44))
                pygame.draw.line(self.window, (71, 67, 59), (rect.x + 12, rect.y + 76), (rect.right - 12, rect.y + 76))
                grip_x = rect.right - 2 if side == "left" else rect.left + 2
                center_y = rect.centery
                pygame.draw.line(self.window, (118, 106, 84), (grip_x, center_y - 20), (grip_x, center_y + 20), 2)
                if side == "right":
                    self.draw_exposure_panes()
                else:
                    self.draw_settings()
            else:
                label = self.tooltip_font.render("Set" if side == "left" else "LLM", True, (171, 161, 140))
                self.window.blit(label, label.get_rect(center=(rect.centerx, rect.y + 56)))

    def settings_body(self):
        rect = self.sidebar_rects["left"]
        reserved = 202 if self.agent_play_mode else 100
        return pygame.Rect(rect.x + 12, rect.y + 88, max(1, rect.w - 30), max(1, rect.h - reserved))

    def scrollbar_geometry(self, track, value, maximum, visible, total):
        if maximum <= 0:
            return None
        height = min(track.h, max(28, round(track.h * visible / total)))
        top = track.y + round((track.h - height) * max(0, min(maximum, value)) / maximum)
        return track, pygame.Rect(track.x, top, track.w, height), maximum

    def sidebar_scrollbars(self):
        bars = {}
        if self.agent_play_mode and self.sidebars["left"].expanded and self.setting_controls():
            body = self.settings_body()
            geometry = self.scrollbar_geometry(pygame.Rect(body.right + 3, body.y, 10, body.h),
                self.settings_scroll, max(0, self.settings_content_height - body.h), body.h, self.settings_content_height)
            if geometry:
                bars["settings"] = geometry
        rect = self.exposure_pane_rects().get("thinking")
        if rect:
            body = self.exposure_body(rect)
            visible = max(1, body.h // (self.exposure_font.get_linesize() + 3))
            count = self.exposure_line_count("thinking", body.w)
            geometry = self.scrollbar_geometry(pygame.Rect(rect.right - 15, body.y, 10, body.h),
                self.exposure_panes["thinking"].scroll, max(0, count - visible), visible, count)
            if geometry:
                bars["thinking"] = geometry
        return bars

    def drag_scrollbar_to(self, y):
        geometry = self.sidebar_scrollbars().get(self.drag_scrollbar)
        if geometry is None:
            self.drag_scrollbar = None
            return
        track, thumb, maximum = geometry
        travel = track.h - thumb.h
        top = max(0, min(travel, y - track.y - min(self.scrollbar_drag_offset, thumb.h)))
        value = round(maximum * top / travel) if travel else 0
        if self.drag_scrollbar == "settings":
            self.settings_scroll = value
        else:
            self.exposure_panes["thinking"].scroll = value

    def handle_scrollbar_event(self, event):
        if event.type == pygame.WINDOWFOCUSLOST:
            self.drag_scrollbar = None
        if self.drag_scrollbar:
            if event.type == pygame.MOUSEMOTION:
                self.drag_scrollbar_to(event.pos[1])
                return True
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.drag_scrollbar_to(event.pos[1])
                self.drag_scrollbar = None
                return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for name, (track, thumb, _) in self.sidebar_scrollbars().items():
                if track.collidepoint(event.pos):
                    self.drag_scrollbar = name
                    self.scrollbar_drag_offset = event.pos[1] - thumb.y if thumb.collidepoint(event.pos) else thumb.h // 2
                    self.drag_scrollbar_to(event.pos[1])
                    return True
        return False

    def draw_scrollbar(self, name, geometry):
        if geometry:
            track, thumb, _ = geometry
            pygame.draw.rect(self.window, (36, 37, 39), track, border_radius=5)
            pygame.draw.rect(self.window, (71, 67, 59), track, 1, border_radius=5)
            active = self.drag_scrollbar == name or thumb.collidepoint(pygame.mouse.get_pos())
            pygame.draw.rect(self.window, (213, 184, 99) if active else (151, 132, 91), thumb, border_radius=5)

    def settings_rows(self):
        rows = [("agent_play_mode", "Agent Play Mode", "On" if self.agent_play_mode else "Off")]
        if not self.agent_play_mode:
            return rows
        rows.append(("play_control_mode", "Play Control Mode", self.play_control_mode.capitalize()))
        labels = {"endpoint": "API Base URL", "api_key": "API Key", "model": "Model", "streaming": "Streaming",
                  "max_tokens": "Output Token Limit", "token_parameter": "Token Limit Parameter",
                  "timeout": "break when no AP cost (s)", "temperature": "Temperature",
                  "reasoning_effort": "Reasoning Effort", "vision": "Image Input"}
        for side in ("black", "white"):
            rows.append(("player:" + side, "Black / First Player" if side == "black" else "White / Second Player",
                         "LLM" if self.player_types[side] == "llm" else "Human"))
            if self.player_types[side] != "llm":
                continue
            rows.append(("profile:" + side, "API Configuration",
                         "Hide" if self.profile_expanded[side] else "Show"))
            if not self.profile_expanded[side]:
                continue
            for key, title in labels.items():
                value = self.api_profiles[side][key]
                if key == "api_key":
                    value = "*" * min(12, len(value)) if value else "Click to enter"
                elif key in {"vision", "streaming"}:
                    value = "On" if value else "Off"
                elif value is None or value == "":
                    value = "Click to enter" if key in {"endpoint", "model"} else "Default"
                rows.append((f"field:{side}:{key}", title, str(value)))
                if key == "streaming":
                    state = self.connection_status[side]
                    rows.append((f"test:{side}", "Model Connection", "Testing..." if state == "running" else "Test Connection"))
        rows.append(("system_prompt", "Action Prompt", "Hide" if self.system_prompt_expanded else "Show"))
        if self.system_prompt_expanded:
            rows.extend([("system_prompt_text", "", self.prompt_text("system_prompt_text")), ("copy_system_prompt", "", "Copy Text")])
        rows.append(("notes_prompt", "Notes Prompt", "Hide" if self.notes_prompt_expanded else "Show"))
        if self.notes_prompt_expanded:
            rows.extend([("notes_prompt_text", "", self.prompt_text("notes_prompt_text")), ("copy_notes_prompt", "", "Copy Text")])
        rows.append(("ng_plus_prompt", "NG+ Action Prompt", "Hide" if self.ng_plus_prompt_expanded else "Show"))
        if self.ng_plus_prompt_expanded:
            rows.extend([("ng_plus_prompt_text", "", self.prompt_text("ng_plus_prompt_text")), ("copy_ng_plus_prompt", "", "Copy Text")])
        return rows

    def system_prompt_lines(self, width, prompt=SYSTEM_PROMPT):
        key = (prompt, width)
        if key not in self.system_prompt_line_cache:
            # The existing prompt contains words short enough for a minimum
            # sidebar width; it needs wrapping without an ellipsis limit.
            lines = self.wrap_text(prompt, max(1, width), max_lines=1000, font=self.exposure_font)
            if len(self.system_prompt_line_cache) >= 8:
                self.system_prompt_line_cache.clear()
            self.system_prompt_line_cache[key] = lines
        return self.system_prompt_line_cache[key]

    def setting_row_height(self, name, body):
        if name in {"system_prompt_text", "notes_prompt_text", "ng_plus_prompt_text"}:
            line_height = self.exposure_font.get_linesize() + 3
            return max(3, len(self.prompt_spans(self.prompt_text(name), body.w - 20))) * line_height + 28
        if name in {"copy_system_prompt", "copy_notes_prompt", "copy_ng_plus_prompt"}:
            return 38
        return 82 if name.startswith("field:") and name.split(":")[-1] in NUMERIC_SETTINGS else 58

    def settings_layout(self):
        body = self.settings_body()
        rows = self.settings_rows()
        heights = [self.setting_row_height(name, body) for name, _, _ in rows]
        self.settings_content_height = sum(heights)
        maximum = max(0, self.settings_content_height - body.h)
        self.settings_scroll = max(0, min(maximum, self.settings_scroll))
        layout = []
        character_width = self.tooltip_font.size(" ")[0]
        top = body.y - self.settings_scroll
        for (name, title, value), height in zip(rows, heights):
            indent = character_width * 2 if name.startswith(("profile:", "field:", "test:")) else 0
            if name in {"system_prompt_text", "notes_prompt_text", "ng_plus_prompt_text"}:
                layout.append((name, title, value, pygame.Rect(body.x, top, body.w, height - 8)))
                top += height
                continue
            layout.append((name, title, value, pygame.Rect(
                body.x + indent, top + (22 if title else 0), body.w - indent, 30)))
            top += height
        return layout

    def setting_input_rect(self, name, rect):
        if name.startswith("field:") and name.split(":")[-1] in NUMERIC_SETTINGS:
            return pygame.Rect(rect.x, rect.y, max(24, rect.w - 76), rect.h)
        return rect

    def numeric_slider_rect(self, rect):
        return pygame.Rect(rect.x + 4, rect.bottom + 5, max(1, rect.w - 8), 16)

    def dropdown_values(self, name):
        if name == "play_control_mode":
            return ("step", "auto")
        if name.startswith("player:"):
            return ("human", "llm")
        if name.endswith(":token_parameter"):
            return ("max_tokens", "max_completion_tokens")
        if name.endswith(":reasoning_effort"):
            return ("default", "none", "low", "medium", "high")
        return ()

    def dropdown_controls(self):
        name = self.setting_dropdown
        if name is None or not self.agent_play_mode or not self.sidebars["left"].expanded:
            return {}
        layout = {key: rect for key, _, _, rect in self.settings_layout()}
        rect = layout.get(name)
        body = self.settings_body()
        if rect is None or not body.contains(rect):
            self.setting_dropdown = None
            return {}
        values = self.dropdown_values(name)
        top = rect.bottom + 2
        if top + len(values) * 28 > body.bottom:
            top = rect.y - len(values) * 28 - 2
        top = max(body.y, top)
        return {value: pygame.Rect(rect.x, top + index * 28, rect.w, 28).clip(body)
                for index, value in enumerate(values)}

    def setting_controls(self):
        bar = self.sidebar_rects["left"]
        if not self.sidebars["left"].expanded or bar.w < 100 or bar.h < 226:
            return {}
        body = self.settings_body()
        controls = {}
        for name, _, _, rect in self.settings_layout():
            item = self.setting_input_rect(name, rect)
            if item.colliderect(body):
                controls[name] = item.clip(body)
            if name.startswith("field:") and name.split(":")[-1] in NUMERIC_SETTINGS:
                slider = self.numeric_slider_rect(rect)
                if slider.colliderect(body):
                    controls["slider:" + name[6:]] = slider.clip(body)
            if name.startswith("field:") and name.split(":")[-1] in NUMERIC_SETTINGS and rect.colliderect(body):
                controls["default:" + name[6:]] = pygame.Rect(rect.right - 70, rect.y, 70, rect.h).clip(body)
        if not self.agent_play_mode:
            return controls
        width = (bar.w - 32) // 3
        for index, name in enumerate(("next", "pause", "stop")):
            controls[name] = pygame.Rect(bar.x + 12 + index * (width + 4), bar.bottom - 54, width, 42)
        return controls

    def scroll_settings(self, position, delta):
        rect = self.sidebar_rects["left"]
        if not rect.collidepoint(position):
            return False
        if self.sidebars["left"].expanded and self.settings_body().collidepoint(position):
            self.setting_dropdown = None
            self.settings_scroll += delta
            self.settings_layout()
        return True

    def fit_setting_text(self, text, width):
        while text and self.tooltip_font.size(text)[0] > width:
            text = text[:-1]
        return text

    def play_control_visuals(self, now=None):
        now = pygame.time.get_ticks() if now is None else now
        green, yellow, red, gray = (62, 218, 105), (242, 193, 66), (235, 74, 72), (139, 142, 145)
        blinking = self.play_state == "ready" and self.play_received
        return {
            "next": {"color": green if self.play_state == "running" or blinking else gray,
                     "visible": not blinking or now % 900 < 550, "icon": "play"},
            "pause": {"color": yellow if self.play_state == "pausing" else gray,
                      "visible": True, "icon": "pause"},
            "stop": {"color": red if self.play_state == "stopped" else gray, "visible": True, "icon": "stop"},
        }

    def draw_settings(self):
        controls = self.setting_controls()
        if not controls:
            return
        body = self.settings_body()
        original_clip = self.window.get_clip()
        self.window.set_clip(body)
        for name, title, value, rect in self.settings_layout():
            if not pygame.Rect(rect.x, rect.y - (22 if title else 0), rect.w,
                               rect.h + (22 if title else 0) + 24).colliderect(body):
                continue
            if name in {"system_prompt_text", "notes_prompt_text", "ng_plus_prompt_text"}:
                self.draw_prompt_editor(name, rect)
                continue
            if name.startswith(("profile:", "field:", "test:")):
                stem = body.x + self.tooltip_font.size(" ")[0] // 2
                pygame.draw.line(self.window, (171, 161, 140), (stem, rect.y - 20), (stem, rect.centery), 1)
                pygame.draw.line(self.window, (171, 161, 140), (stem, rect.centery), (rect.x - 4, rect.centery), 1)
                pygame.draw.polygon(self.window, (171, 161, 140), [
                    (rect.x - 3, rect.centery), (rect.x - 8, rect.centery - 3), (rect.x - 8, rect.centery + 3)])
            self.window.blit(self.tooltip_font.render(self.fit_setting_text(title, rect.w), True, (210, 203, 186)), (rect.x, rect.y - 22))
            full_rect = rect
            rect = self.setting_input_rect(name, rect)
            pygame.draw.rect(self.window, (58, 61, 65), rect, border_radius=3)
            if name == self.active_setting:
                self.draw_setting_editor(name, rect)
                pygame.draw.rect(self.window, (213, 184, 99), rect, 1, border_radius=3)
            else:
                reserved = 28 if self.dropdown_values(name) or name.startswith("test:") else 12
                value = self.fit_setting_text(value, rect.w - reserved)
                label = self.tooltip_font.render(value, True, (234, 222, 199))
                center = ((rect.centerx + 8, rect.centery) if name.startswith("test:") else
                          (rect.centerx - 6, rect.centery) if self.dropdown_values(name) else rect.center)
                self.window.blit(label, label.get_rect(center=center))
            if self.dropdown_values(name):
                pygame.draw.polygon(self.window, (234, 222, 199), [
                    (rect.right - 13, rect.centery - 2), (rect.right - 5, rect.centery - 2), (rect.right - 9, rect.centery + 3)])
            if name.startswith("test:"):
                status = self.connection_status[name.split(":")[1]]
                color = {"success": (62, 218, 105), "failed": (235, 74, 72),
                         "running": (242, 193, 66), "idle": (139, 142, 145)}[status]
                pygame.draw.circle(self.window, color, (rect.x + 12, rect.centery), 5)
            if name.startswith("field:") and name.split(":")[-1] in NUMERIC_SETTINGS:
                _, side, key = name.split(":")
                low, high, _ = NUMERIC_SETTINGS[key]
                number = self.api_profiles[side][key]
                slider = self.numeric_slider_rect(full_rect)
                enabled = number is not None
                fraction = max(0, min(1, (number - low) / (high - low))) if enabled else 0
                pygame.draw.line(self.window, (95, 96, 99), (slider.left, slider.centery), (slider.right - 1, slider.centery), 3)
                pygame.draw.circle(self.window, (213, 184, 99) if enabled else (115, 116, 119),
                                   (slider.left + round(fraction * (slider.w - 1)), slider.centery), 5)
            if name.startswith("field:") and name.split(":")[-1] in NUMERIC_SETTINGS:
                button = pygame.Rect(full_rect.right - 70, full_rect.y, 70, full_rect.h)
                pygame.draw.rect(self.window, (58, 61, 65), button, border_radius=3)
                label = self.tooltip_font.render("Default", True, (234, 222, 199))
                self.window.blit(label, label.get_rect(center=button.center))
        self.draw_setting_dropdown()
        self.window.set_clip(original_clip)
        if not self.agent_play_mode:
            return
        self.draw_scrollbar("settings", self.sidebar_scrollbars().get("settings"))
        bar = self.sidebar_rects["left"]
        pygame.draw.line(self.window, (71, 67, 59), (body.x, bar.bottom - 106), (body.right, bar.bottom - 106))
        message = self.settings_error or self.play_message
        self.window.blit(self.tooltip_font.render(self.fit_setting_text(message, body.w), True, (210, 203, 186)), (body.x, bar.bottom - 94))
        if not self.play_can_start and self.play_state in {"idle", "ready", "stopped"}:
            hint = "Place time token first" if self.mode and self.play_message == "Initial token: human only" else "Waiting for an LLM turn"
            self.window.blit(self.tooltip_font.render(self.fit_setting_text(hint, body.w), True, (171, 161, 140)), (body.x, bar.bottom - 74))
        for name, visual in self.play_control_visuals().items():
            rect = controls[name]
            pygame.draw.rect(self.window, (58, 61, 65), rect, border_radius=3)
            if name == "next" and not self.play_can_start and self.play_state != "running":
                color = (97, 99, 102)
            else:
                color = visual["color"]
            x, y = rect.centerx, rect.y + 11
            if visual["visible"]:
                if visual["icon"] == "play":
                    pygame.draw.polygon(self.window, color, [(x - 5, y - 6), (x - 5, y + 6), (x + 6, y)])
                elif visual["icon"] == "pause":
                    pygame.draw.rect(self.window, color, (x - 6, y - 6, 4, 12))
                    pygame.draw.rect(self.window, color, (x + 2, y - 6, 4, 12))
                else:
                    pygame.draw.rect(self.window, color, (x - 5, y - 5, 10, 10))
            label = self.tooltip_font.render(name.capitalize(), True, (234, 222, 199))
            self.window.blit(label, label.get_rect(center=(x, rect.bottom - 11)))

    def prompt_text(self, name):
        return self.setting_editor.text if self.active_setting == name else self.prompts[name.removesuffix("_text")]

    def prompt_spans(self, text, width):
        key = (text, width)
        if key not in self.prompt_span_cache:
            spans, start, pos, last_space = [], 0, 0, None
            while pos < len(text):
                if text[pos] == "\n":
                    spans.append((start, pos))
                    start, pos, last_space = pos + 1, pos + 1, None
                    continue
                if pos > start and self.exposure_font.size(text[start:pos + 1])[0] > width:
                    end = last_space + 1 if last_space is not None else pos
                    spans.append((start, end))
                    start, pos, last_space = end, end, None
                    continue
                if text[pos].isspace():
                    last_space = pos
                pos += 1
            spans.append((start, len(text)))
            if len(self.prompt_span_cache) >= 8:
                self.prompt_span_cache.clear()
            self.prompt_span_cache[key] = spans
        return self.prompt_span_cache[key]

    def prompt_cursor_row(self, spans):
        cursor = self.setting_editor.cursor
        return max(index for index, (start, end) in enumerate(spans) if start <= cursor)

    def prompt_cursor_at(self, rect, position):
        text = self.setting_editor.text
        spans = self.prompt_spans(text, rect.w - 20)
        row = max(0, min(len(spans) - 1, (position[1] - rect.y - 10) // (self.exposure_font.get_linesize() + 3)))
        start, end = spans[row]
        x = position[0] - rect.x - 10
        return min(range(start, end + 1), key=lambda i: abs(self.exposure_font.size(text[start:i])[0] - x))

    def ensure_prompt_cursor_visible(self):
        name = self.active_setting
        if name not in {"system_prompt_text", "notes_prompt_text", "ng_plus_prompt_text"}:
            return
        rect = next(rect for item, _, _, rect in self.settings_layout() if item == name)
        spans = self.prompt_spans(self.setting_editor.text, rect.w - 20)
        row = self.prompt_cursor_row(spans)
        height = self.exposure_font.get_linesize() + 3
        y = rect.y + 10 + row * height
        body = self.settings_body()
        if y < body.top:
            self.settings_scroll -= body.top - y
        elif y + height > body.bottom:
            self.settings_scroll += y + height - body.bottom
        start = spans[row][0]
        x = rect.x + 10 + self.exposure_font.size(self.setting_editor.text[start:self.setting_editor.cursor])[0]
        pygame.key.set_text_input_rect(pygame.Rect(x, max(body.top, min(y, body.bottom - height)), 2, height))

    def draw_prompt_editor(self, name, rect):
        active = self.active_setting == name
        text = self.prompt_text(name)
        spans = self.prompt_spans(text, rect.w - 20)
        height = self.exposure_font.get_linesize() + 3
        pygame.draw.rect(self.window, (21, 23, 26), rect, border_radius=4)
        pygame.draw.rect(self.window, (213, 184, 99) if active else (71, 67, 59), rect, 1, border_radius=4)
        original_clip = self.window.get_clip()
        self.window.set_clip(rect.inflate(-12, -12).clip(original_clip))
        selected_start, selected_end = self.setting_editor.selection if active else (0, 0)
        first = max(0, (original_clip.top - rect.y - 10) // height)
        last = min(len(spans), (original_clip.bottom - rect.y - 10) // height + 1)
        for row in range(first, last):
            start, end = spans[row]
            x, y = rect.x + 10, rect.y + 10 + row * height
            if selected_start < end and selected_end > start:
                left = self.exposure_font.size(text[start:max(start, selected_start)])[0]
                right = self.exposure_font.size(text[start:min(end, selected_end)])[0]
                pygame.draw.rect(self.window, (65, 93, 130), (x + left, y, max(2, right-left), height))
            self.window.blit(self.exposure_font.render(text[start:end], True, (224, 225, 229)), (x, y))
        if active:
            row = self.prompt_cursor_row(spans)
            x = rect.x + 10 + self.exposure_font.size(text[spans[row][0]:self.setting_editor.cursor])[0]
            y = rect.y + 10 + row * height
            if pygame.time.get_ticks() % 1000 < 650:
                pygame.draw.line(self.window, (245, 245, 245), (x, y), (x, y + height - 3))
        self.window.set_clip(original_clip)

    def draw_system_prompt_preview(self, rect, prompt=SYSTEM_PROMPT):
        pygame.draw.rect(self.window, (21, 23, 26), rect, border_radius=4)
        pygame.draw.rect(self.window, (71, 67, 59), rect, 1, border_radius=4)
        line_height = self.exposure_font.get_linesize() + 3
        lines = self.system_prompt_lines(rect.w - 20, prompt)
        original_clip = self.window.get_clip()
        self.window.set_clip(rect.inflate(-12, -12).clip(original_clip))
        first = max(0, (original_clip.top - rect.y - 10) // line_height)
        last = min(len(lines), (original_clip.bottom - rect.y - 10) // line_height + 1)
        for index in range(first, last):
            label = self.exposure_font.render(lines[index], True, (224, 225, 229))
            self.window.blit(label, (rect.x + 10, rect.y + 10 + index * line_height))
        self.window.set_clip(original_clip)

    def setting_editor_geometry(self, rect):
        editor, font = self.setting_editor, self.exposure_font
        value = "*" * len(editor.text) if self.active_setting.endswith(":api_key") else editor.text
        caret = font.size(value[:editor.cursor])[0]
        visible = max(1, rect.w - 12)
        if caret < editor.offset:
            editor.offset = caret
        elif caret > editor.offset + visible - 2:
            editor.offset = caret - visible + 2
        editor.offset = max(0, min(editor.offset, max(0, font.size(value)[0] - visible + 2)))
        return value, rect.x + 6 - editor.offset, caret

    def setting_cursor_at(self, rect, x):
        value, origin, _ = self.setting_editor_geometry(rect)
        position = x - origin
        widths = [self.exposure_font.size(value[:index])[0] for index in range(len(value) + 1)]
        return min(range(len(widths)), key=lambda index: abs(widths[index] - position))

    def draw_setting_editor(self, name, rect):
        value, origin, caret = self.setting_editor_geometry(rect)
        clip = self.window.get_clip()
        self.window.set_clip(rect.inflate(-8, -2).clip(clip))
        start, end = self.setting_editor.selection
        y = rect.centery - self.exposure_font.get_height() // 2
        if start != end:
            left = origin + self.exposure_font.size(value[:start])[0]
            width = self.exposure_font.size(value[:end])[0] - self.exposure_font.size(value[:start])[0]
            pygame.draw.rect(self.window, (72, 99, 131), (left, y, width, self.exposure_font.get_height()))
        self.window.blit(self.exposure_font.render(value, True, (234, 222, 199)), (origin, y))
        pygame.draw.line(self.window, (234, 222, 199), (origin + caret, rect.y + 5), (origin + caret, rect.bottom - 5))
        self.window.set_clip(clip)

    def draw_setting_dropdown(self):
        for index, (value, rect) in enumerate(self.dropdown_controls().items()):
            pygame.draw.rect(self.window, (72, 79, 89) if index == self.dropdown_index else (40, 43, 48), rect)
            pygame.draw.rect(self.window, (116, 108, 92), rect, 1)
            text = "LLM" if value == "llm" else value.capitalize() if self.setting_dropdown.startswith(("player:", "play_control")) else value
            self.window.blit(self.tooltip_font.render(self.fit_setting_text(text, rect.w - 12), True, (234, 222, 199)), (rect.x + 6, rect.y + 5))

    def rule_image_payloads(self):
        """Current displayed clue images as cached PNGs, with ordinal IDs only."""
        if self.mode not in self.rule_image_cache:
            cards = []
            for index, name in enumerate(self.condition_files, 1):
                buffer = BytesIO()
                pygame.image.save(self.condition_images[name], buffer, "clue.png")
                cards.append({"index": index, "mime_type": "image/png",
                              "data": base64.b64encode(buffer.getvalue()).decode("ascii")})
            self.rule_image_cache[self.mode] = cards
        return self.rule_image_cache[self.mode]

    def exposure_pane_rects(self):
        rect = self.sidebar_rects["right"]
        if not self.sidebars["right"].expanded or rect.w < 48 or rect.h < 220:
            return {}
        available = pygame.Rect(rect.x + 12, rect.y + 88, rect.w - 24, rect.h - 100)
        upper_height = round((available.h - 10) * 0.58)
        return {
            "thinking": pygame.Rect(available.x, available.y, available.w, upper_height),
            "notes": pygame.Rect(available.x, available.y + upper_height + 10,
                                 available.w, available.h - upper_height - 10),
        }

    def thinking_turns(self):
        return sorted({entry["turn"] for entry in self.exposure_panes["thinking"].messages
                       if type(entry.get("turn")) is int})

    def thinking_messages(self):
        messages = self.exposure_panes["thinking"].messages
        if self.thinking_turn is None:
            return messages
        turns = self.thinking_turns()
        latest = turns[-1] if turns else None
        return [entry for entry in messages if entry.get("turn") == self.thinking_turn
                or (entry.get("turn") is None and self.thinking_turn == latest)]

    def select_thinking_turn(self, turn):
        if type(turn) is not int or turn < 1:
            return False
        pane = self.exposure_panes["thinking"]
        self.thinking_scroll_positions[self.thinking_turn] = pane.scroll
        self.thinking_turn = turn
        turns = self.thinking_turns()
        self.thinking_follow_latest = bool(turns and turn == turns[-1])
        pane.scroll = self.thinking_scroll_positions.get(turn, 0)
        self.chat_layout_cache = None
        self.exposure_line_cache.pop("thinking", None)
        return True

    def thinking_turn_controls(self):
        rect = self.exposure_pane_rects().get("thinking")
        if rect is None or rect.w < 116:
            return {}
        field_width = 48 if rect.w >= 220 else 36
        right = pygame.Rect(rect.right - 10 - 22, rect.y + 5, 22, 24)
        field = pygame.Rect(right.x - 4 - field_width, right.y, field_width, right.h)
        left = pygame.Rect(field.x - 4 - 22, right.y, 22, right.h)
        return {"previous": left, "turn": field, "next": right}

    def adjacent_thinking_turn(self, direction):
        turns = self.thinking_turns()
        current = self.thinking_turn
        if current is None:
            return None
        candidates = [turn for turn in turns if (turn - current) * direction > 0]
        return (min(candidates) if direction > 0 else max(candidates)) if candidates else None

    def end_thinking_turn_edit(self):
        if self.thinking_turn_editing:
            pygame.key.stop_text_input()
        self.thinking_turn_editing = self.thinking_turn_invalid = False

    def insert_thinking_turn_text(self, text):
        if text and text.isascii() and text.isdecimal():
            editor = self.thinking_turn_editor
            start, end = editor.selection
            editor.insert(text[:max(0, 6 - len(editor.text) + end - start)])
            self.thinking_turn_invalid = False

    def handle_thinking_turn_event(self, event):
        controls = self.thinking_turn_controls()
        if event.type == pygame.WINDOWFOCUSLOST or not controls:
            self.end_thinking_turn_edit()
        if self.thinking_turn_editing:
            editor = self.thinking_turn_editor
            if event.type == pygame.TEXTINPUT:
                self.insert_thinking_turn_text(event.text)
                return True
            if event.type == pygame.KEYDOWN:
                modifiers = getattr(event, "mod", 0)
                control, shift = bool(modifiers & pygame.KMOD_CTRL), bool(modifiers & pygame.KMOD_SHIFT)
                if event.key in {pygame.K_RETURN, pygame.K_KP_ENTER}:
                    turn = int(editor.text) if editor.text else 0
                    if self.select_thinking_turn(turn):
                        self.end_thinking_turn_edit()
                    else:
                        self.thinking_turn_invalid = True
                elif event.key == pygame.K_ESCAPE:
                    self.end_thinking_turn_edit()
                elif control and event.key == pygame.K_a:
                    editor.select_all()
                elif control and event.key == pygame.K_v:
                    try:
                        self.insert_thinking_turn_text(read_clipboard().strip())
                    except (OSError, pygame.error, UnicodeError):
                        self.thinking_turn_invalid = True
                elif event.key in {pygame.K_BACKSPACE, pygame.K_DELETE}:
                    editor.delete(backward=event.key == pygame.K_BACKSPACE, word=control)
                    self.thinking_turn_invalid = False
                elif event.key in {pygame.K_LEFT, pygame.K_RIGHT}:
                    editor.move(-1 if event.key == pygame.K_LEFT else 1, extend=shift, word=control)
                elif event.key in {pygame.K_HOME, pygame.K_END}:
                    editor.set_cursor(0 if event.key == pygame.K_HOME else len(editor.text), extend=shift)
                return True
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self.end_thinking_turn_edit()
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for name, rect in controls.items():
                if rect.collidepoint(event.pos):
                    if name == "turn":
                        self.thinking_turn_editor.load(str(self.thinking_turn or ""))
                        self.thinking_turn_editor.select_all()
                        self.thinking_turn_editing = True
                        pygame.key.start_text_input()
                        pygame.key.set_text_input_rect(rect)
                    else:
                        turn = self.adjacent_thinking_turn(-1 if name == "previous" else 1)
                        if turn is not None:
                            self.select_thinking_turn(turn)
                    return True
        return False

    def draw_thinking_turn_controls(self, controls):
        for name in ("previous", "next"):
            rect = controls[name]
            direction = -1 if name == "previous" else 1
            enabled = self.adjacent_thinking_turn(direction) is not None
            pygame.draw.rect(self.window, (58, 61, 65), rect, border_radius=3)
            color = (234, 222, 199) if enabled else (104, 105, 108)
            x, y = rect.center
            pygame.draw.lines(self.window, color, False, [(x - direction * 3, y - 4),
                                                        (x + direction * 3, y), (x - direction * 3, y + 4)], 2)
        rect = controls["turn"]
        pygame.draw.rect(self.window, (27, 29, 32), rect, border_radius=3)
        border = ((235, 74, 72) if self.thinking_turn_invalid else (213, 184, 99)
                  if self.thinking_turn_editing else (110, 103, 88))
        pygame.draw.rect(self.window, border, rect, 1, border_radius=3)
        if not self.thinking_turn_editing:
            text = str(self.thinking_turn) if self.thinking_turn is not None else "–"
            label = self.tooltip_font.render(self.fit_setting_text(text, rect.w - 8), True, (234, 222, 199))
            self.window.blit(label, label.get_rect(center=rect.center))
            return
        editor, font = self.thinking_turn_editor, self.tooltip_font
        caret = font.size(editor.text[:editor.cursor])[0]
        editor.offset = max(0, min(editor.offset, caret))
        editor.offset = max(editor.offset, caret - (rect.w - 12))
        origin, y = rect.x + 5 - editor.offset, rect.centery - font.get_height() // 2
        clip = self.window.get_clip()
        self.window.set_clip(rect.inflate(-6, -4).clip(clip))
        start, end = editor.selection
        if start != end:
            pygame.draw.rect(self.window, (72, 99, 131), (origin + font.size(editor.text[:start])[0], y,
                font.size(editor.text[start:end])[0], font.get_height()))
        self.window.blit(font.render(editor.text, True, (234, 222, 199)), (origin, y))
        if pygame.time.get_ticks() % 1000 < 650:
            pygame.draw.line(self.window, (234, 222, 199), (origin + caret, rect.y + 4), (origin + caret, rect.bottom - 4))
        self.window.set_clip(clip)

    def set_exposure_content(self, thinking=None, notes=None):
        """Replace either pane's current text, keeping the other pane intact."""
        for name, value in (("thinking", thinking), ("notes", notes)):
            if value is not None:
                if name == "thinking":
                    self.thinking_turn = None
                    self.thinking_follow_latest = True
                    self.thinking_scroll_positions.clear()
                    self.end_thinking_turn_edit()
                    if self.drag_scrollbar == "thinking":
                        self.drag_scrollbar = None
                if name == "notes":
                    self.notes_side = "black"
                    self.notes_by_side = {}
                    self.notes_summary = ""
                    self.notes_scroll_positions = {}
                    self.notes_page_initialized = False
                self.exposure_panes[name].text = str(value)
                self.exposure_panes[name].scroll = 0
                self.exposure_panes[name].colors = []
                self.exposure_panes[name].messages = ([{"text": str(value), "side": None, "color": None}]
                                                       if name == "thinking" and value else [])
                self.chat_layout_cache = None
                self.exposure_line_cache.pop(name, None)

    def set_side_notes(self, notes, summary=""):
        """Refresh stored notes while preserving the selected side and reading position."""
        if not self.notes_page_initialized and any(notes.get(side) for side in ("black", "white")):
            self.notes_side = "black" if notes.get("black") else "white"
            self.notes_page_initialized = True
        self.notes_by_side = {side: notes.get(side, "") for side in ("black", "white")}
        self.notes_summary = summary
        self.refresh_notes_page()

    def refresh_notes_page(self):
        pane = self.exposure_panes["notes"]
        text = self.notes_by_side.get(self.notes_side, "")
        if self.notes_summary:
            text += ("\n\n" if text else "") + self.notes_summary
        if pane.text != text:
            pane.text = text
            pane.messages = []
            pane.colors = []
            self.exposure_line_cache.pop("notes", None)

    def select_notes_side(self, side):
        if side not in {"black", "white"}:
            return False
        pane = self.exposure_panes["notes"]
        self.notes_scroll_positions[self.notes_side] = pane.scroll
        self.notes_side = side
        self.notes_page_initialized = True
        pane.scroll = self.notes_scroll_positions.get(side, 0)
        self.refresh_notes_page()
        return True

    def notes_page_controls(self):
        rect = self.exposure_pane_rects().get("notes")
        if rect is None or rect.w < 116:
            return {}
        right = pygame.Rect(rect.right - 32, rect.y + 5, 22, 24)
        field = pygame.Rect(right.x - 60, right.y, 56, right.h)
        left = pygame.Rect(field.x - 26, right.y, 22, right.h)
        return {"previous": left, "side": field, "next": right}

    def handle_notes_page_event(self, event):
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        for name, rect in self.notes_page_controls().items():
            if rect.collidepoint(event.pos):
                if name == "previous":
                    self.select_notes_side("black")
                elif name == "next":
                    self.select_notes_side("white")
                return True
        return False

    def draw_notes_page_controls(self, controls):
        for name in ("previous", "next"):
            rect = controls[name]
            direction = -1 if name == "previous" else 1
            enabled = self.notes_side == ("white" if direction < 0 else "black")
            pygame.draw.rect(self.window, (58, 61, 65), rect, border_radius=3)
            color = (234, 222, 199) if enabled else (104, 105, 108)
            x, y = rect.center
            pygame.draw.lines(self.window, color, False, [(x - direction * 3, y - 4),
                                (x + direction * 3, y), (x - direction * 3, y + 4)], 2)
        rect = controls["side"]
        pygame.draw.rect(self.window, (27, 29, 32), rect, border_radius=3)
        pygame.draw.rect(self.window, (110, 103, 88), rect, 1, border_radius=3)
        label = self.tooltip_font.render(self.notes_side.capitalize(), True, (234, 222, 199))
        self.window.blit(label, label.get_rect(center=rect.center))

    def append_exposure_output(self, text, color=None, side=None, request_id=None, turn=None):
        """Keep this live game's received output; follow the tail only when at it."""
        pane = self.exposure_panes["thinking"]
        rect = self.exposure_pane_rects().get("thinking")
        follow_tail = rect is None
        if rect is not None:
            body = self.exposure_body(rect)
            visible = max(1, body.h // (self.exposure_font.get_linesize() + 3))
            follow_tail = pane.scroll >= max(0, self.exposure_line_count("thinking", body.w) - visible)
        entry = next((item for item in pane.messages if request_id is not None
                      and item.get("request_id") == request_id), None)
        if entry is None:
            entry = {"text": str(text), "side": side, "color": color}
            if request_id is not None:
                entry["request_id"] = request_id
            pane.messages.append(entry)
        else:
            entry.update(text=str(text), side=side, color=color)
        if type(turn) is int and turn > 0:
            entry["turn"] = turn
        turns = self.thinking_turns()
        if turns and self.thinking_follow_latest and self.thinking_turn != turns[-1]:
            self.select_thinking_turn(turns[-1])
            follow_tail = True
        pane.text = "\n\n".join(item["text"] for item in pane.messages)
        pane.colors, offset = [], 0
        for item in pane.messages:
            if item["color"] is not None:
                pane.colors.append((offset, offset + len(item["text"]), item["color"]))
            offset += len(item["text"]) + 2
        self.chat_layout_cache = None
        self.exposure_line_cache.pop("thinking", None)
        if follow_tail:
            pane.scroll = (max(0, self.exposure_line_count("thinking", body.w) - visible)
                           if rect is not None else 10 ** 12)

    def exposure_lines(self, name, width):
        pane = self.exposure_panes[name]
        styles = pane.colors
        if name == "thinking":
            messages = self.thinking_messages()
            text = "\n\n".join(entry["text"] for entry in messages)
            styles, offset = [], 0
            for entry in messages:
                if entry["color"] is not None:
                    styles.append((offset, offset + len(entry["text"]), entry["color"]))
                offset += len(entry["text"]) + 2
            text = text or (f"No model output for turn {self.thinking_turn}." if self.thinking_turn is not None else pane.placeholder)
        else:
            text = pane.text or pane.placeholder
        key = (text, width, tuple(styles))
        cached = self.exposure_line_cache.get(name)
        if cached is None or cached[0] != key:
            lines, colors = [], []
            offset = 0
            def append_line(value):
                lines.append(value)
                colors.append(color)
            for paragraph in text.split("\n"):
                color = next((color for start, end, color in styles if start <= offset < end), None)
                line = ""
                for token in re.findall(r"\s+|[A-Za-z0-9_]+|.", paragraph):
                    if self.exposure_font.size(line + token)[0] > width and line:
                        if token in "，。！？；：、）】》”’" and len(line) > 1 and not line[-1].isascii():
                            append_line(line[:-1].rstrip())
                            line = line[-1]
                        else:
                            append_line(line.rstrip())
                            line = ""
                        token = token.lstrip()
                    if self.exposure_font.size(token)[0] > width:
                        for char in token:
                            if line and self.exposure_font.size(line + char)[0] > width:
                                append_line(line)
                                line = ""
                            line += char
                    else:
                        line += token
                append_line(line.rstrip())
                offset += len(paragraph) + 1
            cached = (key, lines, colors)
            self.exposure_line_cache[name] = cached
        return cached[1]

    def exposure_body(self, rect):
        thinking = rect == self.exposure_pane_rects().get("thinking")
        return pygame.Rect(rect.x + 10, rect.y + 34, max(1, rect.w - (32 if thinking else 26)), max(1, rect.h - 44))

    def wrap_chat_suffix(self, text, width):
        lines, starts, offset = [], [], 0
        for paragraph in text.split("\n"):
            line, start = "", offset
            for match in re.finditer(r"\s+|[A-Za-z0-9_]+|.", paragraph):
                token, position = match.group(), offset + match.start()
                if line and self.exposure_font.size(line + token)[0] > width:
                    lines.append(line.rstrip())
                    starts.append(start)
                    stripped = token.lstrip()
                    start = position + len(token) - len(stripped)
                    line, token, position = "", stripped, start
                if self.exposure_font.size(line + token)[0] <= width:
                    line += token
                    continue
                for index, char in enumerate(token):
                    if line and self.exposure_font.size(line + char)[0] > width:
                        lines.append(line.rstrip())
                        starts.append(start)
                        line, start = "", position + index
                    line += char
            lines.append(line.rstrip())
            starts.append(start)
            offset += len(paragraph) + 1
        return lines, starts

    def chat_entry_lines(self, entry, width):
        """Reuse completed bubbles and rewrap only an appended message's tail."""
        text, cache = entry["text"], entry.get("_wrap_cache")
        if cache and cache["width"] == width:
            if text == cache["text"]:
                return cache["lines"]
            if text.startswith(cache["text"]):
                start = cache["starts"][-1]
                lines, starts = self.wrap_chat_suffix(text[start:], width)
                lines = cache["lines"][:-1] + lines
                starts = cache["starts"][:-1] + [start + position for position in starts]
            else:
                lines, starts = self.wrap_chat_suffix(text, width)
        else:
            lines, starts = self.wrap_chat_suffix(text, width)
        entry["_wrap_cache"] = {"text": text, "width": width, "lines": lines, "starts": starts}
        return lines

    def chat_layout(self, width):
        messages = self.thinking_messages()
        key = (width, tuple((entry["text"], entry["side"], entry["color"]) for entry in messages))
        if self.chat_layout_cache and self.chat_layout_cache[0] == key:
            return self.chat_layout_cache[1:]
        line_height = self.exposure_font.get_linesize() + 3
        top, layout = 0, []
        for entry in messages:
            side = entry["side"]
            inset = min(24, max(8, width // 10)) if side in {"black", "white"} else 0
            padding = 8 if inset else 0
            item_width = max(1, width - inset)
            wrap_width = max(1, item_width - padding * 2)
            lines = self.chat_entry_lines(entry, wrap_width)
            height = len(lines) * line_height + padding * 2
            layout.append({**entry, "rect": pygame.Rect(inset if side == "white" else 0, top, item_width, height),
                           "lines": lines, "padding": padding})
            top += height + 10
        count = (max(0, top - 10) + line_height - 1) // line_height
        self.chat_layout_cache = (key, layout, count)
        return layout, count

    def exposure_line_count(self, name, width):
        if name == "thinking" and self.thinking_messages():
            return self.chat_layout(width)[1]
        return len(self.exposure_lines(name, width))

    def draw_chat_messages(self, body, pane):
        line_height = self.exposure_font.get_linesize() + 3
        for entry in self.chat_layout(body.w)[0]:
            rect = entry["rect"].move(body.x, body.y - pane.scroll * line_height)
            if not rect.colliderect(body):
                continue
            side = entry["side"]
            if side in {"black", "white"}:
                background = (5, 5, 7) if side == "black" else (245, 245, 242)
                foreground = (245, 245, 245) if side == "black" else (20, 20, 22)
                pygame.draw.rect(self.window, background, rect, border_radius=8)
            else:
                foreground = entry["color"] or (224, 225, 229)
            padding = entry["padding"]
            first = max(0, (body.top - rect.y - padding) // line_height)
            last = min(len(entry["lines"]), (body.bottom - rect.y - padding) // line_height + 1)
            for index in range(first, last):
                label = self.exposure_font.render(entry["lines"][index], True, foreground)
                self.window.blit(label, (rect.x + padding, rect.y + padding + index * line_height))

    def scroll_exposure_pane(self, position, delta):
        for name, rect in self.exposure_pane_rects().items():
            if rect.collidepoint(position):
                body = self.exposure_body(rect)
                count = self.exposure_line_count(name, body.w)
                visible = max(1, body.h // (self.exposure_font.get_linesize() + 3))
                pane = self.exposure_panes[name]
                pane.scroll = max(0, min(max(0, count - visible), pane.scroll + delta))
                return True
        return self.sidebar_rects["right"].collidepoint(position)

    def draw_exposure_panes(self):
        line_height = self.exposure_font.get_linesize() + 3
        original_clip = self.window.get_clip()
        for name, rect in self.exposure_pane_rects().items():
            pane = self.exposure_panes[name]
            pygame.draw.rect(self.window, (21, 23, 26), rect, border_radius=4)
            pygame.draw.rect(self.window, (71, 67, 59), rect, 1, border_radius=4)
            self.window.set_clip(rect.clip(original_clip))
            navigation = self.thinking_turn_controls() if name == "thinking" else self.notes_page_controls()
            title_width = navigation["previous"].left - rect.x - 16 if navigation else rect.w - 20
            title = self.small_font.render(self.fit_text(pane.title, max(1, title_width), self.small_font), True, (225, 213, 191))
            self.window.blit(title, (rect.x + 10, rect.y + 8))
            if navigation:
                if name == "thinking":
                    self.draw_thinking_turn_controls(navigation)
                else:
                    self.draw_notes_page_controls(navigation)
            body = self.exposure_body(rect)
            has_messages = name == "thinking" and bool(self.thinking_messages())
            lines = [] if has_messages else self.exposure_lines(name, body.w)
            count = self.exposure_line_count(name, body.w)
            visible = max(1, body.h // line_height)
            pane.scroll = min(pane.scroll, max(0, count - visible))
            self.window.set_clip(body.clip(rect).clip(original_clip))
            color = (224, 225, 229) if pane.text else (144, 145, 148)
            if name == "notes":
                background = (5, 5, 7) if self.notes_side == "black" else (245, 245, 242)
                color = (245, 245, 245) if self.notes_side == "black" else (20, 20, 22)
                self.window.set_clip(rect.clip(original_clip))
                pygame.draw.rect(self.window, background, pygame.Rect(rect.x + 1, body.y - 2, rect.w - 2, rect.bottom - body.y + 1))
                self.window.set_clip(body.clip(rect).clip(original_clip))
            if has_messages:
                self.draw_chat_messages(body, pane)
            else:
                for index, line in enumerate(lines[pane.scroll:pane.scroll + visible]):
                    styled = self.exposure_line_cache[name][2][pane.scroll + index]
                    label = self.exposure_font.render(line, True, styled or color)
                    self.window.blit(label, (body.x, body.y + index * line_height))
            self.window.set_clip(original_clip)
            if name == "thinking":
                self.draw_scrollbar("thinking", self.sidebar_scrollbars().get("thinking"))
            elif count > visible:
                track = pygame.Rect(rect.right - 7, body.y, 3, body.h)
                height = min(track.h, max(12, round(track.h * visible / count)))
                top = track.y + round((track.h - height) * pane.scroll / (count - visible))
                pygame.draw.rect(self.window, (110, 103, 88), (track.x, top, track.w, height), border_radius=2)

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
            "mole.png",
            "butterfly.png",
            "tree_rooted.png",
            "tree_uprooted.png",
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
        for side in ("black", "white"):
            images[f"{side}_diagonal_cell"] = self.create_diagonal_piece_cell(side, images[f"{side}_cell"])
            images[f"{side}_eight_cell"] = self.create_eight_way_piece_cell(side, images[f"{side}_cell"])
        images["squirrel"] = pygame.transform.scale(images["squ.png"], (56, 56))
        images["acorn"] = pygame.transform.scale(images["pine.png"], (34, 34))
        images["lion_piece"] = pygame.transform.scale(images["lion.png"], (68, 68))
        images["elephant_piece"] = pygame.transform.scale(images["elephant.png"], (68, 68))
        images["mole_piece"] = pygame.transform.scale(images["mole.png"], (68, 68))
        butterfly = images["butterfly.png"]
        butterfly = butterfly.subsurface(butterfly.get_bounding_rect()).copy()
        images["butterfly_piece"] = pygame.transform.smoothscale(butterfly, (68, 68))
        for state in ("rooted", "uprooted"):
            original = images[f"tree_{state}.png"]
            scale = 68 / max(original.get_size())
            images[f"tree_{state}_piece"] = pygame.transform.scale(original, (round(original.get_width() * scale), round(original.get_height() * scale)))
        images["time_token_small"] = pygame.transform.scale(images["time_token_light.png"], (40, 60))
        images["time_token_light_panel"] = pygame.transform.scale(images["time_token_light.png"], (60, 90))
        images["time_token_dark_panel"] = pygame.transform.scale(images["time_token_dark.png"], (60, 90))
        return images

    def create_diagonal_piece_cell(self, side: str, base: pygame.Surface) -> pygame.Surface:
        """Draw a full square tile with arrows pointing toward its four corners."""
        surface = pygame.Surface((CELL, CELL), pygame.SRCALPHA)
        background = base.get_at((CELL // 2, CELL // 2))
        surface.fill((background.r, background.g, background.b, 255))
        arrow_color = (255, 255, 255) if side == "black" else (29, 29, 29)
        inset, arm = 5, 8
        for x, y, dx, dy in (
            (inset, inset, 1, 1),
            (CELL - 1 - inset, inset, -1, 1),
            (inset, CELL - 1 - inset, 1, -1),
            (CELL - 1 - inset, CELL - 1 - inset, -1, -1),
        ):
            pygame.draw.polygon(surface, arrow_color, [(x, y), (x + dx * arm, y), (x, y + dy * arm)])
        return surface

    def create_eight_way_piece_cell(self, side, base):
        surface = self.create_diagonal_piece_cell(side, base)
        color = (255, 255, 255) if side == "black" else (29, 29, 29)
        c, edge, arm = CELL // 2, CELL - 6, 8
        for points in (((c, 5), (c - arm, 13), (c + arm, 13)),
                       ((c, edge), (c - arm, edge - arm), (c + arm, edge - arm)),
                       ((5, c), (13, c - arm), (13, c + arm)),
                       ((edge, c), (edge - arm, c - arm), (edge - arm, c + arm))):
            pygame.draw.polygon(surface, color, points)
        return surface

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
            if name in self.condition_images:
                continue
            if name.startswith("g3_"):
                self.condition_images[name] = self.create_g3_condition_card(name)
                continue
            if name.startswith("g2_distance_"):
                card = pygame.Surface((CELL - 4, CELL - 4), pygame.SRCALPHA)
                card.fill((245, 242, 228))
                font = pygame.font.SysFont("consolas", 42, bold=True)
                label = font.render(name.rsplit('_', 1)[1], True, (25, 25, 25))
                card.blit(label, label.get_rect(center=card.get_rect().center))
                self.condition_images[name] = card
                continue
            cropped = self.crop_condition_content_square(self.images[name])
            scale = min(max_size / cropped.get_width(), max_size / cropped.get_height())
            size = (
                max(1, round(cropped.get_width() * scale)),
                max(1, round(cropped.get_height() * scale)),
            )
            self.condition_images[name] = pygame.transform.smoothscale(cropped, size)

    def create_g3_condition_card(self, name):
        from game3 import START_PATTERN, END_PATTERN
        card = pygame.Surface((CELL - 4, CELL - 4), pygame.SRCALPHA)
        card.fill((245, 242, 228))
        if name in {"g3_start", "g3_end"}:
            label = "Start" if name == "g3_start" else "End"
            text = self.small_font.render(label, True, (165, 48, 42))
            card.blit(text, text.get_rect(center=(42, 12)))
            for x, y in START_PATTERN if name == "g3_start" else END_PATTERN:
                rect = pygame.Rect(18 + x * 16, 29 + y * 16, 16, 16)
                pygame.draw.rect(card, (190, 15, 120), rect)
                pygame.draw.rect(card, (250, 230, 240), rect, 1)
        elif name == "g3_move":
            text = self.button_font.render("1", True, (182, 42, 36))
            card.blit(text, text.get_rect(center=(42, 17)))
            for x in (14, 54):
                pygame.draw.rect(card, (35, 35, 35), (x, 43, 17, 17), 2)
            pygame.draw.line(card, (35, 35, 35), (33, 51), (50, 51), 3)
            pygame.draw.polygon(card, (35, 35, 35), ((50, 51), (44, 46), (44, 56)))
        else:
            for index, key in enumerate(("squirrel", "elephant_piece", "lion_piece", "butterfly_piece")):
                icon = pygame.transform.smoothscale(self.images[key], (28, 28))
                card.blit(icon, (12 + index % 2 * 32, 13 + index // 2 * 32))
            pygame.draw.line(card, (185, 45, 35), (12, 12), (72, 72), 5)
        return card

    def crop_condition_content_square(self, surface: pygame.Surface) -> pygame.Surface:
        width, height = surface.get_size()
        content = pygame.mask.from_surface(surface, 10)
        pale_background = pygame.mask.from_threshold(surface, (255, 255, 255, 255), (71, 71, 71, 255))
        content.erase(pale_background, (0, 0))
        regions = content.get_bounding_rects()
        if not regions:
            return surface.copy()
        bounds = regions[0].unionall(regions[1:])
        padding = 5
        left = max(0, bounds.left - padding)
        right = min(width, bounds.right + padding)
        top = max(0, bounds.top - padding)
        bottom = min(height, bounds.bottom + padding)
        side = max(right - left, bottom - top)
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        square_left = max(0, min(width - side, center_x - side // 2))
        square_top = max(0, min(height - side, center_y - side // 2))
        return surface.subsurface(pygame.Rect(square_left, square_top, side, side)).copy()

    def build_click_regions(self) -> None:
        self.click_regions.clear()
        self.add_menu_click_regions()

        for logical_x, logical_y in sorted(self.actionable_cells) if self.mode else []:
            rect = self.rect_for_logical_cell(logical_x, logical_y)
            self.click_regions.append(ClickRegion(f"board:{logical_x},{logical_y}", rect))

        for index, (resource_id, _label) in enumerate(self.resource_buttons):
            if not self.mode or resource_id.startswith("placeholder_"):
                continue
            self.click_regions.append(ClickRegion(f"resource:{resource_id}", self.resource_button_rect(index)))

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
            self.click_regions.append(ClickRegion("menu:start_g2", self.dropdown_rect(1)))
            self.click_regions.append(ClickRegion("menu:start_g3", self.dropdown_rect(2)))
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
        self.set_mode(self.get_value(game, "game", game), self.get_value(game, "new_game_plus_available", False))
        self.build_click_regions()
        if mouse_pos is None:
            mouse_pos = self.to_logical_position(pygame.mouse.get_pos())
        hovered = self.find_region_name(mouse_pos) if mouse_pos is not None else None

        self.screen.blit(self.background, (0, 0))
        self.draw_conditions(game)
        self.draw_side_panels(game, hovered, legal_camp_targets)
        self.draw_board(game, hovered, legal_board_targets)
        self.draw_dialog(self.get_dialog_text(game))
        self.draw_resource_buttons(hovered, legal_resource_targets)
        self.draw_menu_bar(hovered)
        if tooltip_text and mouse_pos is not None:
            self.draw_tooltip(tooltip_text, mouse_pos)
        self.present()

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
        labels = [("start", "New Game+" if self.new_game_plus_available else "Start"), ("save", "Save"), ("load", "Load")]
        for index, (menu_id, label) in enumerate(labels):
            rect = self.menu_rect(index)
            enabled = hovered == f"menu:{menu_id}" or self.open_menu == menu_id
            self.draw_beveled_rect(rect, (214, 211, 204), enabled=enabled)
            rendered = self.menu_font.render(label, True, (12, 12, 12))
            self.screen.blit(rendered, rendered.get_rect(center=rect.center))

        if self.open_menu == "start":
            for index, label in enumerate(self.start_menu_labels()):
                self.draw_dropdown_row(index, label, hovered == f"menu:start_g{index + 1}", enabled=True)
        elif self.open_menu == "save":
            for index in range(3):
                name = f"menu:save_slot_{index + 1}"
                self.draw_dropdown_row(index, f"Slot {index + 1}", hovered == name, enabled=True)
        elif self.open_menu == "load":
            for index, (label, path) in enumerate(self.load_menu_items()):
                name = f"menu:load:{path.name}" if path is not None else f"menu:disabled_load_{index}"
                self.draw_dropdown_row(index, label, hovered == name, enabled=path is not None)

    def start_menu_labels(self):
        return [f"Start G{mode}{'+' if self.new_game_plus_available else ''}" for mode in (1, 2, 3)]

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

        model = self.get_value(game, "game", game)
        for visible_y in range(VISIBLE_GRID_H):
            for visible_x in range(VISIBLE_GRID_W):
                rect = pygame.Rect(BOARD_X + visible_x * CELL, BOARD_Y + visible_y * CELL, CELL, CELL)
                logical_x = VISIBLE_OFFSET_COL + visible_x
                logical_y = VISIBLE_OFFSET_ROW + visible_y
                if (logical_x, logical_y) in self.actionable_cells:
                    self.draw_cell(rect, self.piece_at(board, (logical_x, logical_y)))
                else:
                    pygame.draw.rect(self.screen, (45, 29, 27), rect)
                    pygame.draw.line(self.screen, (128, 55, 46), rect.topleft, rect.bottomright, 3)
                    pygame.draw.line(self.screen, (128, 55, 46), rect.topright, rect.bottomleft, 3)

        if self.mode == 2:
            for corner in model.expanded_corners:
                _, position = SHIFTED_CORNERS[corner]
                self.draw_cell(self.rect_for_logical_cell(*position), self.piece_at(board, position))
            for position in model.tree_markers:
                rect = self.rect_for_logical_cell(*position)
                if rect is not None and self.piece_at(board, position) is None:
                    inset = 7
                    diamond = [(rect.centerx, rect.top + inset), (rect.right - inset, rect.centery),
                               (rect.centerx, rect.bottom - inset), (rect.left + inset, rect.centery)]
                    pygame.draw.lines(self.screen, (231, 47, 44), True, diamond, 4)

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

        # Keep legality on the full board-cell border, above piece, hover and
        # selection artwork. Shifted corner cells use the same geometry.
        for position in legal_board_targets:
            rect = self.rect_for_logical_cell(*position)
            if rect is not None:
                self.draw_legal_hint_corners(rect)

        interaction = self.get_value(game, "interaction", None)
        for position in self.get_value(interaction, "selected_cost", []) or []:
            rect = self.rect_for_logical_cell(*position)
            if rect is not None and self.piece_at(board, position) is not None:
                points = [(rect.right - 34, rect.top + 23), (rect.right - 24, rect.top + 33),
                          (rect.right - 9, rect.top + 12)]
                pygame.draw.lines(self.screen, (17, 27, 18), False, points, 10)
                pygame.draw.lines(self.screen, LEGAL_HINT_COLOR, False, points, 6)

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
            self.draw_piece(rect, self.piece_owner(piece), self.piece_kind(piece), self.get_value(piece, "rooted", True))
        pygame.draw.rect(self.screen, (32, 32, 32), rect, 2)
        pygame.draw.line(self.screen, (255, 255, 255), rect.topleft, rect.topright, 1)
        pygame.draw.line(self.screen, (255, 255, 255), rect.topleft, rect.bottomleft, 1)

    def draw_piece(self, rect: pygame.Rect, side: str, kind: str, rooted: bool = True) -> None:
        if kind == "tree" and rooted:
            pygame.draw.rect(self.screen, (0, 0, 0) if side == "black" else (255, 255, 255), rect)
        else:
            cell_name = f"{side}_diagonal_cell" if kind == "tree" else f"{side}_cell"
            if kind == "butterfly":
                cell_name = f"{side}_eight_cell"
            self.screen.blit(self.images[cell_name], rect.topleft)
        image_key = {
            "squirrel": "squirrel",
            "acorn": "acorn",
            "pine": "acorn",
            "pinecone": "acorn",
            "lion": "lion_piece",
            "elephant": "elephant_piece",
            "mole": "mole_piece",
            "butterfly": "butterfly_piece",
            "tree": "tree_rooted_piece" if rooted else "tree_uprooted_piece",
        }.get(kind)
        if image_key is not None:
            image = self.images[image_key]
            self.screen.blit(image, image.get_rect(center=rect.center))

    def draw_dialog(self, message: str) -> None:
        rect = pygame.Rect(DIALOG_X, DIALOG_Y, DIALOG_W, DIALOG_H)
        pygame.draw.rect(self.screen, (10, 10, 10), rect)
        pygame.draw.rect(self.screen, (230, 230, 230), rect, 3)
        pygame.draw.rect(self.screen, (92, 92, 92), rect.inflate(-8, -8), 1)
        for index, line in enumerate(self.wrap_text(message, rect.width - 40, max_lines=3, font=self.dialog_font)):
            label = self.dialog_font.render(line, True, (235, 235, 225))
            self.screen.blit(label, (rect.x + 20, rect.y + 12 + index * 22))

    def resource_button_rect(self, index):
        x = BUTTON_X + index * (BUTTON_W + BUTTON_GAP)
        return pygame.Rect(x, BUTTON_Y, BUTTON_W, BUTTON_H)

    def draw_resource_buttons(self, hovered: str | None, legal_resource_targets: set[str]) -> None:
        for index, (resource_id, label) in enumerate(self.resource_buttons):
            rect = self.resource_button_rect(index)
            self.draw_beveled_rect(rect, (214, 211, 204), enabled=hovered == f"resource:{resource_id}")
            if resource_id.startswith("placeholder_"):
                text = self.button_font.render("?", True, (12, 12, 12))
                self.screen.blit(text, text.get_rect(center=rect.center))
                continue
            if not self.mode:
                continue
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
        if kind == "butterfly":
            cell_name = f"{side}_eight_cell"
        cell = pygame.transform.smoothscale(self.images[cell_name], (42, 42))
        self.screen.blit(cell, cell.get_rect(center=center))
        image_name = {"squirrel": "squ.png", "lion": "lion.png", "elephant": "elephant.png", "mole": "mole.png", "butterfly": "butterfly_piece"}.get(kind)
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
        x1 = rect.right - 1 - LEGAL_HINT_INSET
        y1 = rect.bottom - 1 - LEGAL_HINT_INSET
        arm = min(LEGAL_HINT_ARM, min(rect.w, rect.h) // 3)
        color = self.legal_hint_color
        width = LEGAL_HINT_WIDTH
        segments = [((x0, y0), (x0 + arm, y0)), ((x0, y0), (x0, y0 + arm)),
                    ((x1, y0), (x1 - arm, y0)), ((x1, y0), (x1, y0 + arm)),
                    ((x0, y1), (x0 + arm, y1)), ((x0, y1), (x0, y1 - arm)),
                    ((x1, y1), (x1 - arm, y1)), ((x1, y1), (x1, y1 - arm))]
        for stroke, size in (((17, 27, 18), width + 2), (color, width)):
            for start, end in segments:
                pygame.draw.line(self.screen, stroke, start, end, size)

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
        if (logical_x, logical_y) not in self.actionable_cells:
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
