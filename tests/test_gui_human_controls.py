"""Human hit targets, time-token feedback and visible cell-border hints."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from basicgame import Resource
from gameengine import PHASE_PLAYING, PHASE_CHECKING_WIN


class HumanControlsTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy", "PYGAME_HIDE_SUPPORT_PROMPT": "1"})
        self.environment.start()
        from main import CheckMateApp
        self.directory = tempfile.TemporaryDirectory()
        self.app = CheckMateApp(settings_path=Path(self.directory.name) / "settings.json")
        self.app.persist = lambda: None

    def tearDown(self):
        import pygame
        pygame.quit()
        self.directory.cleanup()
        self.environment.stop()

    def start(self, mode):
        self.app.start_game(mode)
        self.app.state.session.select_camp("black")
        self.app.state.game.current_ap = 3
        self.app.after_engine_change()

    def render(self):
        board, resources, camps = self.app.get_legal_targets()
        self.app.gui.draw(self.app.state, legal_board_targets=board,
                          legal_resource_targets=resources, legal_camp_targets=camps, mouse_pos=(-1, -1))

    def test_all_modes_share_five_button_positions_and_question_mark_is_not_clickable(self):
        expected = [tuple(self.app.gui.resource_button_rect(index)) for index in range(5)]
        for mode in (1, 2, 3, 1):
            self.start(mode)
            self.render()
            renderer = self.app.gui
            self.assertEqual(len(renderer.resource_buttons), 5)
            self.assertEqual([tuple(renderer.resource_button_rect(index)) for index in range(5)], expected)
            self.assertEqual(renderer.resource_buttons[4][0], "time_token")
            fourth = renderer.resource_button_rect(3)
            region = renderer.find_region_name(fourth.center)
            if mode == 3:
                self.assertEqual(region, "resource:butterfly")
            else:
                self.assertIsNone(region)
                self.assertFalse(any(item.name.startswith("resource:placeholder") for item in renderer.click_regions))
                center = renderer.screen.subsurface(fourth.inflate(-140, -40))
                dark_pixels = [(x, y) for y in range(center.get_height()) for x in range(center.get_width())
                               if max(center.get_at((x, y))[:3]) < 40]
                self.assertTrue(dark_pixels, "The centered question mark must be visible")

    def test_token_hints_follow_current_player_for_either_holder_and_after_turn_boundary(self):
        import gui
        for mode in (1, 2, 3):
            self.start(mode)
            session, game = self.app.state.session, self.app.state.game
            for side in ("black", "white"):
                for holder in ("black", "white"):
                    game.current_player, game.time_token_owner, game.current_ap = side, holder, 3
                    session.refresh_players()
                    self.assertEqual(self.app.get_legal_targets()[2], {side})
                    self.assertIn("time_token", self.app.get_legal_targets()[1])
                    self.render()
                    panel = gui.LEFT_CAMP_RECT if side == "black" else gui.RIGHT_CAMP_RECT
                    pixel = (panel.x + gui.LEGAL_HINT_INSET, panel.y + gui.LEGAL_HINT_INSET)
                    self.assertEqual(tuple(self.app.gui.screen.get_at(pixel))[:3], gui.LEGAL_HINT_COLOR)
                    session.select_camp(side)
                    self.assertEqual(game.time_token_owner, "white" if holder == "black" else "black")
                    self.assertEqual(game.current_ap, 2)
                    self.assertEqual(self.app.get_legal_targets()[2], {side})
            # Selection temporarily disables token exchange; cancellation restores it.
            session.select_board((6, 1))
            self.assertEqual(self.app.get_legal_targets()[2], set())
            self.assertNotIn("time_token", self.app.get_legal_targets()[1])
            session.cancel()
            self.assertEqual(self.app.get_legal_targets()[2], {"white"})
            game.current_ap = 1
            session.select_camp("white")
            self.assertEqual(session.flow.phase, PHASE_CHECKING_WIN)
            self.assertEqual(self.app.get_legal_targets(), (set(), set(), set()))
            session.resolve_checks()
            self.assertEqual(session.flow.phase, PHASE_PLAYING)
            self.assertEqual(game.current_player, "black")
            self.assertEqual(self.app.get_legal_targets()[2], {"black"})
            game.time_token_owner = None
            self.assertEqual(self.app.get_legal_targets()[2], set())
            self.assertNotIn("time_token", self.app.get_legal_targets()[1])

    def test_reserve_hint_requires_the_actual_human_buy_or_selected_sale(self):
        self.start(1)
        session, game = self.app.state.session, self.app.state.game
        game.set_piece((4, 3), Resource("black", "lion"))
        session.refresh_players()
        self.assertTrue(any(action["type"] == "sell_lion" for action in session.legal_actions()))
        self.assertNotIn("lion", self.app.get_legal_targets()[1])
        session.select_board((4, 3))
        self.assertIn("lion", self.app.get_legal_targets()[1])
        session.cancel()
        session.select_board((2, 1))
        self.assertIn("elephant", self.app.get_legal_targets()[1])
        self.assertNotIn("squirrel", self.app.get_legal_targets()[1])
        self.assertEqual(session.select_resource("elephant"), "buy_elephant")
        self.assertEqual(self.app.get_legal_targets()[1], set())

    def test_board_hints_use_full_cell_edges_above_piece_hover_and_selection(self):
        import gui
        self.start(2)
        renderer, game = self.app.gui, self.app.state.game
        for side in ("black", "white"):
            position = (4, 3)
            game.set_piece(position, Resource(side, "tree", rooted=False))
            self.app.state.interaction.selected_pos = position
            renderer.draw_board(self.app.state, "board:4,3", {position})
            rect = renderer.rect_for_logical_cell(*position)
            inset = gui.LEGAL_HINT_INSET
            for pixel in ((rect.left + inset, rect.top + inset),
                          (rect.right - 1 - inset, rect.top + inset),
                          (rect.left + inset, rect.bottom - 1 - inset),
                          (rect.right - 1 - inset, rect.bottom - 1 - inset)):
                self.assertEqual(tuple(renderer.screen.get_at(pixel))[:3], gui.LEGAL_HINT_COLOR)
        game.expanded_corners.add("lower_left")
        game.set_piece((2, 6), Resource("black", "tree", rooted=False))
        renderer.set_mode(game)
        renderer.draw_board(self.app.state, None, {(2, 6)})
        shifted = renderer.rect_for_logical_cell(2, 6)
        self.assertEqual(tuple(renderer.screen.get_at((shifted.x + inset, shifted.y + inset)))[:3], gui.LEGAL_HINT_COLOR)
        self.assertIsNone(renderer.rect_for_logical_cell(2, 5))

    def test_new_special_can_be_selected_and_sold_without_moving(self):
        for mode, kind in ((1, "elephant"), (1, "lion"), (2, "mole"), (3, "butterfly")):
            with self.subTest(kind=kind):
                self.start(mode)
                session, game = self.app.state.session, self.app.state.game
                position = (4, 3)
                game.set_piece(position, Resource("black", kind))
                game.mark_new_piece(position)
                session.refresh_players()
                self.assertIn(position, self.app.get_legal_targets()[0])
                self.app.handle_board_click(position)
                self.assertEqual(session.interaction.selected_pos, position)
                self.assertEqual(self.app.get_legal_targets()[0], set())
                self.assertIn(kind, self.app.get_legal_targets()[1])
                self.app.handle_resource_click(kind)
                self.assertEqual(session.flow.phase, "pending_refund")
                self.assertIsNone(game.get_piece(position))

    def test_payment_selection_draws_green_check_and_cancel_clears_it(self):
        import gui
        self.start(1)
        session = self.app.state.session
        session.select_resource("elephant")
        session.select_board((2, 1))
        self.render()
        rect = self.app.gui.rect_for_logical_cell(2, 1)
        pixel = (rect.right - 24, rect.top + 33)
        self.assertEqual(tuple(self.app.gui.screen.get_at(pixel))[:3], gui.LEGAL_HINT_COLOR)
        session.cancel()
        self.render()
        self.assertNotEqual(tuple(self.app.gui.screen.get_at(pixel))[:3], gui.LEGAL_HINT_COLOR)

    def test_control_switch_refreshes_color_and_direct_action_targets_immediately(self):
        import gui
        self.start(1)
        session = self.app.state.session
        empty = (4, 3)
        revision = session.revision
        self.assertNotIn(empty, self.app.get_legal_targets()[0])
        self.app.gui.agent_play_mode = True
        self.app.gui.player_types["black"] = "llm"
        self.app.apply_settings()
        self.assertEqual(self.app.gui.legal_hint_color, gui.LLM_LEGAL_HINT_COLOR)
        self.assertIn(empty, self.app.get_legal_targets()[0])
        self.render()
        rect = self.app.gui.rect_for_logical_cell(*empty)
        pixel = (rect.left + gui.LEGAL_HINT_INSET, rect.top + gui.LEGAL_HINT_INSET)
        self.assertEqual(tuple(self.app.gui.screen.get_at(pixel))[:3], gui.LLM_LEGAL_HINT_COLOR)
        for master_off in (False, True):
            self.app.gui.agent_play_mode = True
            self.app.gui.player_types["black"] = "llm"
            self.app.apply_settings()
            if master_off:
                self.app.gui.agent_play_mode = False
                self.app.apply_settings()
            else:
                self.app.gui.setting_dropdown = "player:black"
                self.app.select_setting_dropdown("human")
            self.assertEqual(self.app.gui.legal_hint_color, gui.LEGAL_HINT_COLOR)
            self.assertNotIn(empty, self.app.get_legal_targets()[0])
            self.render()
            piece = self.app.gui.rect_for_logical_cell(2, 1)
            self.assertEqual(tuple(self.app.gui.screen.get_at((piece.left + gui.LEGAL_HINT_INSET,
                                                              piece.top + gui.LEGAL_HINT_INSET)))[:3], gui.LEGAL_HINT_COLOR)
        self.assertEqual(session.revision, revision)

    def test_selected_piece_guidance_lists_only_available_actions(self):
        self.start(3)
        session, game = self.app.state.session, self.app.state.game
        game.set_piece((4, 3), Resource("black", "butterfly"))
        session.refresh_players()
        session.select_board((4, 3))
        self.assertIn("destination to move", session.pending_message)
        self.assertIn("shop button to sell", session.pending_message)
        self.assertIn("click this piece again", session.pending_message)
        session.cancel()
        game.mark_new_piece((4, 3))
        session.select_board((4, 3))
        self.assertNotIn("destination to move", session.pending_message)
        self.assertNotIn("click this piece again", session.pending_message)
        self.assertIn("shop button to sell", session.pending_message)
        self.app.gui.agent_play_mode = True
        self.app.gui.player_types["black"] = "llm"
        self.app.apply_settings()
        self.assertEqual(session.interaction.selected_pos, (4, 3))
        self.assertIn("shop button to sell", session.pending_message)
        self.start(2)
        session = self.app.state.session
        session.select_board((2, 3))
        self.assertIn("uproot the tree", session.pending_message)
        self.assertNotIn("shop button to sell", session.pending_message)

    def test_token_tooltip_predicts_actual_holder_for_both_sides_and_parities(self):
        for side in ("black", "white"):
            for holder in ("black", "white"):
                for ap in (2, 3):
                    self.start(1)
                    session, game = self.app.state.session, self.app.state.game
                    game.current_player, game.time_token_owner, game.current_ap = side, holder, ap
                    tooltip = self.app.tooltip_for_region("resource:time_token")
                    session.select_resource("time_token")
                    self.assertIn(f"{game.time_token_owner.capitalize()} will hold the token", tooltip)


if __name__ == "__main__":
    unittest.main()
