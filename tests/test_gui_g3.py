"""G3 desktop inputs share engine rules and the common board geometry."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from basicgame import Resource
from gameengine import PHASE_CHECKING_WIN, PHASE_PLAYING
from sl_func import save_game


class G3GuiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy", "PYGAME_HIDE_SUPPORT_PROMPT": "1"})
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

    def click(self, name):
        region, = [region for region in self.app.gui.click_regions if region.name == name]
        self.app.handle_click(self.app.gui.to_window_position(region.rect.center))

    def render(self):
        board, resources, camps = self.app.get_legal_targets()
        self.app.gui.draw(self.app.state, legal_board_targets=board, legal_resource_targets=resources, legal_camp_targets=camps)

    def start_ready(self):
        self.app.gui.open_menu = "start"
        self.app.gui.build_click_regions()
        self.click("menu:start_g3")
        self.click("camp:black")
        self.app.state.game.current_ap = self.app.state.game.max_ap = 20

    def test_shop_purchase_diagonal_movement_extra_turn_and_sale(self):
        self.start_ready()
        game = self.app.state.game
        session = self.app.state.session
        for pos in ((3, 1), (3, 2)):
            game.set_piece(pos, Resource("black", "squirrel"))
        session.refresh_players()
        self.render()
        self.assertEqual(len(self.app.gui.resource_buttons), 5)
        self.assertEqual(self.app.gui.resource_buttons[3][0], "butterfly")
        self.assertIn("butterfly", self.app.get_legal_targets()[1])
        self.click("resource:butterfly")
        for x, y in ((2, 1), (2, 5), (3, 1), (3, 2)):
            self.click(f"board:{x},{y}")
        self.click("board:4,3")
        self.assertEqual(game.get_piece((4, 3)).kind, "butterfly")
        self.assertTrue(game.g3_turn.resources_changed)
        self.assertEqual(game.current_ap, 19)
        game.new_piece = []
        self.click("board:4,3")
        self.assertIn((5, 4), self.app.get_legal_targets()[0])
        self.click("board:5,4")
        self.assertEqual(game.current_ap, 18)
        self.click("board:5,4")
        self.click("board:5,4")
        self.assertEqual(session.flow.phase, PHASE_CHECKING_WIN)
        self.assertEqual(game.current_ap, 17)
        self.assertEqual(session.flow.last_condition_results[1:3], [True, False])
        session.resolve_checks()
        self.app.after_engine_change()
        self.assertEqual(session.flow.phase, PHASE_PLAYING)
        self.assertTrue(game.g3_turn.extra_turn)
        self.assertFalse(game.g3_turn.resources_changed)
        self.click("board:5,4")
        self.assertIn("butterfly", self.app.get_legal_targets()[1])
        self.assertIn("sell this butterfly", self.app.tooltip_for_region("resource:butterfly"))
        self.click("resource:butterfly")
        self.click("board:5,4")
        self.click("board:4,4")
        self.assertEqual(session.players["black"].num_squirrels, 2)
        self.assertEqual(game.current_ap, 16)
        self.assertTrue(game.g3_turn.resources_changed)
        self.render()

    def test_mode_geometry_cards_arrows_and_load_preserve_extra_turn(self):
        self.start_ready()
        import pygame
        renderer = self.app.gui
        game = self.app.state.game
        game.set_piece((4, 3), Resource("black", "butterfly"))
        self.app.state.session.refresh_players()
        game.g3_turn.extra_turn = True
        original_geometry = (tuple(renderer.viewport), tuple(renderer.rect_for_logical_cell(2, 1)), renderer.screen.get_size())
        self.render()
        self.assertEqual(len(renderer.condition_files), 5)
        self.assertTrue(all(name in renderer.condition_images for name in renderer.condition_files))
        for index in range(5):
            self.assertTrue(renderer.screen.get_rect().contains(renderer.resource_button_rect(index)))
        for side in ("black", "white"):
            tile = renderer.images[f"{side}_eight_cell"]
            self.assertEqual(tile.get_at((0, 0)).a, 255)
            color = (255, 255, 255) if side == "black" else (29, 29, 29)
            for position in ((6, 6), (81, 6), (6, 81), (81, 81), (44, 6), (44, 81), (6, 44), (81, 44)):
                self.assertEqual(tuple(tile.get_at(position))[:3], color)
        with tempfile.TemporaryDirectory() as directory:
            path = save_game(game, self.app.state.session.players, flow=self.app.state.session.flow,
                             interaction=self.app.state.session.interaction, directory=directory, filename="g3.json")
            with patch("main.SAVES_DIR", Path(directory)):
                self.app.load_from_file(path.name)
            self.assertEqual(renderer.mode, 3)
            self.assertTrue(self.app.state.game.g3_turn.extra_turn)
            self.assertEqual(len(renderer.resource_buttons), 5)
        for start in (self.app.start_g1, self.app.start_g2, self.app.start_g3):
            start()
            self.render()
            self.assertEqual((tuple(renderer.viewport), tuple(renderer.rect_for_logical_cell(2, 1)), renderer.screen.get_size()), original_geometry)


if __name__ == "__main__":
    unittest.main()
