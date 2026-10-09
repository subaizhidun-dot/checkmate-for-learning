"""Mouse/menu/save checks for NG+ on the shared desktop interface."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gameengine import GameSession, PHASE_PLAYING, PHASE_CHOOSE_TOKEN, PHASE_TIME_WISH, save_session_file
from basicgame import Resource
import ui_text


class NewGamePlusGuiTests(unittest.TestCase):
    def test_llm_wish_is_human_controlled_after_load_and_unlocks_only_after_click(self):
        import gui
        for mode in (2, 3):
            for holder in ("black", "white"):
                with self.subTest(mode=mode, holder=holder), tempfile.TemporaryDirectory() as directory:
                    self.app.start_game(mode)
                    session = self.app.state.session
                    self.app.gui.agent_play_mode = True
                    self.app.gui.player_types = {"black": "llm", "white": "llm"}
                    self.app.apply_settings()
                    session.game.time_token_owner = holder
                    session.flow.time_wish_winner = "white" if holder == "black" else "black"
                    session.flow.phase = PHASE_TIME_WISH
                    path = save_session_file(session, Path(directory) / "pending.json")
                    with patch("main.SAVES_DIR", Path(directory)):
                        self.app.load_from_file(path.name)
                    session = self.app.state.session
                    self.render()
                    self.assertFalse(self.app.state.new_game_plus_available)
                    self.assertEqual(self.app.gui.start_menu_labels(), ["Start G1", "Start G2", "Start G3"])
                    self.assertFalse(self.app.gui.play_can_start)
                    self.assertEqual(self.app.gui.play_message, "Time wish: human only")
                    self.assertIn("Human control", session.pending_message)
                    self.assertEqual(self.app.get_legal_targets(), (set(), set(), {holder}))
                    panel = gui.LEFT_CAMP_RECT if holder == "black" else gui.RIGHT_CAMP_RECT
                    pixel = (panel.left + gui.LEGAL_HINT_INSET, panel.top + gui.LEGAL_HINT_INSET)
                    self.assertEqual(tuple(self.app.gui.screen.get_at(pixel))[:3], gui.LEGAL_HINT_COLOR)
                    self.click(f"camp:{holder}")
                    self.assertIsNone(session.game.time_token_owner)
                    self.assertTrue(session.can_start_new_game_plus())
                    self.assertEqual(self.app.gui.player_types, {"black": "llm", "white": "llm"})
                    self.assertEqual(self.app.gui.start_menu_labels(), ["Start G1+", "Start G2+", "Start G3+"])

    def test_g1_victory_has_no_wish_or_ng_plus_menu(self):
        self.app.start_g1()
        session = self.app.state.session
        session.game.time_token_owner = "white"
        session.flow.last_condition_results = [True] * 5
        session.resolve_turn_end()
        self.app.after_engine_change()
        self.render()
        self.assertTrue(session.is_over())
        self.assertNotIn("New Game+", session.pending_message)
        self.assertFalse(session.can_start_new_game_plus())
        self.assertEqual(self.app.gui.start_menu_labels(), ["Start G1", "Start G2", "Start G3"])

    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy", "PYGAME_HIDE_SUPPORT_PROMPT": "1",
        })
        self.environment.start()
        from main import CheckMateApp
        self.settings_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.settings_directory.cleanup)
        self.app = CheckMateApp(settings_path=Path(self.settings_directory.name) / "settings.json")
        self.app.persist = lambda: None

    def tearDown(self):
        import pygame
        pygame.quit()
        self.environment.stop()

    def render(self):
        board, resources, camps = self.app.get_legal_targets()
        self.app.gui.draw(self.app.state, legal_board_targets=board, legal_resource_targets=resources, legal_camp_targets=camps)

    def click(self, name):
        region, = [region for region in self.app.gui.click_regions if region.name == name]
        self.app.handle_click(self.app.gui.to_window_position(region.rect.center))

    def start(self, mode):
        self.click("menu:start")
        self.app.gui.build_click_regions()
        self.click(f"menu:start_g{mode}")

    def test_startup_is_standard_even_when_a_terminal_save_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            source = GameSession.new_g1()
            source.end_session("mate", loser="white")
            save_session_file(source, Path(directory) / "terminal.json")
            with patch("main.SAVES_DIR", Path(directory)):
                self.render()
                self.assertIsNone(self.app.state.game)
                self.assertFalse(self.app.state.new_game_plus_available)
                self.assertEqual(self.app.gui.start_menu_labels(), ["Start G1", "Start G2", "Start G3"])
                self.start(2)
                self.assertEqual(self.app.state.session.flow.phase, PHASE_CHOOSE_TOKEN)
                self.click("camp:black")
                self.assertEqual(self.app.state.session.flow.phase, PHASE_PLAYING)

    def test_save_terminal_load_cross_mode_start_and_resave_playing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("main.SAVES_DIR", Path(directory)), patch("sl_func.SAVES_DIR", Path(directory)):
                self.app.start_g2()
                source = self.app.state.session
                source.game.time_token_owner = "white"
                source.flow.phase = PHASE_TIME_WISH
                source.flow.time_wish_winner = "black"
                source.select_camp("white")
                self.render()
                self.assertEqual(self.app.gui.start_menu_labels(), ["Start G1+", "Start G2+", "Start G3+"])
                path = self.app.save_slot(1)
                self.app.load_from_file(path.name)
                self.assertTrue(self.app.state.new_game_plus_available)
                self.start(3)
                self.assertEqual(self.app.state.gamemode, 3)
                self.assertEqual(self.app.state.session.flow.phase, PHASE_PLAYING)
                self.assertIsNone(self.app.state.time_token_owner)
                self.assertEqual(self.app.state.current_ap, 1)
                self.assertIsNone(self.app.state.session.result()["winner"])
                self.render()
                self.assertEqual(self.app.gui.start_menu_labels(), ["Start G1", "Start G2", "Start G3"])
                self.click("board:2,1")
                path = self.app.save_slot(2)
                self.app.load_from_file(path.name)
                self.assertEqual(self.app.state.interaction.selected_pos, (2, 1))
                self.click("board:3,1")
                self.app.state.session.resolve_checks()
                self.app.after_engine_change()
                self.assertEqual(self.app.state.current_player, "white")
                self.assertEqual(self.app.state.current_ap, 2)
                self.assertIsNone(self.app.state.time_token_owner)
                self.app.state.session.end_session("mate", loser="black")
                self.render()
                self.assertFalse(self.app.state.new_game_plus_available)
                self.start(2)
                self.assertEqual(self.app.state.gamemode, 2)
                self.assertEqual(self.app.state.session.flow.phase, PHASE_CHOOSE_TOKEN)
                self.assertIsNone(self.app.state.time_token_owner)
                self.assertTrue(self.app.state.game.get_piece((2, 3)).rooted)
                self.assertEqual(self.app.state.game.tree_markers, set())
                self.assertEqual(self.app.state.game.expanded_corners, set())
                self.assertTrue(path.exists())

    def test_time_wish_mouse_prompt_and_start_other_mode(self):
        self.app.start_g2()
        session = self.app.state.session
        session.flow.phase = PHASE_TIME_WISH
        session.flow.time_wish_winner = "black"
        session.game.time_token_owner = session.game.current_player = "white"
        self.render()
        self.assertFalse(self.app.gui.new_game_plus_available)
        self.click("camp:white")
        self.assertEqual(session.pending_message, "Save this game, then start New Game+.")
        self.assertEqual(session.result()["winner"], "black")
        self.assertTrue(self.app.gui.new_game_plus_available)
        self.render()
        self.start(1)
        self.assertEqual(self.app.state.session.flow.phase, PHASE_PLAYING)
        self.assertIsNone(self.app.state.time_token_owner)
        self.assertEqual(self.app.state.game.get_piece((0, 3)).kind, "pine")

    def test_terminal_without_pieces_and_unfinished_position_start_standard(self):
        for known_winner in (False, True):
            self.app.start_g3()
            if known_winner:
                self.app.state.session.end_session("mate", loser="white")
                self.app.state.game.board_matrix = [[None for _ in range(9)] for _ in range(7)]
                self.app.state.game.set_piece((0, 3), Resource("black", "pine"))
            self.render()
            self.assertFalse(self.app.gui.new_game_plus_available)
            self.start(1)
            self.assertEqual(self.app.state.session.flow.phase, PHASE_CHOOSE_TOKEN)


if __name__ == "__main__":
    unittest.main()
