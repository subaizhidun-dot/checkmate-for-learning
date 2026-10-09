"""Live desktop binding, cached slot queries and deferred autosaves."""
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gameengine import encode_action_id


class AgentSettingsGuiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy", "PYGAME_HIDE_SUPPORT_PROMPT": "1",
        })
        self.environment.start()
        from main import CheckMateApp
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.app = CheckMateApp(settings_path=self.path / "settings.json")
        self.paths = patch("main.SAVES_DIR", self.path)
        self.paths.start()
        self.save_paths = patch("sl_func.SAVES_DIR", self.path)
        self.save_paths.start()

    def tearDown(self):
        import pygame
        pygame.quit()
        self.save_paths.stop()
        self.paths.stop()
        self.directory.cleanup()
        self.environment.stop()

    def act(self, kind, **params):
        session = self.app.state.session
        result = session.submit(encode_action_id(kind, params), session.revision)
        self.app.after_engine_change()
        return result

    def start(self, mode=3):
        self.app.start_game(mode)
        self.app.state.session.resolve_checks_immediately = True
        self.act("choose_initial_token", side="black")

    def test_slot_queries_are_cached_until_change_or_use(self):
        with patch("main.list_save_slots", return_value={}) as query:
            for _ in range(120):
                self.app.refresh_save_slots()
            self.assertEqual(query.call_count, 1)
            self.app.handle_menu_click("menu:load")
            self.assertEqual(query.call_count, 2)
            self.app.refresh_save_slots()
            self.assertEqual(query.call_count, 2)
            self.app.save_slots_dirty = True
            self.app.refresh_save_slots()
            self.assertEqual(query.call_count, 3)

    def test_setting_controls_use_set_and_switch_mode_and_side(self):
        import pygame
        self.assertEqual(self.app.gui.sidebars["left"].title, "Set")
        self.start()
        controls = self.app.gui.setting_controls()
        self.assertTrue(self.app.handle_setting_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1, pos=controls["agent_play_mode"].center)))
        self.assertTrue(self.app.gui.agent_play_mode)
        self.assertEqual(self.app.agent_tools.color, "white")
        old = self.app.agent_tools
        for name, value in (("player:white", "human"), ("player:black", "llm")):
            controls = self.app.gui.setting_controls()
            self.app.handle_setting_event(pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, button=1, pos=controls[name].center))
            self.app.handle_setting_event(pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, button=1, pos=self.app.gui.dropdown_controls()[value].center))
        self.assertEqual(self.app.agent_tools.color, "black")
        self.assertEqual(old.call("get_public_state")["code"], "stale_game")
        self.app.gui.draw_sidebars()

    def test_settings_survive_restart_independently_of_game_saves(self):
        from main import CheckMateApp
        self.app.configure_agent_play_mode(True, "black")
        self.start()
        self.assertIsNotNone(self.app.save_slot(1))
        settings = json.loads((self.path / "settings.json").read_text())
        self.assertTrue(settings["agent_play_mode"])
        self.assertEqual(settings["llm_color"], "black")
        for save in self.path.glob("*.json"):
            if save.name == "settings.json":
                continue
            data = json.loads(save.read_text())
            self.assertNotIn("agent_play_mode", data)
            self.assertNotIn("llm_color", data)
        self.app = CheckMateApp(settings_path=self.path / "settings.json")
        self.assertTrue(self.app.gui.agent_play_mode)
        self.assertEqual(self.app.gui.llm_color, "black")
        self.assertTrue(self.app.save_policy.agent_play_mode)
        self.assertEqual(self.app.save_policy.llm_colors, frozenset({"black"}))
        self.assertIsNone(self.app.state.session)

    def test_rule_images_are_current_png_cards_without_internal_names(self):
        self.app.configure_agent_play_mode(True, "black")
        for mode, count in ((1, 5), (2, 7), (3, 5)):
            self.app.start_game(mode)
            response = self.app.call_agent_tool("get_rule_images")
            self.assertEqual(response["gamemode"], mode)
            self.assertEqual(len(response["images"]), count)
            for card in response["images"]:
                self.assertEqual(set(card), {"index", "mime_type", "data"})
                self.assertTrue(base64.b64decode(card["data"]).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_agent_moves_current_board_and_saves_only_on_human_return(self):
        self.start()
        self.app.configure_agent_play_mode(True, "white")
        self.act("fast_time_token")
        session = self.app.state.session
        self.assertEqual(session.game.current_player, "white")
        with patch("main.save_game", wraps=__import__("sl_func").save_game) as write:
            for kind, params in (("select_piece", {"at": [6, 1]}),
                                 ("move", {"from": [6, 1], "to": [5, 1]})):
                result = self.app.call_agent_tool("apply_action", {
                    "action_id": encode_action_id(kind, params), "revision": session.revision,
                })
                self.assertTrue(result["ok"])
            self.assertIsNotNone(self.app.state.game.get_piece((5, 1)))
            self.app.autosave()  # Closing during the LLM turn also preserves the checkpoint.
            self.assertEqual(write.call_count, 0)
            self.app.call_agent_tool("apply_action", {"action_id": "fast_time_token", "revision": session.revision})
            self.assertEqual(session.game.current_player, "black")
            self.assertEqual(write.call_count, 1)
            self.app.persist()
            self.assertEqual(write.call_count, 1)
            self.assertTrue(self.app.save_slots_dirty)

    def test_new_game_invalidates_previous_tools_and_notes_restore(self):
        self.start()
        self.app.configure_agent_play_mode(True, "white")
        previous = self.app.agent_tools
        self.app.call_agent_tool("update_notes", {"text": "My saved hypothesis"})
        path = self.app.save_slot(1)
        self.app.start_game(2)
        self.assertEqual(previous.call("get_gi_rules")["code"], "stale_game")
        self.app.load_from_file(path.name)
        self.assertEqual(self.app.call_agent_tool("get_notes")["notes"], "My saved hypothesis")
        self.assertIn("My saved hypothesis", self.app.gui.exposure_panes["notes"].text)
        self.assertEqual(self.app.state.session.action_log, [])

    def test_human_checkpoint_waits_for_visible_check_animation(self):
        self.start()
        self.app.configure_agent_play_mode(True, "white")
        self.act("fast_time_token")
        session = self.app.state.session
        session.resolve_checks_immediately = False
        with patch("main.save_game") as write:
            self.app.call_agent_tool("apply_action", {"action_id": "fast_time_token", "revision": session.revision})
            self.assertEqual(session.flow.phase, "checking_win")
            self.assertEqual(write.call_count, 0)
            self.assertEqual(self.app.call_agent_tool("get_public_state")["visible_condition_feedback"], [])
            with patch("main.monotonic_ms", return_value=self.app.checking_started_at + 9999):
                self.app.update_victory_check()
            self.assertEqual(session.game.current_player, "black")
            self.assertEqual(session.flow.phase, "playing")
            self.assertEqual(write.call_count, 1)

    def test_received_output_accumulates_in_memory_and_only_usage_is_saved(self):
        self.start()
        self.app.configure_agent_play_mode(True, "black")
        for request_id, total in (("r1", 100), ("r2", 200)):
            anchor = self.app.begin_llm_request(request_id, "black")
            self.assertTrue(self.app.complete_llm_request(
                anchor, request_id, reasoning=f"Received reasoning {request_id}", reply=f"Received reply {request_id}",
                usage={"input_tokens": total - 10, "output_tokens": 10}))
        upper = self.app.gui.exposure_panes["thinking"].text
        for text in ("Received reasoning r1", "Received reply r1", "Received reasoning r2", "total 200"):
            self.assertIn(text, upper)
        self.app.configure_agent_play_mode(False, "black")
        self.assertEqual(self.app.gui.exposure_panes["thinking"].text, upper)
        path = self.app.save_slot(1)
        contents = path.read_text()
        data = json.loads(contents)
        self.assertNotIn("Received reasoning", contents)
        self.assertNotIn("Received reply", contents)
        self.assertNotIn("action_log", data)
        self.assertEqual(data["llm_stats"]["total"]["requests"], 2)
        self.assertEqual(data["llm_stats"]["total"]["llm_turns"], 1)
        self.assertEqual(data["llm_stats"]["total"]["average_tokens_per_request"], 150)
        self.app.load_from_file(path.name)
        self.assertEqual(self.app.gui.exposure_panes["thinking"].text, "")
        self.assertEqual(self.app.state.session.llm_usage.summary()["total_tokens"], 300)

    def test_winner_summary_is_program_owned_under_notes(self):
        self.start()
        self.app.configure_agent_play_mode(True, "black")
        anchor = self.app.begin_llm_request("r1", "black")
        self.app.complete_llm_request(anchor, "r1", usage={"input_tokens": 40, "output_tokens": 10})
        self.assertNotIn("Game Summary", self.app.gui.exposure_panes["notes"].text)
        self.app.state.session.end_session("mate", loser="white")
        self.app.after_engine_change()
        self.app.call_agent_tool("update_notes", {"text": "My final notes"})
        notes = self.app.gui.exposure_panes["notes"].text
        self.assertIn("My final notes\n\nGame Summary", notes)
        self.assertIn("Winner: Black", notes)
        self.assertIn("Average/request: 50.0", notes)

    def test_request_duplicate_and_late_game_response_are_ignored(self):
        self.start()
        anchor = self.app.begin_llm_request("r1", "black")
        self.assertTrue(self.app.complete_llm_request(anchor, "r1", reply="one reply", usage={"total_tokens": 10}))
        self.assertFalse(self.app.complete_llm_request(anchor, "r1", reply="duplicate", usage={"total_tokens": 10}))
        self.assertNotIn("duplicate", self.app.gui.exposure_panes["thinking"].text)
        self.app.start_game(2)
        self.assertFalse(self.app.complete_llm_request(anchor, "r1", reply="stale", usage={"total_tokens": 10}))
        self.assertEqual(self.app.gui.exposure_panes["thinking"].text, "")
        self.assertEqual(self.app.state.session.llm_usage.summary()["requests"], 0)

    def test_received_history_keeps_reader_scroll_position(self):
        pane = self.app.gui.exposure_panes["thinking"]
        self.app.gui.append_exposure_output("\n".join(f"Line {index}" for index in range(100)))
        self.assertGreater(pane.scroll, 0)
        body = self.app.gui.exposure_pane_rects()["thinking"]
        self.app.gui.scroll_exposure_pane(body.center, -6)
        old_scroll = pane.scroll
        self.app.gui.append_exposure_output("A newly received reply")
        self.assertEqual(pane.scroll, old_scroll)

    def test_control_icons_run_blink_pause_and_stop(self):
        self.app.gui.play_state = "running"
        self.assertEqual(self.app.gui.play_control_visuals(0)["next"]["color"], (62, 218, 105))
        self.app.gui.play_state = "ready"
        self.app.gui.play_received = True
        self.assertTrue(self.app.gui.play_control_visuals(100)["next"]["visible"])
        self.assertFalse(self.app.gui.play_control_visuals(700)["next"]["visible"])
        self.app.gui.play_state = "pausing"
        self.assertEqual(self.app.gui.play_control_visuals(0)["pause"]["color"], (242, 193, 66))
        self.assertEqual(self.app.gui.play_control_visuals(0)["pause"]["icon"], "pause")
        self.app.gui.play_state = "stopped"
        self.app.gui.play_paused = True
        self.assertEqual(self.app.gui.play_control_visuals(0)["pause"]["icon"], "pause")
        self.assertEqual(self.app.gui.play_control_visuals(0)["pause"]["color"], (139, 142, 145))
        self.assertEqual(self.app.gui.play_control_visuals(0)["stop"]["color"], (235, 74, 72))

    def test_configuration_scrolling_keeps_controls_fixed_and_fields_editable(self):
        import pygame
        gui = self.app.gui
        self.app.configure_agent_play_mode(True, "black")
        self.app.gui.player_types["white"] = "llm"
        self.app.apply_settings()
        initial = {name: rect.copy() for name, rect in gui.setting_controls().items() if name in {"next", "pause", "stop"}}
        gui.scroll_settings(gui.settings_body().center, 400)
        for name, rect in initial.items():
            self.assertEqual(gui.setting_controls()[name], rect)
        layout = {name: rect for name, _, _, rect in gui.settings_layout()}
        # Bring the first model field into the scroll body.
        gui.settings_scroll += layout["field:black:model"].y - gui.settings_body().y - 60
        field = gui.setting_controls()["field:black:model"]
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=field.center))
        self.app.handle_setting_event(pygame.event.Event(pygame.TEXTINPUT, text="custom-model"))
        self.app.handle_setting_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0))
        self.assertEqual(gui.api_profiles["black"]["model"], "custom-model")
        data = json.loads((self.path / "settings.json").read_text())
        self.assertEqual(data["api_profiles"]["black"]["model"], "custom-model")
        gui.draw_sidebars()

    def test_set_runtime_is_locked_until_human_places_time_token(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        self.app.start_game(2)
        self.assertFalse(self.app.gui.play_can_start)
        with patch.object(self.app.agent_play, "request_factory") as request:
            button = self.app.gui.setting_controls()["next"]
            self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=button.center))
            request.assert_not_called()
        with self.assertRaises(ValueError):
            self.app.begin_llm_request("forbidden-initial", "black")
        self.assertEqual(self.app.agent_tools.get_legal_actions()["actions"], [])
        self.act("choose_initial_token", side="black")
        self.assertTrue(self.app.gui.play_can_start)
        self.assertTrue(self.app.agent_tools.get_legal_actions()["actions"])

    def test_stop_remains_immediate_while_editing_an_invalid_field(self):
        import pygame
        from unittest.mock import Mock
        self.app.configure_agent_play_mode(True, "black")
        self.start()
        controller = self.app.agent_play
        controller.request_id = "active-fixture"
        controller.anchor = self.app.begin_llm_request(controller.request_id, "black")
        pending_request = Mock()
        controller.request = pending_request
        controller.state = "running"
        self.app.gui.active_setting = "field:black:timeout"
        self.app.gui.setting_buffer = "invalid number"
        button = self.app.gui.setting_controls()["stop"]
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=button.center))
        pending_request.cancel.assert_called_once()
        self.assertEqual(controller.state, "stopped")
        self.assertEqual(self.app.gui.play_state, "stopped")
        self.assertEqual(controller.anchor.llm_usage.requests[0]["status"], "stopped")

    def test_agent_mode_off_only_shows_master_switch_and_disables_other_controls(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        self.start()
        old_controls = self.app.gui.setting_controls()
        previous_profile = dict(self.app.gui.api_profiles["black"])
        self.app.gui.active_setting = "field:black:timeout"
        self.app.gui.setting_buffer = "invalid number"
        self.app.handle_setting_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1, pos=old_controls["agent_play_mode"].center))
        self.assertFalse(self.app.gui.agent_play_mode)
        self.assertEqual(self.app.gui.settings_rows(), [("agent_play_mode", "Agent Play Mode", "Off")])
        self.assertEqual(set(self.app.gui.setting_controls()), {"agent_play_mode"})
        self.assertIsNone(self.app.gui.active_setting)
        with patch.object(self.app.agent_play, "next") as run:
            self.app.handle_play_control("next")
            self.app.handle_setting_event(pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, button=1, pos=old_controls["next"].center))
            run.assert_not_called()
        self.assertEqual(self.app.gui.api_profiles["black"], previous_profile)
        self.assertEqual(self.app.agent_tools_by_side, {})
        self.app.gui.draw_sidebars()

    def test_each_players_api_rows_follow_that_player_with_two_character_indent(self):
        self.app.gui.agent_play_mode = True
        self.app.gui.player_types = {"black": "llm", "white": "llm"}
        rows = self.app.gui.settings_layout()
        names = [name for name, _, _, _ in rows]
        self.assertEqual(names[:3], ["agent_play_mode", "play_control_mode", "player:black"])
        self.assertEqual(names[names.index("player:black") + 1], "profile:black")
        self.assertEqual(names[names.index("player:white") + 1], "profile:white")
        black_fields = [name for name in names if name.startswith("field:black:")]
        self.assertTrue(all(names.index(name) < names.index("player:white") for name in black_fields))
        rectangles = {name: rect for name, _, _, rect in rows}
        indent = self.app.gui.tooltip_font.size(" ")[0] * 2
        for side in ("black", "white"):
            parent = rectangles["player:" + side]
            for name, child in rectangles.items():
                if name == "profile:" + side or name.startswith("field:" + side + ":"):
                    self.assertEqual(child.x, parent.x + indent)
                    self.assertEqual(child.right, parent.right)

    def show_setting(self, name):
        gui = self.app.gui
        layout = {key: rect for key, _, _, rect in gui.settings_layout()}
        gui.settings_scroll += layout[name].y - gui.settings_body().y - 52
        gui.settings_layout()
        return gui.setting_controls()[name]

    def key_setting(self, key, mod=0):
        import pygame
        self.app.handle_setting_event(pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod))

    def test_clipboard_shortcuts_preserve_unicode_and_partial_selection(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        self.show_setting("field:black:endpoint")
        self.app.focus_setting("field:black:endpoint")
        self.app.gui.setting_buffer = "https://old.example/v1"
        editor = self.app.gui.setting_editor
        editor.set_cursor(8)
        editor.set_cursor(11, extend=True)
        with patch("main.write_clipboard") as write, patch("main.read_clipboard", return_value="新地址🦋"):
            self.key_setting(pygame.K_c, pygame.KMOD_CTRL)
            write.assert_called_once_with("old")
            self.key_setting(pygame.K_x, pygame.KMOD_CTRL)
            self.assertEqual(editor.text, "https://.example/v1")
            self.key_setting(pygame.K_v, pygame.KMOD_CTRL)
            self.assertEqual(editor.text, "https://新地址🦋.example/v1")
            self.key_setting(pygame.K_a, pygame.KMOD_CTRL)
            self.assertEqual(editor.selected_text(), editor.text)
        self.app.gui.draw_sidebars()

    def test_shift_selection_mouse_drag_and_held_backspace(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        rect = self.show_setting("field:black:model")
        self.app.focus_setting("field:black:model")
        editor = self.app.gui.setting_editor
        editor.load("abcdefghij")
        self.key_setting(pygame.K_HOME)
        self.key_setting(pygame.K_RIGHT, pygame.KMOD_SHIFT)
        self.key_setting(pygame.K_RIGHT, pygame.KMOD_SHIFT)
        self.assertEqual(editor.selected_text(), "ab")
        editor.load("abcdefghij")
        editor.set_cursor(0)
        origin = self.app.gui.setting_editor_geometry(rect)[1]
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(origin, rect.centery)))
        x = origin + self.app.gui.exposure_font.size("abcde")[0]
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(x, rect.centery)))
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(x, rect.centery)))
        self.assertEqual(editor.selected_text(), "abcde")
        editor.load("abcdefghij")
        self.key_setting(pygame.K_BACKSPACE)
        due = self.app.setting_repeat[2]
        self.app.update_setting_repeat(now=due + 105)
        self.assertEqual(editor.text, "abcde")
        self.app.handle_setting_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_BACKSPACE))
        self.app.update_setting_repeat(now=due + 1000)
        self.assertEqual(editor.text, "abcde")

    def test_numeric_slider_limits_write_only_on_release_and_manual_values_work(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        self.show_setting("field:black:max_tokens")
        track = self.app.gui.setting_controls()["slider:black:max_tokens"]
        with patch.object(self.app.local_settings, "save", wraps=self.app.local_settings.save) as save:
            self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=track.center))
            self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(track.right - 1, track.centery)))
            self.assertEqual(self.app.gui.api_profiles["black"]["max_tokens"], 131072)
            save.assert_not_called()
            self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(track.right - 1, track.centery)))
            self.assertEqual(save.call_count, 1)
        self.app.focus_setting("field:black:max_tokens")
        self.app.gui.setting_buffer = "32768"
        self.key_setting(pygame.K_RETURN)
        self.assertEqual(self.app.gui.api_profiles["black"]["max_tokens"], 32768)
        self.app.focus_setting("field:black:max_tokens")
        self.app.gui.setting_buffer = "131073"
        self.assertFalse(self.app.commit_setting())
        self.assertEqual(self.app.gui.api_profiles["black"]["max_tokens"], 32768)
        self.key_setting(pygame.K_ESCAPE)
        self.show_setting("field:black:temperature")
        self.app.gui.api_profiles["black"]["temperature"] = 1.0
        button = self.app.gui.setting_controls()["default:black:temperature"]
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=button.center))
        self.assertIsNone(self.app.gui.api_profiles["black"]["temperature"])

    def test_dropdown_opens_without_changing_value_then_selects_explicit_option(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        field = self.show_setting("field:black:token_parameter")
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=field.center))
        self.assertEqual(self.app.gui.api_profiles["black"]["token_parameter"], "max_tokens")
        options = self.app.gui.dropdown_controls()
        self.assertEqual(set(options), {"max_tokens", "max_completion_tokens"})
        self.app.gui.draw_sidebars()
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=options["max_completion_tokens"].center))
        self.assertEqual(self.app.gui.api_profiles["black"]["token_parameter"], "max_completion_tokens")
        self.assertIsNone(self.app.gui.setting_dropdown)
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=field.center))
        self.key_setting(pygame.K_ESCAPE)
        self.assertEqual(self.app.gui.api_profiles["black"]["token_parameter"], "max_completion_tokens")

    def test_token_limit_notice_is_colored_in_upper_pane_and_clears_with_game(self):
        from agent_play import AgentPlayController
        from test_agent_play import FakeRequest, response
        self.app.configure_agent_play_mode(True, "black")
        self.start()
        self.app.gui.api_profiles["black"].update(endpoint="https://example.test/v1", model="fixture")
        requests = []
        def factory(profile, payload):
            request = FakeRequest(profile, payload)
            requests.append(request)
            return request
        self.app.agent_play = AgentPlayController(self.app, factory)
        self.app.agent_play.next()
        returned = response(content="Partial reply", usage=4096)
        returned["data"]["choices"][0]["finish_reason"] = "length"
        requests[0].response = returned
        self.app.agent_play.tick()
        pane = self.app.gui.exposure_panes["thinking"]
        start, end, color = pane.colors[-1]
        self.assertIn("Increase Output Token Limit", pane.text[start:end])
        self.assertEqual(color, (235, 74, 72))
        self.app.gui.exposure_lines("thinking", 120)
        cached = self.app.gui.exposure_line_cache["thinking"]
        colored_lines = [line for line, style in zip(cached[1], cached[2]) if style == color]
        self.assertTrue(colored_lines)
        self.app.gui.draw_sidebars()
        self.app.start_game(2)
        self.assertEqual(pane.colors, [])

    def test_connection_test_follows_model_and_reports_async_result_without_game_actions(self):
        import pygame
        from agent_play import ConnectionTester
        from test_agent_play import FakeRequest, response
        self.app.configure_agent_play_mode(True, "black")
        self.app.start_game(2)  # A probe is allowed during human-only token placement.
        session = self.app.state.session
        revision = session.revision
        self.app.gui.api_profiles["black"].update(endpoint="https://example.test/v1", model="fixture")
        requests = []
        def factory(profile, payload):
            request = FakeRequest(profile, payload)
            requests.append(request)
            return request
        self.app.connection_tester = ConnectionTester(self.app, factory)
        names = [name for name, _, _ in self.app.gui.settings_rows()]
        self.assertEqual(names[names.index("field:black:model") + 1:names.index("field:black:model") + 3],
                         ["field:black:streaming", "test:black"])
        button = self.show_setting("test:black")
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=button.center))
        self.assertEqual(self.app.gui.connection_status["black"], "running")
        self.assertNotIn("tools", requests[0].payload)
        self.assertNotIn("board", str(requests[0].payload))
        self.assertTrue(requests[0].payload["stream"])
        requests[0].response = {"event": "progress", "reasoning": "Testing", "reply": "O"}
        self.app.connection_tester.tick()
        self.assertEqual(self.app.gui.connection_status["black"], "running")
        requests[0].response = response(content="OK")
        self.app.connection_tester.tick()
        self.assertEqual(self.app.gui.connection_status["black"], "running")
        job = self.app.connection_tester.jobs["black"]
        requests[-1].response = response(("connection_echo", {"nonce": job["nonce"]}))
        self.app.connection_tester.tick()
        self.assertEqual(requests[-1].payload["messages"][1]["reasoning_content"], "Provider-returned reasoning")
        requests[-1].response = response(content=job["receipt"])
        self.app.connection_tester.tick()
        if self.app.gui.api_profiles["black"]["vision"]:
            content = requests[-1].payload["messages"][0]["content"]
            self.assertNotIn(job["color"], content[0]["text"])
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
            requests[-1].response = response(content=job["color"])
            self.app.connection_tester.tick()
        self.assertEqual(self.app.gui.connection_status["black"], "success")
        pane = self.app.gui.exposure_panes["thinking"]
        self.assertIn("Connection Test | Black | Success", pane.text)
        self.assertEqual(pane.messages[-1]["side"], None)
        self.assertEqual(pane.messages[-1]["color"], None)
        self.assertEqual(session.revision, revision)
        self.assertEqual(session.llm_usage.requests, [])
        self.app.connection_tester.start("black")
        requests[-1].response = {"error": "API returned HTTP 401. Invalid API key."}
        self.app.connection_tester.tick()
        self.assertEqual(self.app.gui.connection_status["black"], "failed")
        self.assertIn("HTTP 401", pane.messages[-1]["text"])
        self.assertIsNone(pane.messages[-1]["color"])
        while self.app.connection_tester.jobs:
            requests[-1].response = {"error": "API returned HTTP 401. Invalid API key."}
            self.app.connection_tester.tick()
        self.app.connection_tester.start("black")
        self.app.apply_settings()
        self.assertTrue(requests[-1].cancelled)
        self.assertEqual(self.app.gui.connection_status["black"], "idle")

    def test_streaming_click_switch_is_per_side_and_persists_locally(self):
        import pygame
        from local_settings import LocalSettings
        self.app.configure_agent_play_mode(True, "black")
        self.app.gui.player_types["white"] = "llm"
        rows = [name for name, _, _ in self.app.gui.settings_rows()]
        for side in ("black", "white"):
            model = rows.index("field:" + side + ":model")
            self.assertEqual(rows[model + 1:model + 3], ["field:" + side + ":streaming", "test:" + side])
        button = self.show_setting("field:black:streaming")
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=button.center))
        self.assertFalse(self.app.gui.api_profiles["black"]["streaming"])
        self.assertTrue(self.app.gui.api_profiles["white"]["streaming"])
        self.assertIsNone(self.app.gui.active_setting)
        self.assertEqual(self.app.gui.dropdown_values("field:black:streaming"), ())
        restored = LocalSettings(self.path / "settings.json").load()
        self.assertFalse(restored["api_profiles"]["black"]["streaming"])

    def test_partial_updates_replace_one_bubble_follow_tail_and_never_save(self):
        from unittest.mock import patch
        self.app.configure_agent_play_mode(True, "black")
        self.start()
        session = self.app.begin_llm_request("stream-bubble", "black")
        pane = self.app.gui.exposure_panes["thinking"]
        with patch.object(self.app, "persist") as save:
            for number in range(2):
                self.assertTrue(self.app.update_llm_request(session, "stream-bubble",
                    reasoning="中文 reasoning 🦋\n" * (60 + number)))
            save.assert_not_called()
        self.assertEqual(len(pane.messages), 1)
        self.assertEqual(pane.messages[0]["side"], "black")
        self.assertGreater(pane.scroll, 0)
        self.assertEqual(session.llm_usage.requests[0]["status"], "pending")
        self.assertNotIn("Tokens:", pane.text)
        pane.scroll = 0
        self.app.update_llm_request(session, "stream-bubble", reasoning="中文 reasoning 🦋\n" * 65)
        self.assertEqual(pane.scroll, 0)
        self.app.complete_llm_request(session, "stream-bubble", reasoning="Final reasoning", reply="Final reply",
                                      usage={"prompt_tokens": 20, "completion_tokens": 11, "total_tokens": 31})
        self.assertEqual(len(pane.messages), 1)
        self.assertIn("total 31", pane.text)
        self.assertIn("Completed", pane.text)
        self.assertFalse(self.app.update_llm_request(session, "stream-bubble", reply="Late progress"))
        self.assertEqual(len(session.llm_usage.requests), 1)
        self.app.gui.set_exposure_content(thinking="")
        self.assertTrue(self.app.update_llm_request(self.app.begin_llm_request("stopped-stream", "white"),
                                                   "stopped-stream", reasoning="Partial"))
        self.app.complete_llm_request(session, "stopped-stream", status="stopped", reasoning="Partial")
        self.assertIsNone(pane.messages[0]["side"])
        self.assertEqual(pane.messages[0]["color"], (235, 74, 72))

    def test_incremental_bubble_wrapping_matches_full_text_across_chunks_and_resize(self):
        gui = self.app.gui
        text = "中文🦋 and word fragments\n" + "a" * 100 + "  spaces\n" + "last line " * 20
        entry = {"text": "", "side": "black", "color": None}
        for width in (170, 95):
            entry.clear()
            entry.update(text="", side="black", color=None)
            for end in range(1, len(text) + 1, 7):
                entry["text"] = text[:end]
                self.assertEqual(gui.chat_entry_lines(entry, width), gui.wrap_chat_suffix(text[:end], width)[0])
            entry["text"] = text
            self.assertEqual(gui.chat_entry_lines(entry, width + 15), gui.wrap_chat_suffix(text, width + 15)[0])

    def test_numeric_defaults_reset_all_three_fields(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        for key, changed, default in (("max_tokens", 8192, 32768), ("timeout", 300, 120), ("temperature", 0.8, None)):
            self.app.gui.api_profiles["black"][key] = changed
            self.show_setting("field:black:" + key)
            button = self.app.gui.setting_controls()["default:black:" + key]
            self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=button.center))
            self.assertEqual(self.app.gui.api_profiles["black"][key], default)
        self.assertTrue(all("=" not in title for _, title, _ in self.app.gui.settings_rows()))

    def test_request_bubbles_keep_side_alignment_warning_style_and_scroll(self):
        self.app.configure_agent_play_mode(True, "black")
        self.start()
        for number, side in enumerate(("black", "white"), 1):
            request_id = "bubble-" + str(number)
            anchor = self.app.begin_llm_request(request_id, side)
            self.app.complete_llm_request(anchor, request_id, reasoning="Review the board. " * 20,
                                          reply="Choose a legal move.", usage={"total_tokens": 100})
        self.app.report_agent_notice("A request failed.")
        pane = self.app.gui.exposure_panes["thinking"]
        self.assertEqual(len(pane.messages), 3)
        self.assertEqual([entry["side"] for entry in pane.messages], ["black", "white", None])
        layout, count = self.app.gui.chat_layout(200)
        self.assertEqual(layout[0]["rect"].x, 0)
        self.assertLess(layout[0]["rect"].right, 200)
        self.assertGreater(layout[1]["rect"].x, 0)
        self.assertEqual(layout[1]["rect"].right, 200)
        self.assertEqual(layout[2]["rect"].x, 0)
        self.assertEqual(layout[2]["rect"].w, 200)
        self.assertEqual(layout[2]["color"], (235, 74, 72))
        self.assertGreater(count, 0)
        self.app.gui.draw_sidebars()
        self.app.start_game(2)
        self.assertEqual(pane.messages, [])

    def test_thinking_bubbles_match_side_background_and_text(self):
        import pygame
        gui = self.app.gui
        body = pygame.Rect(0, 0, 320, 400)
        pane = gui.exposure_panes["thinking"]
        pane.scroll = 0
        for side, background, foreground in (("black", (5, 5, 7), (245, 245, 245)),
                                             ("white", (245, 245, 242), (20, 20, 22))):
            entry = {"rect": pygame.Rect(0, 0, 300, 80), "side": side,
                     "padding": 10, "lines": ["Side notes"], "color": None}
            with patch.object(gui, "chat_layout", return_value=([entry], 3)):
                gui.draw_chat_messages(body, pane)
            self.assertEqual(tuple(gui.window.get_at((150, 65)))[:3], background)
            colors = {tuple(gui.window.get_at((x, y)))[:3]
                      for y in range(10, 45) for x in range(10, 160)}
            self.assertIn(foreground, colors)

    def test_prompt_editor_grows_preserves_newlines_and_saves_without_stopping(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        gui = self.app.gui
        field = self.show_setting("system_prompt_text")
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=field.center))
        self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=field.center))
        self.assertEqual(gui.active_setting, "system_prompt_text")
        self.app.handle_setting_key(pygame.K_a, pygame.KMOD_CTRL)
        self.app.edit_setting_text("中文行动提示词")
        short_height = gui.setting_row_height("system_prompt_text", gui.settings_body())
        self.app.handle_setting_key(pygame.K_RETURN, 0)
        self.app.edit_setting_text("第二行，执行已有笔记中的实验。\n" * 40)
        value = gui.setting_buffer
        self.assertIn("\n", value)
        self.assertGreater(gui.setting_row_height("system_prompt_text", gui.settings_body()), short_height)
        gui.draw_sidebars()
        with patch.object(self.app.agent_play, "stop") as stop:
            self.app.handle_setting_key(pygame.K_RETURN, pygame.KMOD_CTRL)
            stop.assert_not_called()
        self.assertIsNone(gui.active_setting)
        self.assertEqual(gui.prompts["system_prompt"], value)
        self.assertEqual(self.app.local_settings.load()["prompts"]["system_prompt"], value)
        button = self.show_setting("copy_system_prompt")
        with patch("main.write_clipboard") as write:
            self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=button.center))
            write.assert_called_once_with(value)

    def test_prompt_editor_cancel_blank_validation_and_mouse_caret(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        gui = self.app.gui
        original = gui.prompts["notes_prompt"]
        self.app.focus_setting("notes_prompt_text")
        gui.setting_editor.select_all()
        self.app.edit_setting_text("甲乙\n丙丁")
        rect = self.app.active_setting_rect()
        line_height = gui.exposure_font.get_linesize() + 3
        self.assertEqual(gui.prompt_cursor_at(rect, (rect.x + 10, rect.y + 10 + line_height)), 3)
        self.app.handle_setting_key(pygame.K_HOME, 0)
        self.assertEqual(gui.setting_cursor, 3)
        self.app.handle_setting_key(pygame.K_UP, 0)
        self.assertEqual(gui.setting_cursor, 0)
        self.app.handle_setting_key(pygame.K_a, pygame.KMOD_CTRL)
        self.app.handle_setting_key(pygame.K_BACKSPACE, 0)
        self.assertFalse(self.app.commit_setting())
        self.assertEqual(gui.prompts["notes_prompt"], original)
        self.app.handle_setting_key(pygame.K_ESCAPE, 0)
        self.assertIsNone(gui.active_setting)
        self.assertEqual(gui.prompts["notes_prompt"], original)

    def test_system_prompt_preview_matches_sent_text_and_viewing_does_not_stop_play(self):
        import pygame
        import agent_play
        import gui as gui_module
        from agent_prompts import SYSTEM_PROMPT
        self.app.configure_agent_play_mode(True, "black")
        gui = self.app.gui
        self.assertEqual(agent_play.SYSTEM_PROMPT, SYSTEM_PROMPT)
        self.assertEqual(gui_module.SYSTEM_PROMPT, SYSTEM_PROMPT)
        rows = {name: value for name, _, value in gui.settings_rows()}
        self.assertEqual(rows["system_prompt_text"], SYSTEM_PROMPT)
        for width in (120, 240, 400):
            self.assertEqual(" ".join(gui.system_prompt_lines(width)), SYSTEM_PROMPT)
        fixed_buttons = {name: tuple(gui.setting_controls()[name]) for name in ("next", "pause", "stop")}
        header = self.show_setting("system_prompt")
        with patch.object(self.app.agent_play, "stop") as stop, patch.object(self.app.local_settings, "save") as save:
            self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=header.center))
            self.assertFalse(gui.system_prompt_expanded)
            self.assertNotIn("system_prompt_text", {name for name, _, _ in gui.settings_rows()})
            header = self.show_setting("system_prompt")
            self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=header.center))
            self.assertTrue(gui.system_prompt_expanded)
            self.show_setting("copy_system_prompt")
            button = gui.setting_controls()["copy_system_prompt"]
            with patch("main.write_clipboard") as write:
                self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=button.center))
                write.assert_called_once_with(SYSTEM_PROMPT)
            stop.assert_not_called()
            save.assert_not_called()
        self.assertEqual({name: tuple(gui.setting_controls()[name]) for name in fixed_buttons}, fixed_buttons)
        gui.draw_sidebars()
        gui.agent_play_mode = False
        self.assertEqual(gui.settings_rows(), [("agent_play_mode", "Agent Play Mode", "Off")])

    def test_notes_prompt_preview_copy_and_no_ap_label(self):
        import pygame
        from agent_prompts import NOTES_PROMPT
        self.app.configure_agent_play_mode(True, "black")
        gui = self.app.gui
        rows = {name: (title, value) for name, title, value in gui.settings_rows()}
        self.assertEqual(rows["notes_prompt_text"][1], NOTES_PROMPT)
        self.assertEqual(rows["field:black:timeout"][0], "break when no AP cost (s)")
        for width in (120, 240, 400):
            self.assertEqual(" ".join(gui.system_prompt_lines(width, NOTES_PROMPT)), NOTES_PROMPT)
        with patch.object(self.app.agent_play, "stop") as stop:
            header = self.show_setting("notes_prompt")
            self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=header.center))
            self.assertFalse(gui.notes_prompt_expanded)
            header = self.show_setting("notes_prompt")
            self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=header.center))
            button = self.show_setting("copy_notes_prompt")
            with patch("main.write_clipboard") as write:
                self.app.handle_setting_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=button.center))
                write.assert_called_once_with(NOTES_PROMPT)
            stop.assert_not_called()
        gui.draw_sidebars()

    def test_next_can_resume_queued_review_during_human_time_wish(self):
        self.start()
        session = self.app.state.session
        session.flow.phase = "time_wish"
        session.flow.time_wish_winner = "black"
        self.app.gui.agent_play_mode = True
        self.app.agent_play.pause()
        self.app.note_reviewer.queue.append((session, "black", {}))
        self.app.sync_play_controls()
        self.assertTrue(self.app.gui.play_can_start)
        self.app.handle_play_control("next")
        self.assertFalse(self.app.agent_play.paused)
        self.assertEqual(session.flow.phase, "time_wish")

    def test_notes_pages_preserve_side_scroll_and_update_independently(self):
        import pygame
        self.start()
        session = self.app.state.session
        session.agent_notes = {"black": "Black hypothesis", "white": "White hypothesis"}
        self.app.refresh_exposure_notes()
        gui = self.app.gui
        pane = gui.exposure_panes["notes"]
        self.assertEqual(gui.notes_side, "black")
        self.assertEqual(pane.text, "Black hypothesis")
        self.assertEqual(pane.messages, [])
        pane.scroll = 2
        controls = gui.notes_page_controls()
        self.assertTrue(gui.handle_sidebar_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=controls["next"].center)))
        self.assertEqual(gui.notes_side, "white")
        self.assertEqual(pane.text, "White hypothesis")
        self.assertEqual(pane.scroll, 0)
        pane.scroll = 3
        session.agent_notes["black"] = "Revised black hypothesis"
        self.app.refresh_exposure_notes()
        self.assertEqual(pane.text, "White hypothesis")
        self.assertEqual(pane.scroll, 3)
        gui.select_notes_side("black")
        self.assertEqual(pane.scroll, 2)
        self.assertEqual(pane.text, "Revised black hypothesis")
        gui.draw_exposure_panes()
        body = gui.exposure_body(gui.exposure_pane_rects()["notes"])
        self.assertEqual(tuple(gui.window.get_at((body.right-2, body.bottom-2)))[:3], (5,5,7))
        gui.select_notes_side("white")
        gui.draw_exposure_panes()
        self.assertEqual(tuple(gui.window.get_at((body.right-2, body.bottom-2)))[:3], (245,245,242))
        self.app.start_game(2)
        self.assertNotIn("hypothesis", pane.text)
        self.assertEqual(gui.notes_scroll_positions, {})


if __name__ == "__main__":
    unittest.main()
