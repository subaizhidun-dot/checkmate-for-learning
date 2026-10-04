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
                self.app.start_g1()
                source = self.app.state.session
                source.game.time_token_owner = "white"
                source.end_session("check_victory", loser="white")
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
                self.start(2)
                self.assertEqual(self.app.state.gamemode, 2)
                self.assertEqual(self.app.state.session.flow.phase, PHASE_PLAYING)
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
