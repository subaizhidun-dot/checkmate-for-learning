"""Off-screen desktop checks for mode switching, clicks and G2 save restoration."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class G2GuiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
            "PYGAME_HIDE_SUPPORT_PROMPT": "1",
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

    def click(self, name):
        regions = [r for r in self.app.gui.click_regions if r.name == name]
        self.assertEqual(len(regions), 1, name)
        self.assertEqual(self.app.gui.find_region_name(regions[0].rect.center), name)
        self.app.handle_click(self.app.gui.to_window_position(regions[0].rect.center))

    def render(self):
        board, resources, camps = self.app.get_legal_targets()
        self.app.gui.draw(self.app.state, legal_board_targets=board,
                          legal_resource_targets=resources, legal_camp_targets=camps)

    def start_ready(self):
        self.app.gui.open_menu = "start"
        self.app.gui.build_click_regions()
        self.click("menu:start_g2")
        self.click("camp:black")
        self.app.state.game.current_ap = self.app.state.game.max_ap = 20

    def test_startup_buttons_and_mode_switching(self):
        self.render()
        self.assertFalse(any(r.name.startswith("resource:") for r in self.app.gui.click_regions))
        self.assertEqual(self.app.gui.condition_files, [])
        self.start_ready()
        self.render()
        self.assertEqual(self.app.gui.resource_buttons[1], ("mole", "Mole (2)"))
        self.assertEqual(len(self.app.gui.condition_files), 7)
        self.app.start_g1()
        self.render()
        self.assertEqual(self.app.gui.resource_buttons[1], ("elephant", "Elephant (2)"))
        self.assertEqual(len(self.app.gui.condition_files), 5)
        self.assertEqual(self.app.gui.screen.get_size(), (1200, 1000))

    def test_rapid_mode_switches_keep_the_complete_g2_layout(self):
        import gui
        renderer = self.app.gui
        renderer.resize_window((1500, 900))
        self.app.start_g2()
        self.render()
        canvas = renderer.screen
        background = renderer.background

        def geometry():
            return (
                renderer.screen.get_size(), tuple(renderer.viewport),
                tuple(renderer.rect_for_logical_cell(2, 1)),
                tuple(renderer.rect_for_logical_cell(6, 5)),
                renderer.to_window_position(renderer.rect_for_logical_cell(2, 1).topleft),
                tuple(gui.LEFT_CAMP_RECT), tuple(gui.RIGHT_CAMP_RECT),
                tuple(gui.LEFT_STATUS_RECT), tuple(gui.RIGHT_STATUS_RECT),
                gui.DIALOG_Y, gui.DIALOG_H, gui.BUTTON_Y,
            )

        expected = geometry()
        for index in range(20):
            self.app.start_g1() if index % 2 == 0 else self.app.start_g2()
            self.render()
            self.assertEqual(geometry(), expected)
            self.assertIs(renderer.screen, canvas)
            self.assertIs(renderer.background, background)
            upper_corner = gui.BOARD_Y - gui.CELL
            lower_corner_bottom = gui.BOARD_Y + gui.BOARD_H + gui.CELL
            self.assertGreater(upper_corner, gui.CONDITION_Y + gui.CELL)
            self.assertLess(lower_corner_bottom, gui.DIALOG_Y)
        self.app.start_g1()
        self.assertNotIn((6, 0), renderer.actionable_cells)
        self.assertNotIn((2, 6), renderer.actionable_cells)

    def test_mouse_purchase_uproot_movement_and_planting(self):
        self.start_ready()
        self.click("resource:mole")
        self.click("board:2,1")
        self.click("board:2,5")
        self.click("board:4,4")
        self.assertEqual(self.app.state.game.get_piece((4, 4)).kind, "mole")
        self.click("board:2,3")
        self.click("board:2,3")
        self.assertFalse(self.app.state.game.get_piece((2, 3)).rooted)
        self.click("board:2,3")
        self.click("board:3,4")
        self.render()
        self.assertIn((2, 3), self.app.state.game.tree_markers)
        self.assertNotIn("board:2,5", {r.name for r in self.app.gui.click_regions})
        self.assertIn("board:2,6", {r.name for r in self.app.gui.click_regions})
        self.click("board:3,4")
        self.click("board:2,3")
        self.click("board:2,3")
        self.click("board:2,3")
        self.assertTrue(self.app.state.game.get_piece((2, 3)).rooted)
        self.assertEqual(self.app.state.game.expanded_corners, set())
        self.assertIn("board:2,5", {r.name for r in self.app.gui.click_regions})
        self.assertNotIn("board:2,6", {r.name for r in self.app.gui.click_regions})

    def test_loaded_expanded_board_hit_targets_and_layout(self):
        from sl_func import save_game
        import gui
        self.start_ready()
        self.click("board:2,3")
        self.click("board:2,3")
        s = self.app.state.session
        s.game.current_player = "white"
        self.click("board:6,3")
        self.click("board:6,3")
        with tempfile.TemporaryDirectory() as directory:
            path = save_game(s.game, s.players, flow=s.flow, interaction=s.interaction,
                             directory=directory, filename="g2.json")
            self.app.start_g1()
            with patch("main.SAVES_DIR", Path(directory)):
                self.app.load_from_file(path.name)
            self.render()
            regions = [r for r in self.app.gui.click_regions if r.name.startswith("board:")]
            self.assertEqual(len(regions), 25)
            self.assertEqual(self.app.state.game.expanded_corners, {"lower_left", "upper_right"})
            names = {r.name for r in regions}
            self.assertIn("board:6,0", names)
            self.assertIn("board:2,6", names)
            self.assertNotIn("board:6,1", names)
            self.assertNotIn("board:2,5", names)
            for region in regions:
                self.assertGreaterEqual(region.rect.top, gui.CONDITION_Y + gui.CELL)
                self.assertLess(region.rect.bottom, gui.DIALOG_Y)
                self.assertTrue(self.app.gui.screen.get_rect().contains(region.rect))
                self.assertEqual(self.app.gui.find_region_name(region.rect.center), region.name)
            self.assertLessEqual(gui.BUTTON_Y + gui.BUTTON_H, self.app.gui.screen.get_height())

    def test_mode_changes_preserve_window_and_resize_keeps_clicks_aligned(self):
        import pygame
        size = self.app.gui.window.get_size()
        window = self.app.gui.native_window
        self.start_ready()
        self.render()
        self.assertEqual(self.app.gui.window.get_size(), size)
        self.assertIs(self.app.gui.native_window, window)
        self.app.start_g1()
        self.render()
        self.assertEqual(self.app.gui.window.get_size(), size)
        self.assertIs(self.app.gui.native_window, window)
        self.app.gui.resize_window((900, 600))
        self.app.start_g2()
        self.render()
        self.assertEqual(self.app.gui.window.get_size(), (900, 600))
        self.assertTrue(self.app.gui.window.get_rect().contains(self.app.gui.viewport))
        self.assertIsNone(self.app.gui.to_logical_position((0, 0)))
        self.click("camp:black")
        self.app.state.game.current_ap = 20
        self.click("board:2,3")
        self.click("board:2,3")
        self.assertFalse(self.app.state.game.get_piece((2, 3)).rooted)
        self.render()
        for region in self.app.gui.click_regions:
            point = self.app.gui.to_window_position(region.rect.center)
            logical = self.app.gui.to_logical_position(point)
            self.assertEqual(self.app.gui.find_region_name(logical), region.name)
        self.assertEqual(pygame.display.get_surface().get_size(), (900, 600))

    def test_sidebars_fold_resize_and_preserve_game_input(self):
        import pygame
        self.start_ready()
        gui = self.app.gui
        gui.resize_window((1500, 900))
        before = self.app.state.session.snapshot()
        for side in ("left", "right"):
            original_area = gui.game_area.w
            gui.handle_sidebar_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                button=1, pos=gui.sidebar_toggle_rects[side].center))
            self.assertFalse(gui.sidebars[side].expanded)
            self.assertGreater(gui.game_area.w, original_area)
            gui.handle_sidebar_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                button=1, pos=gui.sidebar_toggle_rects[side].center))
            self.assertTrue(gui.sidebars[side].expanded)
            self.assertEqual(gui.game_area.w, original_area)
            grip = gui.sidebar_grip_rects[side].center
            width = gui.sidebar_rects[side].w
            gui.handle_sidebar_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=grip))
            self.assertEqual(gui.drag_sidebar, side)
            end = (grip[0] + (40 if side == "left" else -40), grip[1])
            gui.handle_sidebar_event(pygame.event.Event(pygame.MOUSEMOTION, pos=end, rel=(40, 0), buttons=(1, 0, 0)))
            gui.handle_sidebar_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=end))
            self.assertIsNone(gui.drag_sidebar)
            self.assertGreater(gui.sidebar_rects[side].w, width)
            self.assertIsNone(gui.to_logical_position(gui.sidebar_rects[side].center))
        self.assertEqual(before, self.app.state.session.snapshot())
        self.assertTrue(gui.game_area.contains(gui.viewport))
        self.assertFalse(gui.viewport.colliderect(gui.sidebar_rects["left"]))
        self.assertFalse(gui.viewport.colliderect(gui.sidebar_rects["right"]))
        self.render()
        self.click("board:2,3")
        self.click("board:2,3")
        self.assertFalse(self.app.state.game.get_piece((2, 3)).rooted)
        widths = {side: bar.width for side, bar in gui.sidebars.items()}
        self.app.start_g1()
        self.app.start_g2()
        self.assertEqual(widths, {side: bar.width for side, bar in gui.sidebars.items()})

    def test_marker_is_hidden_under_pieces_and_returns_when_vacated(self):
        from basicgame import Resource
        self.start_ready()
        game = self.app.state.game
        game.tree_markers.add((2, 3))
        rect = self.app.gui.rect_for_logical_cell(2, 3)
        point = (rect.centerx, rect.top + 7)
        for piece in (None, Resource("black", "squirrel"), Resource("white", "mole"),
                      Resource("black", "tree", rooted=False), None):
            game.set_piece((2, 3), piece)
            self.app.gui.draw(self.app.state, mouse_pos=(0, 0))
            color = tuple(self.app.gui.screen.get_at(point)[:3])
            self.assertEqual(color == (231, 47, 44), piece is None)
            self.assertIn((2, 3), game.tree_markers)

    def test_enemy_tree_double_click_plants_with_own_lion_and_mole(self):
        from basicgame import Resource
        self.start_ready()
        game = self.app.state.game
        game.get_piece((6, 3)).rooted = False
        game.tree_markers.add((6, 3))
        game.set_piece((4, 2), Resource("black", "lion"))
        game.set_piece((4, 4), Resource("black", "mole"))
        self.app.state.session.refresh_players()
        self.app.gui.resize_window((780, 650))
        self.render()
        before = game.current_ap
        self.click("board:6,3")
        self.click("board:6,3")
        self.assertTrue(game.get_piece((6, 3)).rooted)
        self.assertEqual(game.get_piece((6, 3)).owner, "white")
        self.assertEqual(game.current_ap, before - 2)

    def test_native_window_events_keep_wrapper_alive(self):
        import gc
        import pygame
        seen = 0
        for action in (self.app.start_g2, self.app.start_g1,
                       lambda: self.app.gui.resize_window((900, 600)), self.app.start_g2):
            action()
            gc.collect()
            for event in pygame.event.get():
                window = getattr(event, "window", None)
                if window is not None:
                    seen += 1
                    self.assertIs(window, self.app.gui.native_window)
            self.render()
            pygame.display.flip()
        self.assertGreater(seen, 0)

    def test_native_rule_card_crops_preserve_existing_artwork(self):
        import pygame
        self.app.start_g1()
        # Artwork bounds captured from the original pixel-scan crop.
        bounds = [(276, 369, 696, 542), (269, 488, 720, 259), (333, 324, 595, 582),
                  (315, 446, 629, 319), (321, 307, 613, 615)]
        for index, coordinates in enumerate(bounds):
            name = f"g1_{index}.png"
            source = self.app.gui.images[name]
            padded = pygame.Rect(coordinates).inflate(10, 10)
            side = max(padded.size)
            rect = pygame.Rect(padded.centerx - side // 2, padded.centery - side // 2, side, side)
            expected = pygame.transform.smoothscale(source.subsurface(rect), (84, 84))
            self.assertEqual(pygame.image.tobytes(self.app.gui.condition_images[name], "RGBA"),
                             pygame.image.tobytes(expected, "RGBA"), name)


if __name__ == "__main__":
    unittest.main()
