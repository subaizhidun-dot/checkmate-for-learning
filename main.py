"""CheckMate desktop interface for G1, G2 and G3.

The window only renders the board and forwards clicks.  All rules, phases,
transactions and turn resolution live in :mod:`gameengine`, which is shared
with the scripted agent interface in :mod:`agenthelper`.

Kept here on purpose:
- Pygame event handling and click hit-testing (gui.py regions).
- The victory-condition reveal animation, which is presentation only: the
  engine resolves the turn immediately (``resolve_checks_immediately=False``
  plus ``resolve_checks()`` when the animation finishes) and never waits for
  the frame loop to settle rules.
- Save-slot menu wiring for human saves in ``saves/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pygame

import ui_text
from basicgame import Interaction, can_select_resource, get_legal_moves_for_piece
from agenttools import AgentTools
from save_policy import AutosavePolicy
from llm_usage import LLMUsage
from local_settings import LocalSettings, default_profile
from settings_widgets import NUMERIC_SETTINGS, read_clipboard, write_clipboard
from agent_play import AgentPlayController, ConnectionTester
from agent_prompts import SYSTEM_PROMPT, NOTES_PROMPT, NG_PLUS_PROMPT, DEFAULT_PROMPTS
from note_review import NoteReviewer
from gameengine import (
    GameSession,
    PHASE_CHECKING_WIN,
    PHASE_CHOOSE_TOKEN,
    PHASE_GAME_OVER,
    PHASE_PENDING_ELEPHANT_BONUS,
    PHASE_PENDING_PIECE_LIMIT,
    PHASE_PENDING_REFUND,
    PHASE_PLAYING,
    PHASE_SELECT_COST,
    PHASE_SELECT_RESOURCE,
    PHASE_SELECT_TARGET,
    PHASE_TIME_WISH,
    monotonic_ms,
)
from gui import CheckMateGui, FPS, LEGAL_HINT_COLOR, LLM_LEGAL_HINT_COLOR
from sl_func import SAVES_DIR, list_save_slots, load_game, save_game


Position = tuple[int, int]
CONDITION_REVEAL_MS = 500


class AppState:
    """Thin adapter so gui.py can keep reading game data as before."""

    def __init__(self) -> None:
        self.session: GameSession | None = None
        self.menu_dialog = ui_text.STARTUP_DIALOG

    @property
    def game(self):
        return self.session.game if self.session is not None else None

    @property
    def players(self) -> dict[str, Any]:
        return self.session.players if self.session is not None else {}

    @property
    def interaction(self) -> Interaction:
        if self.session is None:
            interaction = Interaction()
            interaction.set_dialog_text(self.menu_dialog)
            return interaction
        return self.session.interaction

    @property
    def board_matrix(self) -> list[list[Any]]:
        return self.game.board_matrix if self.game is not None else []

    @property
    def gamemode(self) -> int | None:
        return self.game.gamemode if self.game is not None else None

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

    @property
    def condition_results(self) -> list[bool] | None:
        return self.session.condition_results if self.session is not None else None

    @property
    def condition_reveal_count(self) -> int:
        return self.session.condition_reveal_count if self.session is not None else 0

    @property
    def mate_loser(self) -> str | None:
        return self.session.mat_loser if self.session is not None else None

    @property
    def new_game_plus_available(self) -> bool:
        return self.session is not None and self.session.can_start_new_game_plus()

    def bind_runtime_links(self) -> None:
        if self.session is not None:
            self.session.bind_runtime_links()


class CheckMateApp:
    """Desktop loop: draw the board and forward input to the engine."""

    def __init__(self, settings_path: Path | None = None) -> None:
        self.state = AppState()
        self.gui = CheckMateGui()
        self.gui.open_menu = None
        self.checking_started_at = 0
        self.condition_animation_done = True
        self.save_policy = AutosavePolicy()
        self.save_slots = {}
        self.save_slots_dirty = True
        self.agent_tools = None
        self.agent_tools_by_side = {}
        self.setting_repeat = None
        self.local_settings = LocalSettings(settings_path)
        preferences = self.local_settings.load()
        self.gui.agent_play_mode = preferences["agent_play_mode"]
        self.gui.llm_color = preferences["llm_color"]
        self.gui.play_control_mode = preferences["play_control_mode"]
        self.gui.player_types = preferences["player_types"]
        self.gui.api_profiles = preferences["api_profiles"]
        self.gui.prompts = preferences["prompts"]
        self.agent_play = AgentPlayController(self)
        self.connection_tester = ConnectionTester(self)
        self.note_reviewer = NoteReviewer(self)
        self.configure_save_policy()
        if self.local_settings.error:
            self.state.menu_dialog = self.local_settings.error
        self.refresh_condition_animation()
        self.sync_play_controls()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self) -> None:
        running = True
        while running:
            self.gui.set_mode(self.state.game, self.state.new_game_plus_available)
            self.refresh_save_slots()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.note_reviewer.cancel()
                    self.agent_play.stop()
                    self.connection_tester.cancel()
                    self.autosave()
                    running = False
                    break
                if self.handle_setting_event(event):
                    continue
                if self.gui.handle_thinking_selection_event(event):
                    continue
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    self.note_reviewer.cancel()
                    self.agent_play.stop()
                    self.connection_tester.cancel()
                    self.autosave()
                    running = False
                    break
                if event.type in {pygame.VIDEORESIZE, pygame.WINDOWSIZECHANGED}:
                    self.gui.sync_window()
                if self.gui.handle_sidebar_event(event):
                    continue
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
                    self.cancel_current_action()
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self.handle_click(event.pos)

            self.update_victory_check()
            self.update_setting_repeat()
            self.note_reviewer.tick()
            self.agent_play.tick()
            self.connection_tester.tick()
            self.sync_play_controls()
            self.refresh_save_slots()
            self.gui.save_slots = self.save_slots
            self.gui.build_click_regions()
            mouse_pos = self.gui.to_logical_position(pygame.mouse.get_pos())
            self.update_tooltip(self.gui.find_region_name(mouse_pos) if mouse_pos is not None else None)
            board_targets, resource_targets, camp_targets = self.get_legal_targets()
            self.gui.draw(
                self.state,
                legal_board_targets=board_targets,
                legal_resource_targets=resource_targets,
                legal_camp_targets=camp_targets,
                tooltip_text=self.state.interaction.tooltip_text,
                save_slots=self.save_slots,
            )
            pygame.display.flip()
            self.gui.clock.tick(FPS)

        pygame.quit()

    def autosave(self) -> None:
        session = self.state.session
        if session is None or not self.save_policy.should_save(session):
            return
        save_game(
            session.game,
            session.players,
            flow=session.flow,
            interaction=session.interaction,
            autosave=True,
            session_id=session.session_id,
            revision=session.revision,
            agent_notes=session.agent_notes,
            llm_stats=session.llm_usage.to_dict(),
            turn_number=session.turn_number,
            game_over_reason=session.game_over_reason,
            finished=session.finished,
            message=session.pending_message,
        )
        self.save_policy.mark_saved(session)
        self.save_slots_dirty = True

    def persist(self) -> None:
        """Autosave unless the position is mid-check or already finished."""
        session = self.state.session
        if session is None:
            return
        if session.flow.phase == PHASE_CHECKING_WIN:
            return
        if session.is_over():
            # Finished games do not replace the last playable autosave.
            return
        self.autosave()

    def refresh_save_slots(self, force=False):
        if force or self.save_slots_dirty:
            self.save_slots = list_save_slots(SAVES_DIR)
            self.gui.save_slots = self.save_slots
            self.save_slots_dirty = False

    def handle_setting_event(self, event):
        gui, editor = self.gui, self.gui.setting_editor
        if gui.handle_scrollbar_event(event):
            return True
        if gui.thinking_turn_editing and gui.handle_thinking_turn_event(event):
            return True
        if event.type == pygame.WINDOWFOCUSLOST:
            self.setting_repeat = None
            editor.dragging = False
            if gui.drag_setting_slider:
                gui.drag_setting_slider = None
                self.apply_settings()
        if event.type == pygame.KEYUP and self.setting_repeat and event.key == self.setting_repeat[0]:
            self.setting_repeat = None
            return True
        if not gui.agent_play_mode:
            self.close_setting_editor()
            gui.setting_dropdown = None
        controls = gui.setting_controls()
        clicked = None
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            clicked = next((name for name, rect in controls.items() if rect.collidepoint(event.pos)), None)
            over_dropdown = any(rect.collidepoint(event.pos) for rect in gui.dropdown_controls().values())
            if clicked == "agent_play_mode" and not over_dropdown:
                self.close_setting_editor()
                gui.setting_dropdown = gui.drag_setting_slider = None
                gui.agent_play_mode = not gui.agent_play_mode
                self.apply_settings()
                return True
            if clicked in {"stop", "pause"}:
                gui.setting_dropdown = None
                self.handle_play_control(clicked)
                return True
        if gui.drag_setting_slider:
            if event.type == pygame.MOUSEMOTION:
                self.adjust_setting_slider(gui.drag_setting_slider, event.pos[0])
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.adjust_setting_slider(gui.drag_setting_slider, event.pos[0])
                gui.drag_setting_slider = None
                self.apply_settings()
            return True
        if gui.setting_dropdown:
            values = gui.dropdown_values(gui.setting_dropdown)
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    gui.setting_dropdown = None
                elif event.key in {pygame.K_UP, pygame.K_DOWN}:
                    gui.dropdown_index = (gui.dropdown_index + (-1 if event.key == pygame.K_UP else 1)) % len(values)
                elif event.key in {pygame.K_RETURN, pygame.K_KP_ENTER}:
                    self.select_setting_dropdown(values[gui.dropdown_index])
                return True
            if event.type == pygame.MOUSEMOTION:
                for index, rect in enumerate(gui.dropdown_controls().values()):
                    if rect.collidepoint(event.pos):
                        gui.dropdown_index = index
                        return True
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for value, rect in gui.dropdown_controls().items():
                    if rect.collidepoint(event.pos):
                        self.select_setting_dropdown(value)
                        return True
                previous = gui.setting_dropdown
                gui.setting_dropdown = None
                if clicked == previous:
                    return True
        if gui.active_setting:
            if event.type == pygame.TEXTINPUT:
                self.edit_setting_text(event.text)
                return True
            if event.type == pygame.KEYDOWN:
                modifiers = getattr(event, "mod", 0)
                self.handle_setting_key(event.key, modifiers)
                if gui.active_setting and event.key in {pygame.K_BACKSPACE, pygame.K_DELETE, pygame.K_LEFT,
                                                        pygame.K_RIGHT, pygame.K_HOME, pygame.K_END, pygame.K_UP, pygame.K_DOWN}:
                    self.setting_repeat = (event.key, modifiers, pygame.time.get_ticks() + 350)
                return True
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and editor.dragging:
                editor.dragging = False
                return True
            if event.type == pygame.MOUSEMOTION and editor.dragging:
                rect = self.active_setting_rect()
                if rect:
                    editor.set_cursor(self.setting_cursor_at(rect, event.pos), extend=True)
                return True
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and clicked == gui.active_setting:
                rect = self.active_setting_rect()
                editor.set_cursor(self.setting_cursor_at(rect, event.pos), bool(pygame.key.get_mods() & pygame.KMOD_SHIFT))
                editor.dragging = True
                return True
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        if clicked and clicked.startswith("default:"):
            self.close_setting_editor()
            _, side, key = clicked.split(":")
            gui.api_profiles[side][key] = default_profile()[key]
            self.apply_settings()
            return True
        if clicked and clicked.startswith("slider:") and gui.active_setting == "field:" + clicked[7:]:
            self.close_setting_editor()
        if clicked and clicked.startswith("copy_"):
            try:
                write_clipboard(gui.prompt_text(clicked.removeprefix("copy_") + "_text"))
                gui.settings_error = "Prompt copied."
            except (OSError, pygame.error, UnicodeError):
                gui.settings_error = "Clipboard unavailable; try again."
            return True
        if clicked and clicked.startswith("restore_"):
            name = clicked.removeprefix("restore_")
            # Discard only this prompt's pending edit; other editors keep their state.
            if gui.active_setting == name + "_text":
                self.close_setting_editor()
            gui.prompts[name] = DEFAULT_PROMPTS[name]
            self.save_local_settings()
            return True
        if gui.active_setting and not self.commit_setting():
            return True
        if clicked is None:
            return False
        if clicked == "next":
            self.handle_play_control(clicked)
        elif clicked in {"system_prompt", "notes_prompt", "ng_plus_prompt"}:
            attribute = clicked + "_expanded"
            setattr(gui, attribute, not getattr(gui, attribute))
        elif clicked in {"system_prompt_text", "notes_prompt_text", "ng_plus_prompt_text"}:
            self.focus_setting(clicked)
            editor.set_cursor(self.setting_cursor_at(self.active_setting_rect(), event.pos))
            editor.dragging = True
        elif gui.dropdown_values(clicked):
            gui.setting_dropdown = clicked
            gui.dropdown_index = gui.dropdown_values(clicked).index(self.setting_value(clicked))
        elif clicked.startswith("profile:"):
            side = clicked.split(":")[1]
            gui.profile_expanded[side] = not gui.profile_expanded[side]
        elif clicked.startswith("test:"):
            self.connection_tester.start(clicked.split(":")[1])
        elif clicked.startswith("slider:"):
            self.close_setting_editor()
            self.agent_play.stop()
            gui.drag_setting_slider = clicked
            self.adjust_setting_slider(clicked, event.pos[0])
            self.sync_play_controls()
        else:
            _, side, key = clicked.split(":")
            if key in {"vision", "streaming"}:
                gui.api_profiles[side][key] = not gui.api_profiles[side][key]
                self.apply_settings()
            else:
                self.focus_setting(clicked)
                editor.set_cursor(gui.setting_cursor_at(self.active_setting_rect(), event.pos[0]))
                editor.dragging = True
        return True

    def setting_value(self, name):
        if name in {"system_prompt_text", "notes_prompt_text", "ng_plus_prompt_text"}:
            return self.gui.prompts[name.removesuffix("_text")]
        if name == "play_control_mode":
            return self.gui.play_control_mode
        if name.startswith("player:"):
            return self.gui.player_types[name.split(":")[1]]
        _, side, key = name.split(":")
        return self.gui.api_profiles[side][key]

    def select_setting_dropdown(self, value):
        name = self.gui.setting_dropdown
        if value not in self.gui.dropdown_values(name):
            return
        if name == "play_control_mode":
            self.gui.play_control_mode = value
        elif name.startswith("player:"):
            self.gui.player_types[name.split(":")[1]] = value
        else:
            _, side, key = name.split(":")
            self.gui.api_profiles[side][key] = value
        self.gui.setting_dropdown = None
        self.apply_settings()

    def adjust_setting_slider(self, name, x):
        _, side, key = name.split(":")
        rect = self.gui.setting_controls().get(name)
        if rect is None:
            return
        low, high, step = NUMERIC_SETTINGS[key]
        fraction = max(0, min(1, (x - rect.left) / max(1, rect.w - 1)))
        value = low + round(fraction * (high - low) / step) * step
        self.gui.api_profiles[side][key] = round(value, 2) if key == "temperature" else int(value)

    def active_setting_rect(self):
        return next((self.gui.setting_input_rect(name, rect) for name, _, _, rect in self.gui.settings_layout()
                     if name == self.gui.active_setting), None)

    def setting_cursor_at(self, rect, position):
        if self.gui.setting_editor.multiline:
            return self.gui.prompt_cursor_at(rect, position)
        return self.gui.setting_cursor_at(rect, position[0])

    def focus_setting(self, name):
        self.gui.thinking_selection.focused = self.gui.thinking_selection.dragging = False
        self.gui.active_setting = name
        self.gui.setting_editor.multiline = name in {"system_prompt_text", "notes_prompt_text", "ng_plus_prompt_text"}
        value = self.setting_value(name)
        self.gui.setting_editor.load("" if value is None else str(value))
        self.gui.settings_error = ""
        self.setting_repeat = None
        pygame.key.start_text_input()
        pygame.key.set_text_input_rect(self.active_setting_rect())
        if self.gui.setting_editor.multiline:
            self.gui.settings_error = "Enter: newline | Ctrl+Enter: save | Esc: cancel"

    def close_setting_editor(self):
        self.gui.active_setting = None
        self.gui.setting_editor.dragging = False
        self.gui.settings_error = ""
        self.setting_repeat = None
        pygame.key.stop_text_input()

    def handle_setting_key(self, key, modifiers):
        editor = self.gui.setting_editor
        control, shift = bool(modifiers & pygame.KMOD_CTRL), bool(modifiers & pygame.KMOD_SHIFT)
        if key == pygame.K_ESCAPE:
            self.close_setting_editor()
        elif key in {pygame.K_RETURN, pygame.K_KP_ENTER}:
            if editor.multiline and not control:
                editor.insert("\n")
            else:
                self.commit_setting()
        elif key == pygame.K_TAB:
            current = self.gui.active_setting
            fields = [name for name, _, _ in self.gui.settings_rows() if name.startswith("field:")
                      and not self.gui.dropdown_values(name) and name.split(":")[-1] not in {"vision", "streaming"}]
            if self.commit_setting() and current in fields:
                target = fields[(fields.index(current) + (-1 if shift else 1)) % len(fields)]
                self.focus_setting(target)
                editor.select_all()
                rect = self.active_setting_rect()
                body = self.gui.settings_body()
                if rect.top < body.top + 22:
                    self.gui.settings_scroll -= body.top + 22 - rect.top
                elif rect.bottom > body.bottom:
                    self.gui.settings_scroll += rect.bottom - body.bottom
        elif control and key == pygame.K_a:
            editor.select_all()
        elif control and key in {pygame.K_c, pygame.K_x, pygame.K_v}:
            try:
                if key == pygame.K_v:
                    pasted = read_clipboard()
                    if pasted:
                        editor.insert(pasted)
                elif editor.selected_text():
                    write_clipboard(editor.selected_text())
                    if key == pygame.K_x:
                        editor.delete()
            except (OSError, pygame.error, UnicodeError):
                self.gui.settings_error = "Clipboard unavailable; try again."
        elif key in {pygame.K_BACKSPACE, pygame.K_DELETE}:
            editor.delete(backward=key == pygame.K_BACKSPACE, word=control)
        elif key in {pygame.K_LEFT, pygame.K_RIGHT}:
            editor.move(-1 if key == pygame.K_LEFT else 1, extend=shift, word=control)
        elif editor.multiline and key in {pygame.K_UP, pygame.K_DOWN, pygame.K_HOME, pygame.K_END}:
            rect = self.active_setting_rect()
            spans = self.gui.prompt_spans(editor.text, rect.w - 20)
            row = self.gui.prompt_cursor_row(spans)
            start, end = spans[row]
            if key in {pygame.K_HOME, pygame.K_END}:
                target = (0 if key == pygame.K_HOME else len(editor.text)) if control else (start if key == pygame.K_HOME else end)
            else:
                target_row = max(0, min(len(spans)-1, row + (-1 if key == pygame.K_UP else 1)))
                x = rect.x + 10 + self.gui.exposure_font.size(editor.text[start:editor.cursor])[0]
                y = rect.y + 10 + target_row * (self.gui.exposure_font.get_linesize() + 3)
                target = self.gui.prompt_cursor_at(rect, (x, y))
            editor.set_cursor(target, extend=shift)
        elif key in {pygame.K_HOME, pygame.K_END}:
            editor.set_cursor(0 if key == pygame.K_HOME else len(editor.text), extend=shift)
        self.gui.ensure_prompt_cursor_visible()

    def update_setting_repeat(self, now=None):
        if self.setting_repeat is None or not self.gui.active_setting:
            return
        now = pygame.time.get_ticks() if now is None else now
        key, modifiers, due = self.setting_repeat
        if now >= due:
            for _ in range(min(8, 1 + (now - due) // 35)):
                self.handle_setting_key(key, modifiers)
            self.setting_repeat = (key, modifiers, now + 35)

    def handle_play_control(self, name):
        if not self.gui.agent_play_mode:
            return
        if name == "stop":
            self.connection_tester.cancel(reset=False)
            self.note_reviewer.cancel()
        session = self.state.session
        if session is not None and session.flow.phase == PHASE_CHOOSE_TOKEN:
            self.set_dialog("Initial time-token placement is human only. Finish it before starting LLM play.")
        elif name != "next" or self.agent_play.side_to_play() is not None or self.note_reviewer.busy:
            getattr(self.agent_play, name)()
        self.sync_play_controls()

    def edit_setting_text(self, text):
        self.gui.setting_editor.insert(text)
        self.gui.ensure_prompt_cursor_visible()

    def commit_setting(self):
        if self.gui.setting_editor.multiline:
            name = self.gui.active_setting.removesuffix("_text")
            value = self.gui.setting_buffer
            if not value.strip():
                self.gui.settings_error = "Prompt cannot be empty; enter text or press Esc."
                return False
            self.gui.prompts[name] = value
            self.close_setting_editor()
            self.save_local_settings()
            return True
        _, side, field = self.gui.active_setting.split(":")
        value = self.gui.setting_buffer.strip()
        try:
            if field in {"max_tokens", "timeout"}:
                value = int(value)
                minimum, maximum, _ = NUMERIC_SETTINGS[field]
                if not minimum <= value <= maximum:
                    raise ValueError()
            elif field == "temperature":
                value = float(value) if value else None
                if value is not None and not 0 <= value <= 2:
                    raise ValueError()
        except ValueError:
            self.gui.settings_error = "Invalid value; correct it or press Esc."
            return False
        changed = self.gui.api_profiles[side][field] != value
        self.gui.api_profiles[side][field] = value
        self.close_setting_editor()
        if changed:
            self.apply_settings()
        return True

    def configure_save_policy(self):
        colors = tuple(side for side, kind in self.gui.player_types.items() if kind == "llm")
        self.save_policy.configure(self.gui.agent_play_mode and bool(colors), colors or ("white",))

    def apply_settings(self):
        self.note_reviewer.cancel()
        self.agent_play.stop()
        self.connection_tester.cancel()
        self.configure_save_policy()
        self.bind_agent_tools()
        if self.state.session is not None:
            self.state.session.refresh_selection_message()
            self.state.session.interaction.reset_tooltip_text("")
        self.save_local_settings()
        self.sync_play_controls()

    def save_local_settings(self):
        try:
            self.local_settings.save({key: getattr(self.gui, key) for key in (
                "agent_play_mode", "llm_color", "play_control_mode", "player_types", "api_profiles", "prompts")})
        except OSError:
            self.gui.settings_error = "Settings active; local save failed."

    def sync_play_controls(self):
        self.gui.legal_hint_color = (
            LLM_LEGAL_HINT_COLOR if self.agent_play.side_to_play() is not None else LEGAL_HINT_COLOR)
        self.gui.play_state = self.agent_play.state
        self.gui.play_message = self.agent_play.message
        self.gui.play_received = self.agent_play.received
        self.gui.play_paused = self.agent_play.paused
        self.gui.play_can_start = self.agent_play.side_to_play() is not None
        session = self.state.session
        if session and session.flow.phase == PHASE_CHOOSE_TOKEN:
            self.gui.play_message = "Initial token: human only"
        elif self.note_reviewer.busy and not self.agent_play.decision_running:
            self.gui.play_message = "Notes review paused" if self.agent_play.paused else "Updating condition notes"
            self.gui.play_can_start = self.agent_play.paused
        elif session and session.flow.phase == PHASE_TIME_WISH:
            self.gui.play_message = "Time wish: human only"

    def configure_agent_play_mode(self, enabled, llm_color="white"):
        if llm_color not in {"black", "white"}:
            raise ValueError("Invalid LLM side")
        self.gui.agent_play_mode = bool(enabled)
        self.gui.llm_color = llm_color
        self.gui.player_types = {side: "llm" if side == llm_color else "human" for side in ("black", "white")}
        self.apply_settings()

    def bind_agent_tools(self):
        session = self.state.session
        self.agent_tools_by_side = {}
        if session is None or not self.gui.agent_play_mode:
            self.agent_tools = None
            self.refresh_exposure_notes()
            return
        for side, kind in self.gui.player_types.items():
            if kind != "llm":
                continue
            tools = AgentTools(session, side, image_provider=self.gui.rule_image_payloads,
                               on_change=self.after_engine_change)
            tools.is_active = lambda s=session, t=tools, c=side: self.state.session is s and self.agent_tools_by_side.get(c) is t
            self.agent_tools_by_side[side] = tools
            tools.get_notes()
        if self.gui.llm_color not in self.agent_tools_by_side and self.agent_tools_by_side:
            self.gui.llm_color = next(iter(self.agent_tools_by_side))
        self.agent_tools = self.agent_tools_by_side.get(self.gui.llm_color)
        self.refresh_exposure_notes()

    def refresh_exposure_notes(self):
        session = self.state.session
        notes = session.agent_notes if session else {}
        winner = session.result()["winner"] if session else None
        summary = session.llm_usage.game_summary_text(winner) if winner is not None else ""
        self.gui.set_side_notes(notes, summary)

    def begin_llm_request(self, request_id, side):
        """API-controller hook: call at dispatch, retaining the returned game anchor."""
        session = self.state.session
        if session is None:
            raise ValueError("Start a game first")
        if session.flow.phase == PHASE_CHOOSE_TOKEN:
            raise ValueError("Initial time-token placement is human only")
        is_turn = (session.game.current_player == side and session.flow.phase not in {
            PHASE_CHOOSE_TOKEN, PHASE_CHECKING_WIN, PHASE_TIME_WISH, PHASE_GAME_OVER,
        })
        session.llm_usage.begin_request(request_id, side, session.turn_number if is_turn else None)
        return session

    def complete_llm_request(self, session, request_id, *, reasoning="", reply="", usage=None, status="completed", turn=None):
        """API-controller hook: receive output/usage before executing its plan."""
        if session is not self.state.session:
            return False
        if not session.llm_usage.finish_request(request_id, usage, status):
            return False
        self.show_llm_request(session, request_id, reasoning, reply, status, final=True, turn=turn)
        self.refresh_exposure_notes()
        return True

    def update_llm_request(self, session, request_id, *, reasoning="", reply="", turn=None):
        """Update one live request bubble without recording usage or saving."""
        if session is not self.state.session:
            return False
        try:
            if session.llm_usage.request_info(request_id)["status"] != "pending":
                return False
        except KeyError:
            return False
        self.show_llm_request(session, request_id, reasoning, reply, "receiving", final=False, turn=turn)
        return True

    def show_llm_request(self, session, request_id, reasoning, reply, status, *, final, turn=None):
        entry = session.llm_usage.request_info(request_id)
        number = session.llm_usage.requests.index(entry) + 1
        previous = next((message.get("turn") for message in self.gui.exposure_panes["thinking"].messages
                         if message.get("request_id") == request_id), None)
        display_turn = turn or entry.get("turn") or previous or session.turn_number
        display_status = ("Reply received" if status == "completed" and entry.get("purpose") != "notes"
                          else "Condition notes updated" if status == "completed" else status.capitalize())
        lines = [f"Request {number} | {entry['side'].capitalize()} | {display_status}"]
        lines.append(f"Turn {display_turn}")
        if entry.get("purpose") == "notes":
            lines.append("Condition Notes Review")
        if reasoning:
            lines.extend(["Reasoning", str(reasoning)])
        if reply:
            lines.extend(["Reply", str(reply)])
        if final:
            def token(key):
                return str(entry[key]) if entry[key] is not None else "Not reported"
            lines.append(f"Tokens: input {token('input_tokens')} | output {token('output_tokens')} | total {token('total_tokens')}")
        self.gui.append_exposure_output("\n".join(lines),
                                        color=(235, 74, 72) if status in {"failed", "stopped"} else None,
                                        side=entry["side"] if status in {"completed", "receiving"} else None,
                                        request_id=request_id, turn=display_turn)

    def report_tool_receipt(self, session, request_id, text, *, failed=False):
        if session is not self.state.session:
            return
        entry = session.llm_usage.request_info(request_id)
        self.gui.append_exposure_output(text, color=(235, 74, 72) if failed else None,
                                        turn=entry.get("turn") or session.turn_number)

    def report_agent_notice(self, text):
        session = self.state.session
        self.gui.append_exposure_output("Notice | " + text, color=(235, 74, 72),
                                        turn=session.turn_number if session else None)

    def call_agent_tool(self, name, arguments=None):
        """Main-thread entry for the future API controller, bound to this board."""
        if self.agent_tools is None:
            return {"ok": False, "code": "agent_mode_disabled"}
        result = self.agent_tools.call(name, arguments)
        if result.get("ok") and name == "update_notes":
            self.refresh_exposure_notes()
        return result

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def handle_click(self, pos: Position) -> None:
        pos = self.gui.to_logical_position(pos)
        if pos is None:
            return
        name = self.gui.find_region_name(pos)
        if name is None:
            self.gui.open_menu = None
            self.set_dialog(ui_text.NO_BUTTON_DIALOG)
            return

        if name.startswith("menu:"):
            self.handle_menu_click(name)
        elif self.agent_play.side_to_play() is not None and not name.startswith("condition:"):
            self.set_dialog("This side is controlled by the LLM. Use Set play controls.")
        elif self.is_checking_win():
            self.set_dialog(ui_text.CHECKING_WIN_DIALOG)
        elif name.startswith("camp:"):
            self.handle_camp_click(name.removeprefix("camp:"))
        elif name.startswith("board:"):
            logical_x, logical_y = (int(value) for value in name.removeprefix("board:").split(","))
            self.handle_board_click((logical_x, logical_y))
        elif name.startswith("resource:"):
            self.handle_resource_click(name.removeprefix("resource:"))
        elif name.startswith("condition:"):
            self.set_dialog(ui_text.CONDITION_PANEL_DIALOG)

    def set_dialog(self, text: str) -> None:
        """Show a message; before a game exists it is kept for the dialog box."""
        session = self.state.session
        if session is None:
            self.state.menu_dialog = text
            return
        session.set_message(text)

    def is_checking_win(self) -> bool:
        session = self.state.session
        return session is not None and session.flow.phase == PHASE_CHECKING_WIN

    def handle_menu_click(self, name: str) -> None:
        if name in {"menu:start", "menu:save", "menu:load"}:
            menu_name = name.removeprefix("menu:")
            self.gui.open_menu = None if self.gui.open_menu == menu_name else menu_name
            if menu_name in {"save", "load"} and self.gui.open_menu == menu_name:
                self.refresh_save_slots(force=True)
                self.gui.build_click_regions()
            messages = {
                "start": ui_text.START_MENU_DIALOG,
                "save": ui_text.SAVE_MENU_DIALOG,
                "load": ui_text.LOAD_MENU_DIALOG,
            }
            self.set_dialog(messages[menu_name])
            return

        self.gui.open_menu = None
        if name == "menu:start_g1":
            self.start_g1()
            return
        if name == "menu:start_g2":
            self.start_g2()
            return
        if name == "menu:start_g3":
            self.start_g3()
            return
        if name.startswith("menu:save_slot_"):
            self.save_slot(int(name.removeprefix("menu:save_slot_")))
            return
        if name.startswith("menu:load:"):
            self.load_from_file(name.removeprefix("menu:load:"))
            return
        if name.startswith("menu:disabled"):
            self.set_dialog(ui_text.NO_SAVE_FILE_DIALOG)

    def handle_camp_click(self, side: str) -> None:
        session = self.state.session
        if session is None or session.game is None:
            self.set_dialog(ui_text.START_OR_LOAD_DIALOG)
            return
        session.select_camp(side)
        self.after_engine_change()

    def handle_resource_click(self, resource_id: str) -> None:
        session = self.state.session
        if session is None or session.game is None:
            self.set_dialog(ui_text.START_OR_LOAD_DIALOG)
            return
        session.select_resource(resource_id)
        self.after_engine_change()

    def handle_board_click(self, position: Position) -> None:
        session = self.state.session
        if session is None or session.game is None:
            self.set_dialog(ui_text.START_OR_LOAD_DIALOG)
            return
        session.select_board(position)
        self.after_engine_change()

    def cancel_current_action(self) -> None:
        session = self.state.session
        if session is None:
            return
        if self.agent_play.side_to_play() is not None:
            return
        session.cancel_current_action()
        self.after_engine_change()

    def after_engine_change(self) -> None:
        """Autosave after a click that actually changed something."""
        self.gui.set_mode(self.state.game, self.state.new_game_plus_available)
        self.refresh_condition_animation()
        self.persist()
        self.refresh_exposure_notes()
        self.sync_play_controls()


    # ------------------------------------------------------------------
    # Game creation, saving and loading
    # ------------------------------------------------------------------
    def start_g1(self) -> None:
        self.start_game(1)

    def start_g2(self) -> None:
        self.start_game(2)

    def start_g3(self) -> None:
        self.start_game(3)

    def start_game(self, gamemode: int) -> None:
        self.note_reviewer.cancel()
        self.connection_tester.cancel()
        self.agent_play.stop(reset=True)
        self.state.session = GameSession.new_game(
            gamemode, previous_session=self.state.session, session_id="human",
            resolve_checks_immediately=False,
        )
        self.state.bind_runtime_links()
        self.gui.set_exposure_content(thinking="", notes="")
        self.gui.set_mode(self.state.game, self.state.new_game_plus_available)
        self.refresh_condition_animation()
        self.bind_agent_tools()
        self.sync_play_controls()

    def save_slot(self, slot: int) -> Path | None:
        session = self.state.session
        if session is None or session.game is None:
            self.set_dialog(ui_text.NO_GAME_TO_SAVE_DIALOG)
            return None
        path = save_game(
            session.game,
            session.players,
            flow=session.flow,
            interaction=session.interaction,
            slot=slot,
            session_id=session.session_id,
            revision=session.revision,
            game_over_reason=session.game_over_reason,
            finished=session.finished,
            message=session.pending_message,
            agent_notes=session.agent_notes,
            llm_stats=session.llm_usage.to_dict(),
            turn_number=session.turn_number,
        )
        self.save_slots_dirty = True
        self.set_dialog(ui_text.saved(path.name))
        return path

    def load_from_file(self, filename: str) -> None:
        path = SAVES_DIR / filename
        try:
            loaded = load_game(path)
        except FileNotFoundError:
            self.set_dialog(ui_text.NO_SAVE_FILE_DIALOG)
            return
        except ValueError as exc:
            self.set_dialog(ui_text.load_failed(exc))
            return

        session = GameSession(
            game=loaded["game"],
            players=loaded["players"],
            flow=loaded.get("flow_object"),
            interaction=loaded.get("interaction_object"),
            session_id="human",
            resolve_checks_immediately=False,
        )
        self.note_reviewer.cancel()
        self.agent_play.stop(reset=True)
        self.state.session = session
        self.connection_tester.cancel()
        self.state.bind_runtime_links()
        session.revision = int(loaded.get("revision") or 0)
        session.request_cache = {}
        session.action_log = []
        session.agent_notes = loaded.get("agent_notes") or {}
        session.llm_usage = LLMUsage.from_dict(loaded.get("llm_stats"))
        session.turn_number = max(1, int(loaded.get("turn_number") or 1))
        session.game_over_reason = loaded.get("game_over_reason")
        session.finished = bool(loaded.get("finished"))
        session.condition_results = session.flow.last_condition_results
        session.condition_reveal_count = (
            0 if session.flow.phase == PHASE_CHECKING_WIN
            else len(session.condition_results or [])
        )
        message = loaded.get("message") or session.interaction.dialog_text
        if message:
            session.set_message(message)
        elif not session.interaction.dialog_text:
            session.set_message(ui_text.loaded(path.name))
        self.restore_after_load()
        self.gui.set_exposure_content(thinking="", notes="")
        self.save_policy.mark_saved(session)
        self.save_slots_dirty = True
        self.bind_agent_tools()
        self.sync_play_controls()

    def restore_after_load(self) -> None:
        """Restore the presentation state for a freshly loaded session.

        The engine resolves pending victory checks and restores saved action
        phases; the window updates the presentation and new-game menu.
        """
        session = self.state.session
        if session is None:
            return
        session.normalize_after_load()
        self.gui.set_mode(self.state.game, self.state.new_game_plus_available)
        if session.flow.phase == PHASE_CHECKING_WIN:
            self.checking_started_at = monotonic_ms()
            self.condition_animation_done = False
            session.condition_reveal_count = 0
            session.set_message(ui_text.CHECKING_WIN_DIALOG)
            return
        self.checking_started_at = 0
        self.condition_animation_done = True
        if session.is_over():
            session.set_message(ui_text.NEW_GAME_PLUS_HINT if session.can_start_new_game_plus() else ui_text.GAME_OVER_DIALOG)
            return
        if session.flow.phase == PHASE_TIME_WISH:
            session.set_message(ui_text.time_wish_prompt(session.flow.time_wish_winner, session.game.time_token_owner))
        if session.game is not None and session.flow.phase == PHASE_CHOOSE_TOKEN:
            session.set_message(ui_text.LOADED_CHOOSE_TOKEN_DIALOG)

    def refresh_condition_animation(self) -> None:
        """Start the reveal animation only when the engine enters a check."""
        session = self.state.session
        if session is None:
            return
        if session.flow.phase == PHASE_CHECKING_WIN and self.condition_animation_done:
            self.checking_started_at = monotonic_ms()
            self.condition_animation_done = False
            session.condition_reveal_count = 0
            return
        if session.flow.phase != PHASE_CHECKING_WIN:
            self.condition_animation_done = True

    # ------------------------------------------------------------------
    # Victory-condition animation (presentation only)
    # ------------------------------------------------------------------
    def update_victory_check(self) -> None:
        session = self.state.session
        if session is None or session.flow.phase != PHASE_CHECKING_WIN:
            return
        results = session.flow.last_condition_results or []
        if not results:
            session.condition_reveal_count = 0
            session.resolve_checks()
            self.condition_animation_done = True
            self.persist()
            self.refresh_exposure_notes()
            return
        elapsed = monotonic_ms() - self.checking_started_at
        reveal_count = min(len(results), elapsed // CONDITION_REVEAL_MS)
        session.condition_reveal_count = int(reveal_count)
        session.set_message(ui_text.CHECKING_WIN_DIALOG)
        if elapsed >= (len(results) + 1) * CONDITION_REVEAL_MS:
            session.condition_reveal_count = len(results)
            session.resolve_checks()
            self.condition_animation_done = True
            self.persist()
            self.refresh_exposure_notes()


    # ------------------------------------------------------------------
    # Legal targets for the renderer
    # ------------------------------------------------------------------
    def get_legal_targets(self) -> tuple[set[Position], set[str], set[str]]:
        """Interface highlight targets, derived from the engine whitelist."""
        session = self.state.session
        if session is None or session.game is None:
            return set(), set(), set()
        game = session.game
        llm_turn = self.agent_play.side_to_play() is not None
        self.gui.legal_hint_color = LLM_LEGAL_HINT_COLOR if llm_turn else LEGAL_HINT_COLOR
        phase = session.flow.phase
        if phase == PHASE_CHOOSE_TOKEN:
            return set(), set(), {"black", "white"}
        if phase == PHASE_TIME_WISH:
            return set(), set(), {game.time_token_owner} if game.time_token_owner else set()
        if phase == PHASE_GAME_OVER or phase == PHASE_CHECKING_WIN:
            return set(), set(), set()
        actions = session.legal_actions()
        board_targets: set[Position] = set()
        camp_targets: set[str] = set()
        for action in actions:
            params = action["params"]
            if llm_turn and action["type"] in {
                    "place_squirrel", "buy_elephant", "buy_lion", "buy_mole", "buy_butterfly",
                    "sell_elephant", "sell_lion", "sell_mole", "sell_butterfly"} and params.get("at"):
                board_targets.add(tuple(params["at"]))
            if action["type"] in {"select_piece", "uproot_tree", "plant_tree", "butterfly_extra_turn",
                                  "remove_piece_limit", "place_elephant_bonus", "place_refund", "select_cost"} and params.get("at"):
                board_targets.add(tuple(params["at"]))
            elif phase == PHASE_SELECT_RESOURCE and action["type"] in {
                    "place_squirrel", "buy_elephant", "buy_lion", "buy_mole", "buy_butterfly"} and params.get("at"):
                board_targets.add(tuple(params["at"]))
            elif action["type"] == "move" and params.get("to"):
                board_targets.add(tuple(params["to"]))
            elif action["type"] == "move_time_token":
                side = params.get("side", game.current_player)
                if side in {"black", "white"}:
                    camp_targets.add(side)
        resource_targets: set[str] = set()
        if phase in {PHASE_PLAYING, PHASE_SELECT_TARGET}:
            for resource_id in ("squirrel", "elephant", "mole", "lion", "butterfly", "time_token"):
                if resource_id in {"squirrel", "time_token"} and phase != PHASE_PLAYING:
                    continue
                # A direct agent sale does not make an unselected reserve
                # button clickable. Match the human selection entry point.
                if can_select_resource(game, session.players, session.interaction, resource_id):
                    resource_targets.add(resource_id)
        return board_targets, resource_targets, camp_targets

    def positions_of(self, session: GameSession, action_types: set[str]) -> set[Position]:
        positions: set[Position] = set()
        for action in session.legal_actions():
            if action["type"] in action_types and action["params"].get("at"):
                positions.add(tuple(action["params"]["at"]))
        return positions

    def piece_limit_targets(self, owner: str | None) -> set[Position]:
        session = self.state.session
        if session is None or owner is None:
            return set()
        return {
            (x, y)
            for y, row in enumerate(session.game.board_matrix)
            for x, piece in enumerate(row)
            if (piece is not None and piece.owner == owner and piece.is_piece
                and not (piece.kind == "tree" and piece.rooted))
        }

    def elephant_bonus_targets(self) -> set[Position]:
        session = self.state.session
        if session is None:
            return set()
        position = session.interaction.elephant_start_position
        if position is None:
            return set()
        return self.positions_of(session, {"place_elephant_bonus"})

    # ------------------------------------------------------------------
    # Tooltips
    # ------------------------------------------------------------------
    def update_tooltip(self, hovered: str | None) -> None:
        session = self.state.session
        if session is None:
            return
        session.interaction.reset_tooltip_text(self.tooltip_for_region(hovered))

    def tooltip_for_region(self, hovered: str | None) -> str:
        session = self.state.session
        game = session.game if session is not None else None
        if hovered is None:
            return ""
        if hovered.startswith("condition:"):
            index = int(hovered.removeprefix("condition:"))
            result = None
            results = session.flow.last_condition_results if session is not None else None
            if results is not None and index < min(len(results), session.condition_reveal_count):
                result = results[index]
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
        session = self.state.session
        if session is None or session.game is None:
            return False
        game = session.game
        piece = self.get_piece(position)
        playerstate = session.players.get(game.current_player)
        return (
            piece is not None
            and piece.owner != game.current_player
            and piece.kind in {"squirrel", "elephant", "mole", "tree", "butterfly"}
            and playerstate is not None
            and playerstate.has_lion
            and game.current_ap >= 2
            and bool(get_legal_moves_for_piece(game, session.players, position))
        )

    def resource_tooltip_context(self) -> dict[str, Any]:
        session = self.state.session
        game = session.game if session is not None else None
        selected_pos = session.interaction.selected_pos if session is not None else None
        selected_piece = self.get_piece(selected_pos) if selected_pos is not None else None
        return {
            "selected_own_butterfly": (
                game is not None and selected_piece is not None
                and selected_piece.owner == game.current_player and selected_piece.kind == "butterfly"
            ),
            "selected_own_mole": (
                game is not None and selected_piece is not None
                and selected_piece.owner == game.current_player and selected_piece.kind == "mole"
            ),
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
            "time_token_owner": game.time_token_owner if game is not None else None,
            "any_player_over_piece_limit": (
                session is not None
                and any(player.num_pieces > 7 for player in session.players.values())
            ),
            "current_ap": game.current_ap if game is not None else 0,
        }

    def get_piece(self, position: Position | None) -> Any | None:
        session = self.state.session
        game = session.game if session is not None else None
        if game is None or position is None or not game.is_inside_board(position):
            return None
        return game.get_piece(position)


def main() -> None:
    import multiprocessing
    multiprocessing.freeze_support()
    CheckMateApp().run()


if __name__ == "__main__":
    main()
