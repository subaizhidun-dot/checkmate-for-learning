"""Drag scrolling and turn browsing through the desktop event routes."""
import unittest
from unittest.mock import patch

import test_gui_agent_settings as settings_tests


class SidebarNavigationTests(unittest.TestCase):
    def setUp(self):
        settings_tests.AgentSettingsGuiTests.setUp(self)
        self.gui = self.app.gui

    def tearDown(self):
        settings_tests.AgentSettingsGuiTests.tearDown(self)

    def event(self, kind, **values):
        import pygame
        event = pygame.event.Event(kind, **values)
        return self.app.handle_setting_event(event) or self.gui.handle_sidebar_event(event)

    def click(self, rect):
        import pygame
        return self.event(pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center)

    def test_settings_drag_reaches_both_ends_and_releases_outside_sidebar(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        controls = {name: rect.copy() for name, rect in self.gui.setting_controls().items()
                    if name in {"next", "pause", "stop"}}
        track, thumb, maximum = self.gui.sidebar_scrollbars()["settings"]
        self.assertGreaterEqual(track.w, 10)
        self.assertFalse(track.colliderect(self.gui.settings_body()))
        with patch.object(self.app.local_settings, "save") as save:
            self.assertTrue(self.click(thumb))
            self.assertEqual(self.gui.drag_scrollbar, "settings")
            self.assertEqual(self.gui.settings_scroll, 0)
            self.assertTrue(self.event(pygame.MOUSEMOTION, pos=(800, track.bottom + 300)))
            self.assertEqual(self.gui.settings_scroll, maximum)
            self.assertTrue(self.event(pygame.MOUSEBUTTONUP, button=1, pos=(800, track.bottom + 300)))
            self.assertIsNone(self.gui.drag_scrollbar)
            for name, rect in controls.items():
                self.assertEqual(self.gui.setting_controls()[name], rect)
            track, thumb, _ = self.gui.sidebar_scrollbars()["settings"]
            self.click(thumb)
            self.event(pygame.MOUSEMOTION, pos=(800, track.top - 300))
            self.event(pygame.MOUSEBUTTONUP, button=1, pos=(800, track.top - 300))
            self.assertEqual(self.gui.settings_scroll, 0)
            self.click(track)
            self.assertGreater(self.gui.settings_scroll, 0)
            self.event(pygame.MOUSEBUTTONUP, button=1, pos=track.center)
            save.assert_not_called()

    def test_thinking_drag_is_independent_and_does_not_save_or_stop_play(self):
        import pygame
        self.gui.append_exposure_output("\n".join(f"Reasoning {line}" for line in range(180)), turn=1, side="black")
        self.gui.set_exposure_content(notes="\n".join(f"Note {line}" for line in range(100)))
        pane = self.gui.exposure_panes["thinking"]
        pane.scroll = 0
        notes = self.gui.exposure_panes["notes"]
        notes.scroll = 5
        track, thumb, maximum = self.gui.sidebar_scrollbars()["thinking"]
        body = self.gui.exposure_body(self.gui.exposure_pane_rects()["thinking"])
        self.assertLessEqual(body.right, track.left)
        with patch.object(self.app, "persist") as save, patch.object(self.app.agent_play, "stop") as stop:
            self.click(thumb)
            self.event(pygame.MOUSEMOTION, pos=(100, track.bottom + 300))
            self.event(pygame.MOUSEBUTTONUP, button=1, pos=(100, track.bottom + 300))
            self.assertEqual(pane.scroll, maximum)
            self.assertEqual(notes.scroll, 5)
            self.assertEqual(self.gui.settings_scroll, 0)
            self.assertIsNone(self.gui.drag_scrollbar)
            save.assert_not_called()
            stop.assert_not_called()

    def test_focus_loss_cancels_drag_and_resize_keeps_controls_inside_header(self):
        import pygame
        self.gui.append_exposure_output("Thinking\n" * 100, turn=2, side="black")
        track, thumb, _ = self.gui.sidebar_scrollbars()["thinking"]
        self.click(thumb)
        self.event(pygame.WINDOWFOCUSLOST)
        self.assertIsNone(self.gui.drag_scrollbar)
        for size in ((700, 550), (1600, 1000)):
            self.gui.resize_window(size)
            self.gui.draw_sidebars()
            pane = self.gui.exposure_pane_rects()["thinking"]
            controls = self.gui.thinking_turn_controls()
            self.assertTrue(all(pane.contains(rect) for rect in controls.values()))
            self.assertLess(controls["previous"].right, controls["turn"].left)
            self.assertLess(controls["turn"].right, controls["next"].left)
            _, _, maximum = self.gui.sidebar_scrollbars()["thinking"]
            self.assertLessEqual(self.gui.exposure_panes["thinking"].scroll, maximum)

    def test_arrows_skip_unrecorded_turns_and_old_turn_stays_selected_during_new_output(self):
        self.gui.append_exposure_output("First turn", turn=1, side="black")
        self.gui.append_exposure_output("Third turn", turn=3, side="white")
        self.assertEqual(self.gui.thinking_turn, 3)
        self.assertEqual([entry["text"] for entry in self.gui.chat_layout(200)[0]], ["Third turn"])
        self.click(self.gui.thinking_turn_controls()["previous"])
        self.assertEqual(self.gui.thinking_turn, 1)
        self.gui.append_exposure_output("Fifth turn", turn=5, side="black")
        self.assertEqual(self.gui.thinking_turn, 1)
        self.assertEqual([entry["text"] for entry in self.gui.chat_layout(200)[0]], ["First turn"])
        self.click(self.gui.thinking_turn_controls()["next"])
        self.assertEqual(self.gui.thinking_turn, 3)
        self.click(self.gui.thinking_turn_controls()["next"])
        self.assertEqual(self.gui.thinking_turn, 5)
        self.gui.append_exposure_output("Seventh turn", turn=7, side="white")
        self.assertEqual(self.gui.thinking_turn, 7)
        self.assertEqual(len(self.gui.exposure_panes["thinking"].messages), 4)
        self.assertIn("First turn", self.gui.exposure_panes["thinking"].text)

    def test_turn_input_routes_enter_and_escape_without_changing_settings_or_play(self):
        import pygame
        self.gui.append_exposure_output("Turn one", turn=1, side="black")
        self.gui.append_exposure_output("Turn seven", turn=7, side="white")
        with patch.object(self.app, "apply_settings") as settings, patch.object(self.app.agent_play, "stop") as stop:
            self.click(self.gui.thinking_turn_controls()["turn"])
            self.assertTrue(self.event(pygame.TEXTINPUT, text="1"))
            self.assertTrue(self.event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0))
            self.assertEqual(self.gui.thinking_turn, 1)
            self.assertFalse(self.gui.thinking_turn_editing)
            self.click(self.gui.thinking_turn_controls()["turn"])
            self.event(pygame.TEXTINPUT, text="0")
            self.event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0)
            self.assertTrue(self.gui.thinking_turn_invalid)
            self.assertEqual(self.gui.thinking_turn, 1)
            self.assertTrue(self.event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0))
            self.assertFalse(self.gui.thinking_turn_editing)
            self.assertEqual(self.gui.thinking_turn, 1)
            self.click(self.gui.thinking_turn_controls()["turn"])
            self.event(pygame.TEXTINPUT, text="8")
            self.event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0)
            self.assertEqual(self.gui.thinking_turn, 8)
            self.assertIn("No model output for turn 8", " ".join(self.gui.exposure_lines("thinking", 240)))
            settings.assert_not_called()
            stop.assert_not_called()

    def test_stream_updates_do_not_duplicate_requests_or_move_an_old_turn_reader(self):
        self.gui.append_exposure_output("Old reasoning\n" * 100, turn=1, side="black", request_id="old")
        self.gui.append_exposure_output("Receiving", turn=2, side="white", request_id="stream")
        self.gui.select_thinking_turn(1)
        pane = self.gui.exposure_panes["thinking"]
        pane.scroll = 7
        self.gui.append_exposure_output("Receiving more reasoning\n" * 100, turn=2, side="white", request_id="stream")
        self.assertEqual(self.gui.thinking_turn, 1)
        self.assertEqual(pane.scroll, 7)
        self.assertEqual(len(pane.messages), 2)
        self.gui.select_thinking_turn(2)
        pane.scroll = 11
        self.gui.select_thinking_turn(1)
        self.assertEqual(pane.scroll, 7)
        self.gui.select_thinking_turn(2)
        self.assertEqual(pane.scroll, 11)

    def test_review_output_belongs_to_the_checked_turn_without_inventing_llm_turns(self):
        self.app.start_game(3)
        session = self.app.state.session
        session.submit("choose_initial_token:side=black", session.revision)
        self.app.after_engine_change()
        session.turn_number = 10
        session.llm_usage.begin_request("review", "black", purpose="notes")
        self.app.update_llm_request(session, "review", reasoning="Review turn 4", turn=4)
        self.app.complete_llm_request(session, "review", reply="Notes updated", turn=4, usage={"total_tokens": 100})
        message = self.gui.exposure_panes["thinking"].messages[-1]
        self.assertEqual(message["turn"], 4)
        self.assertIn("Turn 4", message["text"])
        self.assertEqual(session.llm_usage.summary()["llm_turns"], 0)

    def test_new_game_clears_turn_selection_and_editor_without_erasing_settings_scroll(self):
        self.gui.append_exposure_output("Old turn", turn=7, side="black")
        self.click(self.gui.thinking_turn_controls()["turn"])
        self.gui.settings_scroll = 15
        self.app.start_game(2)
        self.assertIsNone(self.gui.thinking_turn)
        self.assertFalse(self.gui.thinking_turn_editing)
        self.assertEqual(self.gui.thinking_scroll_positions, {})
        self.assertEqual(self.gui.thinking_turns(), [])
        self.assertEqual(self.gui.settings_scroll, 15)


if __name__ == "__main__":
    unittest.main()
