"""G3 turn boundaries, compound actions, pattern checks and persistence."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from basicgame import Resource, Chessboard, get_legal_moves_for_piece, is_player_accessible
from game3 import START_PATTERN, END_PATTERN, G3Turn, matches_pattern, own_positions, get_g3_condition_results
from gameengine import GameSession, PHASE_PLAYING, PHASE_CHECKING_WIN, PHASE_TIME_WISH, PHASE_GAME_OVER, EngineError, encode_action_id, save_session_file, load_session_file


def act(session, action_type, **params):
    return session.submit(encode_action_id(action_type, params), revision=session.revision)


def move(session, start, end):
    act(session, "select_piece", at=list(start))
    return act(session, "move", **{"from": list(start), "to": list(end)})


def ready(ap=20):
    session = GameSession.new_g3()
    act(session, "choose_initial_token", side="black")
    session.game.current_ap = session.game.max_ap = ap
    return session


def add(session, position, kind, owner="black"):
    session.game.set_piece(position, Resource(owner, kind))
    session.refresh_players()


class G3Tests(unittest.TestCase):
    def test_setup_mode_isolation_and_agent(self):
        s = ready()
        self.assertEqual(s.players["black"].num_squirrels, 2)
        self.assertEqual(s.game.get_piece((2, 3)).kind, "elephant")
        self.assertIsNone(s.game.get_piece((0, 3)))
        self.assertFalse(is_player_accessible(s.game, (2, 6)))
        self.assertNotIn("tree_markers", s.snapshot())
        self.assertFalse(hasattr(s.game, "expanded_corners"))
        for factory in (GameSession.new_g1, GameSession.new_g2):
            other = factory()
            self.assertFalse(hasattr(other.game, "g3_turn"))
            self.assertNotIn("g3_turn", other.snapshot())
        code = '''import agenthelper, sys
agenthelper.configure(autosave=False)
assert agenthelper.create_game(session_id="g3-check",gamemode=3)["ok"]
s=agenthelper.get_state("g3-check")
assert s["ok"] and "g3_turn" in s and "tree_markers" not in s
assert len(agenthelper.get_coordinates("g3-check")["actionable_cells"]) == 25
assert "pygame" not in sys.modules
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_eight_way_movement_and_lion_control(self):
        s = ready()
        add(s, (4, 3), "butterfly")
        self.assertEqual(len(get_legal_moves_for_piece(s.game, s.players, (4, 3))), 8)
        move(s, (4, 3), (5, 4))
        self.assertEqual(s.game.current_ap, 19)
        self.assertEqual(s.game.g3_turn.move_count, 1)
        add(s, (4, 3), "butterfly", "white")
        self.assertFalse(get_legal_moves_for_piece(s.game, s.players, (4, 3)))
        add(s, (3, 2), "lion")
        move(s, (4, 3), (3, 4))
        self.assertEqual(s.game.current_ap, 17)
        self.assertEqual(s.game.g3_turn.move_count, 2)
        self.assertNotIn("butterfly_extra_turn:at=3,4", {a["action_id"] for a in s.legal_actions()})

    def test_extra_turn_uses_remaining_ap_records_fresh_start_and_prevents_chaining(self):
        s = ready(5)
        add(s, (4, 3), "butterfly")
        add(s, (4, 4), "butterfly")
        move(s, (2, 1), (3, 1))
        old_max = s.game.max_ap
        s.game.mark_new_piece((4, 4))
        act(s, "butterfly_extra_turn", at=[4, 3])
        self.assertEqual(s.game.current_player, "black")
        self.assertEqual(s.game.current_ap, 3)
        self.assertEqual(s.game.max_ap, old_max)
        self.assertEqual(s.flow.last_condition_results[1], True)
        self.assertTrue(s.game.g3_turn.extra_turn)
        self.assertEqual(s.game.g3_turn.start_positions, own_positions(s.game))
        self.assertEqual(s.game.g3_turn.move_count, 0)
        self.assertFalse(s.game.g3_turn.resources_changed)
        self.assertEqual(s.game.new_piece, [])
        self.assertNotIn("butterfly_extra_turn", {a["type"] for a in s.legal_actions()})
        act(s, "fast_time_token")
        self.assertEqual(s.game.current_player, "white")
        self.assertEqual(s.game.max_ap, 6)
        self.assertFalse(s.game.g3_turn.extra_turn)
        act(s, "fast_time_token")
        self.assertEqual(s.game.current_player, "black")
        self.assertIn("butterfly_extra_turn", {a["type"] for a in s.legal_actions()})

    def test_new_butterfly_cannot_act_and_last_ap_passes_to_opponent(self):
        s = ready(3)
        add(s, (4, 3), "butterfly")
        s.game.mark_new_piece((4, 3))
        self.assertFalse(get_legal_moves_for_piece(s.game, s.players, (4, 3)))
        self.assertNotIn("butterfly_extra_turn", {a["type"] for a in s.legal_actions()})
        s.game.new_piece = []
        s.game.current_ap = 1
        act(s, "butterfly_extra_turn", at=[4, 3])
        self.assertEqual(s.game.current_player, "white")
        self.assertEqual(s.game.current_ap, 4)
        self.assertFalse(s.game.g3_turn.extra_turn)

    def test_patterns_rotate_translate_but_do_not_mirror_or_ignore_extra_pieces(self):
        for pattern in (START_PATTERN, END_PATTERN):
            rotated = pattern
            for _ in range(4):
                self.assertTrue(matches_pattern({(x + 5, y - 8) for x, y in rotated}, pattern))
                rotated = {(-y, x) for x, y in rotated}
            self.assertFalse(matches_pattern({(-x, y) for x, y in pattern}, pattern))
            self.assertFalse(matches_pattern(pattern | {(5, 5)}, pattern))
        # The two illustrated shapes are themselves related by a 180-degree rotation.
        self.assertTrue(matches_pattern(START_PATTERN, END_PATTERN))

    def test_elephant_chain_and_optional_squirrel_count_as_one_move(self):
        for bonus in (True, False):
            with self.subTest(bonus=bonus):
                s = ready()
                add(s, (3, 3), "squirrel", "white")
                add(s, (4, 3), "lion", "white")
                move(s, (2, 3), (3, 3))
                self.assertEqual(s.game.g3_turn.move_count, 1)
                self.assertFalse(s.game.g3_turn.resources_changed)
                if bonus:
                    act(s, "place_elephant_bonus", at=[2, 3])
                else:
                    act(s, "skip_elephant_bonus")
                self.assertEqual(s.game.g3_turn.move_count, 1)
                self.assertFalse(s.game.g3_turn.resources_changed)
                self.assertEqual(s.game.current_ap, 19)

    def test_ordinary_placement_and_committed_trades_only(self):
        s = ready()
        act(s, "place_squirrel", at=[4, 3])
        self.assertTrue(s.game.g3_turn.resources_changed)
        self.assertEqual(s.game.g3_turn.move_count, 0)
        for cancelled in (True, False):
            with self.subTest(cancelled=cancelled):
                s = ready()
                for pos in ((3, 1), (3, 2)):
                    add(s, pos, "squirrel")
                self.assertEqual(s.select_resource("butterfly"), "buy_butterfly")
                for pos in ((2, 1), (2, 5), (3, 1), (3, 2)):
                    act(s, "select_cost", kind="butterfly", at=list(pos))
                self.assertFalse(s.game.g3_turn.resources_changed)
                if cancelled:
                    act(s, "cancel")
                    self.assertEqual(s.players["black"].num_squirrels, 4)
                    self.assertEqual(s.game.current_ap, 20)
                else:
                    act(s, "buy_butterfly", at=[4, 3])
                    self.assertEqual(s.players["black"].num_squirrels, 0)
                    self.assertTrue(s.game.g3_turn.resources_changed)
                    self.assertEqual(s.game.current_ap, 19)
                self.assertEqual(s.game.g3_turn.resources_changed, not cancelled)

    def test_sale_refunds_two_squirrels_and_cancel_rolls_back(self):
        for cancelled in (True, False):
            s = ready()
            add(s, (4, 3), "butterfly")
            act(s, "sell_butterfly", at=[4, 3])
            act(s, "place_refund", at=[4, 3])
            self.assertFalse(s.game.g3_turn.resources_changed)
            if cancelled:
                act(s, "cancel")
                self.assertEqual(s.game.get_piece((4, 3)).kind, "butterfly")
                self.assertEqual(s.players["black"].num_squirrels, 2)
                self.assertEqual(s.game.current_ap, 20)
            else:
                act(s, "place_refund", at=[4, 4])
                self.assertEqual(s.players["black"].num_squirrels, 4)
                self.assertTrue(s.game.g3_turn.resources_changed)
                self.assertEqual(s.game.current_ap, 19)

    def test_sale_can_refund_into_vacated_square_on_nearly_full_board(self):
        s = ready()
        for y in range(1, 6):
            for x in range(2, 7):
                add(s, (x, y), "squirrel", "white")
        add(s, (4, 3), "butterfly")
        s.game.set_piece((4, 4), None)
        s.refresh_players()
        act(s, "sell_butterfly", at=[4, 3])
        act(s, "place_refund", at=[4, 3])
        act(s, "place_refund", at=[4, 4])
        self.assertEqual(s.players["black"].num_squirrels, 2)

    def test_pending_check_and_extra_turn_survive_save(self):
        s = ready(5)
        add(s, (4, 3), "butterfly")
        move(s, (2, 1), (3, 1))
        s.resolve_checks_immediately = False
        act(s, "butterfly_extra_turn", at=[4, 3])
        self.assertEqual(s.flow.phase, PHASE_CHECKING_WIN)
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "session.json"
            save_session_file(s, p)
            data = json.loads(p.read_text())
            self.assertTrue(data["g3_turn"]["pending_extra_turn"])
            self.assertNotIn("tree_markers", data)
            restored = load_session_file(p)
            self.assertEqual(restored.flow.phase, PHASE_PLAYING)
            self.assertEqual(restored.game.current_ap, 3)
            self.assertTrue(restored.game.g3_turn.extra_turn)
            self.assertEqual(restored.game.g3_turn.start_positions, own_positions(restored.game))
            save_session_file(restored, p)
            again = load_session_file(p)
            self.assertTrue(again.game.g3_turn.extra_turn)
            self.assertNotIn("butterfly_extra_turn", {a["type"] for a in again.legal_actions()})

    def winning_position(self):
        s = ready(2)
        s.game.board_matrix = [[None for _ in range(9)] for _ in range(7)]
        for pos in {(x + 2, y + 1) for x, y in START_PATTERN}:
            add(s, pos, "squirrel")
        add(s, (2, 1), "lion")
        add(s, (3, 2), "butterfly")
        add(s, (6, 5), "squirrel", "white")
        s.game.time_token_owner = "white"
        s.game.g3_turn = G3Turn.capture(s.game)
        return s

    def test_g3_legal_move_wins_then_wish_ends_without_save(self):
        s = self.winning_position()
        move(s, (6, 5), (5, 5))
        self.assertEqual(s.flow.last_condition_results, [True] * 5)
        self.assertEqual(s.flow.phase, PHASE_TIME_WISH)
        with tempfile.TemporaryDirectory() as directory:
            s.wish_save_directory = directory
            act(s, "time_wish", side="white")
            self.assertEqual(s.flow.phase, PHASE_GAME_OVER)
            self.assertEqual(s.result()["winner"], "black")
            self.assertEqual(list(Path(directory).iterdir()), [])
            p = save_session_file(s, Path(directory) / "finished.json")
            self.assertEqual(load_session_file(p).result()["winner"], "black")

    def test_butterfly_check_uses_old_records_before_starting_extra_turn(self):
        s = self.winning_position()
        s.game.current_ap = 4
        move(s, (6, 5), (5, 5))
        self.assertEqual(s.flow.phase, PHASE_PLAYING)
        act(s, "butterfly_extra_turn", at=[3, 2])
        self.assertEqual(s.flow.last_condition_results, [True] * 5)
        self.assertEqual(s.flow.phase, PHASE_TIME_WISH)
        self.assertEqual(s.flow.time_wish_winner, "black")
        self.assertFalse(s.game.g3_turn.extra_turn)

    def test_g1_victory_finishes_directly_and_legacy_wish_loads_as_finished(self):
        s = GameSession.new_g1()
        act(s, "choose_initial_token", side="white")
        for row in s.game.board_matrix:
            for x, piece in enumerate(row):
                if piece and piece.is_piece:
                    row[x] = None
        for pos in ((2, 2), (3, 4), (4, 1), (5, 3), (6, 4)):
            add(s, pos, "squirrel")
        move(s, (6, 4), (6, 5))
        self.assertEqual(s.flow.last_condition_results, [True] * 5)
        self.assertEqual(s.flow.phase, PHASE_GAME_OVER)
        self.assertEqual(s.result()["winner"], "black")
        self.assertFalse(s.legal_actions())
        with tempfile.TemporaryDirectory() as directory:
            p = save_session_file(s, Path(directory) / "g1.json")
            self.assertEqual(load_session_file(p).result()["winner"], "black")
            data = json.loads(p.read_text())
            self.assertNotIn("g3_turn", data)
            self.assertNotIn("tree_markers", data)
            data["flow"]["phase"] = PHASE_TIME_WISH
            data["flow"]["time_wish_winner"] = "black"
            data["finished"] = False
            p.write_text(json.dumps(data))
            old = load_session_file(p)
            self.assertEqual(old.flow.phase, PHASE_GAME_OVER)
            self.assertEqual(old.result()["winner"], "black")


if __name__ == "__main__":
    unittest.main()
