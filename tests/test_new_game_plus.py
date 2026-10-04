"""NG+ eligibility, cross-mode starts and saved action-phase restoration."""
import json
import tempfile
import unittest
from pathlib import Path

from basicgame import Resource
from game3 import own_positions
from gameengine import (
    GameSession, PHASE_CHOOSE_TOKEN, PHASE_PLAYING, PHASE_SELECT_TARGET,
    PHASE_SELECT_COST, PHASE_PENDING_REFUND, PHASE_PENDING_ELEPHANT_BONUS,
    PHASE_GAME_OVER, PHASE_TIME_WISH, encode_action_id,
    save_session_file, load_session_file,
)
import ui_text


def act(session, action_type, **params):
    return session.submit(encode_action_id(action_type, params), revision=session.revision)


def terminal(mode, token=None):
    s = GameSession.new_game(mode)
    s.game.time_token_owner = token
    s.end_session("mate", loser="white")
    return s


class NewGamePlusTests(unittest.TestCase):
    def test_fresh_starts_are_standard_in_every_mode(self):
        for mode in (1, 2, 3):
            s = GameSession.new_game(mode)
            self.assertEqual(s.flow.phase, PHASE_CHOOSE_TOKEN)
            self.assertFalse(s.can_start_new_game_plus())
            self.assertEqual({a["type"] for a in s.legal_actions()}, {"choose_initial_token"})

    def test_eligibility_uses_any_owned_piece_and_known_winner(self):
        self.assertFalse(GameSession().can_start_new_game_plus())
        s = terminal(1, token="white")
        self.assertTrue(s.can_start_new_game_plus())
        self.assertTrue(s.snapshot()["new_game_plus_available"])
        s.game.board_matrix = [[None for _ in range(9)] for _ in range(7)]
        self.assertFalse(s.can_start_new_game_plus())
        s.game.set_piece((0, 3), Resource("black", "pine"))
        self.assertFalse(s.can_start_new_game_plus())
        s.game.set_piece((4, 3), Resource("white", "squirrel"))
        self.assertTrue(s.can_start_new_game_plus())
        s.mat_loser = s.game.mate_loser = None
        self.assertFalse(s.can_start_new_game_plus())
        # The wish phase already has a winner; no extra token/phase gate.
        s.flow.phase = PHASE_TIME_WISH
        s.flow.time_wish_winner = "white"
        self.assertTrue(s.can_start_new_game_plus())

    def test_all_nine_cross_mode_starts_use_fresh_base_positions(self):
        for source_mode in (1, 2, 3):
            for target_mode in (1, 2, 3):
                with self.subTest(source=source_mode, target=target_mode):
                    source = terminal(source_mode, token="white")
                    source.game.current_player = "white"
                    source.game.current_ap = source.game.max_ap = 30
                    source.game.new_piece = [(2, 1)]
                    source.game.set_piece((4, 4), Resource("white", "lion"))
                    if source_mode == 2:
                        source.game.tree_markers.add((2, 3))
                        source.game.expanded_corners.add("lower_left")
                    before = source.snapshot()
                    new = GameSession.new_game(target_mode, previous_session=source)
                    self.assertEqual(new.flow.phase, PHASE_PLAYING)
                    self.assertEqual(new.game.current_player, "black")
                    self.assertEqual((new.game.current_ap, new.game.max_ap), (1, 1))
                    self.assertIsNone(new.game.time_token_owner)
                    self.assertEqual(new.game.new_piece, [])
                    self.assertIsNone(new.result()["winner"])
                    self.assertFalse(new.can_start_new_game_plus())
                    self.assertNotIn("choose_initial_token", {a["type"] for a in new.legal_actions()})
                    self.assertEqual(new.snapshot()["board"], GameSession.new_game(target_mode).snapshot()["board"])
                    if target_mode == 2:
                        self.assertEqual(new.game.tree_markers, set())
                        self.assertEqual(new.game.expanded_corners, set())
                        self.assertFalse(new.game.skip_token_selection)
                    if target_mode == 3:
                        self.assertEqual(new.game.g3_turn.start_positions, own_positions(new.game))
                        self.assertEqual(new.game.g3_turn.move_count, 0)
                        self.assertFalse(new.game.g3_turn.extra_turn)
                    self.assertEqual(source.snapshot(), before)

    def test_saved_ng_plus_resumes_play_and_future_turns_without_a_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            for mode in (1, 2, 3):
                s = GameSession.new_game(mode, previous_session=terminal(1))
                p = save_session_file(s, Path(directory) / f"g{mode}.json")
                data = json.loads(p.read_text())
                self.assertFalse(any(key in data for key in ("ng_plus", "is_ng_plus", "gi_plus")))
                restored = load_session_file(p)
                self.assertEqual(restored.flow.phase, PHASE_PLAYING)
                self.assertIsNone(restored.game.time_token_owner)
                act(restored, "select_piece", at=[2, 1])
                act(restored, "move", **{"from": [2, 1], "to": [3, 1]})
                self.assertEqual(restored.game.current_player, "white")
                self.assertEqual(restored.flow.phase, PHASE_PLAYING)
                save_session_file(restored, p)
                again = load_session_file(p)
                self.assertEqual(again.game.current_player, "white")
                self.assertEqual(again.game.current_ap, 2)
                self.assertEqual(again.flow.phase, PHASE_PLAYING)
                self.assertIsNone(again.game.time_token_owner)

    def test_phase_restore_distinguishes_standard_setup_and_selected_piece(self):
        with tempfile.TemporaryDirectory() as directory:
            for mode in (1, 2, 3):
                p = Path(directory) / f"phase{mode}.json"
                standard = GameSession.new_game(mode)
                save_session_file(standard, p)
                self.assertEqual(load_session_file(p).flow.phase, PHASE_CHOOSE_TOKEN)
                ng = GameSession.new_game(mode, previous_session=terminal(2))
                act(ng, "select_piece", at=[2, 1])
                save_session_file(ng, p)
                selected = load_session_file(p)
                self.assertEqual(selected.flow.phase, PHASE_SELECT_TARGET)
                self.assertEqual(selected.interaction.selected_pos, (2, 1))
                self.assertEqual(selected.game.current_player, "black")
                act(selected, "move", **{"from": [2, 1], "to": [3, 1]})

    def test_pending_elephant_bonus_and_refund_resume_without_token(self):
        with tempfile.TemporaryDirectory() as directory:
            s = GameSession.new_game(3, previous_session=terminal(1))
            s.game.current_ap = s.game.max_ap = 5
            act(s, "select_piece", at=[2, 3])
            act(s, "move", **{"from": [2, 3], "to": [3, 3]})
            p = save_session_file(s, Path(directory) / "pending.json")
            s = load_session_file(p)
            self.assertEqual(s.flow.phase, PHASE_PENDING_ELEPHANT_BONUS)
            self.assertEqual(s.game.g3_turn.move_count, 1)
            act(s, "place_elephant_bonus", at=[2, 3])
            self.assertEqual(s.game.g3_turn.move_count, 1)
            act(s, "sell_elephant", at=[3, 3])
            save_session_file(s, p)
            restored = load_session_file(p)
            self.assertEqual(restored.flow.phase, PHASE_PENDING_REFUND)
            self.assertEqual(restored.flow.pending_refund_count, 1)
            act(restored, "place_refund", at=[3, 3])
            self.assertEqual(restored.flow.phase, PHASE_PLAYING)
            self.assertTrue(restored.game.g3_turn.resources_changed)

    def test_loaded_terminal_and_ng_plus_terminal_allow_another_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            p = save_session_file(terminal(1), Path(directory) / "terminal.json")
            restored = load_session_file(p)
            self.assertTrue(restored.can_start_new_game_plus())
            ng = GameSession.new_game(2, previous_session=restored)
            ng.end_session("mate", loser="black")
            save_session_file(ng, p)
            ng_terminal = load_session_file(p)
            self.assertEqual(ng_terminal.result()["winner"], "white")
            next_ng = GameSession.new_game(3, previous_session=ng_terminal)
            self.assertEqual(next_ng.flow.phase, PHASE_PLAYING)
            self.assertIsNone(next_ng.game.time_token_owner)

    def test_older_finished_wish_restores_winner_from_saved_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            s = terminal(2)
            s.flow.time_wish_winner = "black"
            s.mat_loser = s.game.mate_loser = None
            p = save_session_file(s, Path(directory) / "wish.json")
            restored = load_session_file(p)
            self.assertEqual(restored.result()["winner"], "black")
            self.assertTrue(restored.can_start_new_game_plus())

    def test_legacy_missing_phase_is_inferred_without_overriding_explicit_play(self):
        with tempfile.TemporaryDirectory() as directory:
            p = save_session_file(GameSession.new_g2(), Path(directory) / "old.json")
            data = json.loads(p.read_text())
            data.pop("flow")
            p.write_text(json.dumps(data))
            self.assertEqual(load_session_file(p).flow.phase, PHASE_CHOOSE_TOKEN)
            data["skip_token_selection"] = True
            p.write_text(json.dumps(data))
            self.assertEqual(load_session_file(p).flow.phase, PHASE_PLAYING)
            data["skip_token_selection"] = False
            data["flow"] = {"phase": PHASE_PLAYING}
            p.write_text(json.dumps(data))
            self.assertEqual(load_session_file(p).flow.phase, PHASE_PLAYING)

    def test_wish_hint_and_no_automatic_continuation(self):
        self.assertEqual(ui_text.TIME_WISH_RESOLVED_DIALOG, "Save this game, then start New Game+.")
        for mode in (2, 3):
            s = GameSession.new_game(mode)
            s.flow.phase = PHASE_TIME_WISH
            s.flow.time_wish_winner = "black"
            s.game.time_token_owner = s.game.current_player = "white"
            with tempfile.TemporaryDirectory() as directory:
                s.wish_save_directory = directory
                act(s, "time_wish", side="white")
                self.assertEqual(s.flow.phase, PHASE_GAME_OVER)
                self.assertEqual(s.pending_message, ui_text.NEW_GAME_PLUS_HINT)
                self.assertTrue(s.can_start_new_game_plus())
                self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
