"""G2 rule and persistence checks; run with python -m unittest discover -s tests."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from basicgame import Resource, get_empty_accessible_cells, get_legal_moves_for_piece, is_player_accessible
from game1 import get_g1_condition_results
from game2 import get_g2_condition_results
from gameengine import (
    EngineError, GameSession, PHASE_PENDING_PIECE_LIMIT, PHASE_PLAYING,
    PHASE_SELECT_COST, PHASE_TIME_WISH, encode_action_id, load_session_file,
    save_session_file,
)
import agenthelper


def act(session, kind, **params):
    return session.submit(encode_action_id(kind, params), revision=session.revision)


def move(session, start, end):
    act(session, "select_piece", at=list(start))
    return act(session, "move", **{"from": list(start), "to": list(end)})


def ready():
    session = GameSession.new_g2()
    act(session, "choose_initial_token", side="black")
    session.game.current_ap = session.game.max_ap = 20
    return session


class G2Tests(unittest.TestCase):
    def test_initial_setup(self):
        s = GameSession.new_g2()
        self.assertEqual(s.players["black"].num_pieces, 3)
        self.assertEqual(s.players["white"].num_squirrels, 2)
        self.assertIsNone(s.game.get_piece((0, 3)))
        self.assertIsNone(s.game.get_piece((8, 3)))
        self.assertTrue(s.game.get_piece((2, 3)).rooted)
        self.assertEqual(len(get_empty_accessible_cells(s.game)), 19)
        self.assertEqual(get_legal_moves_for_piece(s.game, s.players, (2, 3)), set())

    def test_uproot_shift_and_diagonal_move(self):
        s = ready()
        carried = s.game.get_piece((2, 5))
        s.game.mark_new_piece((2, 5))
        act(s, "uproot_tree", at=[2, 3])
        self.assertFalse(s.game.get_piece((2, 3)).rooted)
        self.assertIs(s.game.get_piece((2, 6)), carried)
        self.assertTrue(s.game.is_new_piece((2, 6)))
        self.assertFalse(is_player_accessible(s.game, (2, 5)))
        self.assertTrue(is_player_accessible(s.game, (2, 6)))
        self.assertEqual(s.game.tree_markers, set())
        self.assertEqual(get_legal_moves_for_piece(s.game, s.players, (2, 3)), {(3, 2), (3, 4)})
        move(s, (2, 3), (3, 4))
        self.assertEqual(s.game.tree_markers, {(2, 3)})
        self.assertEqual(s.game.current_ap, 18)
        cells = [(x, y) for y in range(7) for x in range(9) if is_player_accessible(s.game, (x, y))]
        self.assertEqual(len(cells), 25)

    def test_rift_rejects_placement_without_mutation(self):
        s = ready()
        act(s, "uproot_tree", at=[2, 3])
        before = json.dumps(s.snapshot(), sort_keys=True)
        with self.assertRaises(EngineError):
            act(s, "place_squirrel", at=[2, 5])
        self.assertEqual(before, json.dumps(s.snapshot(), sort_keys=True))
        self.assertEqual(get_legal_moves_for_piece(s.game, s.players, (2, 6)), set())

    def test_plant_requires_marker_and_own_mole_and_restores_corner(self):
        s = ready()
        act(s, "uproot_tree", at=[2, 3])
        move(s, (2, 3), (3, 4))
        move(s, (3, 4), (2, 3))
        self.assertNotIn("plant_tree", {a["type"] for a in s.legal_actions()})
        s.game.set_piece((5, 5), Resource("white", "mole"))
        self.assertNotIn("plant_tree", {a["type"] for a in s.legal_actions()})
        s.game.set_piece((5, 5), Resource("black", "mole"))
        s.refresh_players()
        act(s, "plant_tree", at=[2, 3])
        self.assertTrue(s.game.get_piece((2, 3)).rooted)
        self.assertEqual(s.game.expanded_corners, set())
        self.assertEqual(s.game.get_piece((2, 5)).kind, "squirrel")
        self.assertIsNone(s.game.get_piece((2, 6)))
        self.assertIn((2, 3), s.game.tree_markers)
        self.assertFalse(is_player_accessible(s.game, (2, 6)))

    def test_plant_works_on_either_red_marker(self):
        s = ready()
        act(s, "uproot_tree", at=[2, 3])
        tree = s.game.get_piece((2, 3))
        other_tree = s.game.get_piece((6, 3))
        s.game.set_piece((2, 3), None)
        s.game.set_piece((4, 3), other_tree)
        s.game.set_piece((6, 3), tree)
        s.game.tree_markers.add((6, 3))
        s.game.set_piece((4, 5), Resource("black", "mole"))
        s.refresh_players()
        act(s, "plant_tree", at=[6, 3])
        self.assertTrue(tree.rooted)
        self.assertEqual(s.game.expanded_corners, {"lower_left"})
        act(s, "uproot_tree", at=[6, 3])
        self.assertEqual(s.game.expanded_corners, {"lower_left", "upper_right"})
        self.assertEqual(s.game.get_piece((6, 0)).owner, "white")
        act(s, "plant_tree", at=[6, 3])
        self.assertEqual(s.game.expanded_corners, {"lower_left"})
        self.assertEqual(s.game.get_piece((6, 1)).owner, "white")

    def test_enemy_plant_requires_both_own_animals_two_ap_and_marker(self):
        for kinds, ap, marked, expected in (
            ((), 5, True, False),
            (("mole",), 5, True, False),
            (("lion",), 5, True, False),
            (("lion", "mole"), 1, True, False),
            (("lion", "mole"), 5, False, False),
            (("lion", "mole"), 5, True, True),
        ):
            with self.subTest(kinds=kinds, ap=ap, marked=marked):
                s = ready()
                s.game.get_piece((6, 3)).rooted = False
                if marked:
                    s.game.tree_markers.add((6, 3))
                for x, kind in enumerate(kinds, 3):
                    s.game.set_piece((x, 4), Resource("black", kind))
                s.game.set_piece((5, 4), Resource("white", "mole"))
                s.game.set_piece((5, 2), Resource("white", "lion"))
                s.game.current_ap = ap
                s.refresh_players()
                actions = {a["type"] for a in s.legal_actions() if a["params"].get("at") == [6, 3]}
                self.assertEqual("plant_tree" in actions, expected)
                before = json.dumps(s.snapshot(), sort_keys=True)
                if expected:
                    act(s, "plant_tree", at=[6, 3])
                    self.assertEqual(s.game.current_ap, ap - 2)
                    self.assertTrue(s.game.get_piece((6, 3)).rooted)
                    self.assertEqual(s.game.get_piece((6, 3)).owner, "white")
                    self.assertNotIn("uproot_tree", {a["type"] for a in s.legal_actions() if a["params"].get("at") == [6, 3]})
                else:
                    with self.assertRaises(EngineError):
                        act(s, "plant_tree", at=[6, 3])
                    self.assertEqual(before, json.dumps(s.snapshot(), sort_keys=True))

    def test_enemy_plant_and_later_uproot_follow_site_after_save(self):
        s = ready()
        act(s, "uproot_tree", at=[2, 3])
        s.game.current_player = "white"
        act(s, "uproot_tree", at=[6, 3])
        black_tree = s.game.get_piece((2, 3))
        white_tree = s.game.get_piece((6, 3))
        s.game.set_piece((2, 3), white_tree)
        s.game.set_piece((6, 3), black_tree)
        s.game.tree_markers.update({(2, 3), (6, 3)})
        s.game.current_player = "black"
        s.game.set_piece((4, 4), Resource("black", "mole"))
        s.game.set_piece((4, 2), Resource("black", "lion"))
        s.refresh_players()
        carried = s.game.get_piece((2, 6))
        s.game.mark_new_piece((2, 6))
        s.select_board((2, 3))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "enemy-plant.json"
            save_session_file(s, path)
            s = load_session_file(path)
            before = s.game.current_ap
            s.select_board((2, 3))
            self.assertEqual(s.game.current_ap, before - 2)
            self.assertEqual(s.game.expanded_corners, {"upper_right"})
            self.assertEqual(s.game.get_piece((2, 5)).kind, carried.kind)
            self.assertTrue(s.game.is_new_piece((2, 5)))
            self.assertTrue(s.game.get_piece((2, 3)).rooted)
            self.assertEqual(s.game.get_piece((2, 3)).owner, "white")
            self.assertTrue(is_player_accessible(s.game, (2, 5)))
            self.assertFalse(is_player_accessible(s.game, (2, 6)))
            s.game.current_player = "white"
            act(s, "uproot_tree", at=[2, 3])
            self.assertEqual(s.game.expanded_corners, {"lower_left", "upper_right"})
            self.assertEqual(s.game.get_piece((2, 6)).kind, carried.kind)

    def test_mole_purchase_sale_cancel_and_move(self):
        s = ready()
        types = {a["type"] for a in s.legal_actions()}
        self.assertIn("buy_mole", types)
        self.assertNotIn("buy_elephant", types)
        self.assertEqual(s.select_resource("mole"), "buy_mole")
        self.assertEqual(s.flow.phase, PHASE_SELECT_COST)
        s.select_board((2, 1))
        s.select_board((2, 5))
        self.assertIsNone(s.game.get_piece((2, 1)))
        s.cancel()
        self.assertEqual(s.players["black"].num_squirrels, 2)
        s.select_resource("mole")
        s.select_board((2, 1))
        s.select_board((2, 5))
        s.select_board((4, 4))
        self.assertEqual(s.game.get_piece((4, 4)).kind, "mole")
        self.assertEqual(s.players["black"].num_squirrels, 0)
        self.assertEqual(s.game.current_ap, 19)
        self.assertEqual(get_legal_moves_for_piece(s.game, s.players, (4, 4)), set())
        s.game.new_piece.clear()
        self.assertEqual(get_legal_moves_for_piece(s.game, s.players, (4, 4)), {(3, 4), (5, 4), (4, 3), (4, 5)})
        move(s, (4, 4), (4, 3))
        s.select_board((4, 3))
        s.select_resource("mole")
        s.select_board((4, 4))
        self.assertIsNone(s.game.get_piece((4, 3)))
        self.assertEqual(s.players["black"].num_squirrels, 1)
        self.assertEqual(s.game.current_ap, 17)

    def test_rooted_tree_cannot_be_removed(self):
        s = ready()
        s.flow.phase = PHASE_PENDING_PIECE_LIMIT
        s.flow.pending_piece_limit_owner = "black"
        self.assertNotIn([2, 3], [a["params"].get("at") for a in s.legal_actions()])
        s.select_board((2, 3))
        self.assertTrue(s.game.get_piece((2, 3)).rooted)
        with self.assertRaises(EngineError):
            act(s, "remove_piece_limit", at=[2, 3])

    def test_lion_controls_uprooted_tree_with_two_ap(self):
        s = ready()
        s.game.set_piece((4, 4), Resource("black", "lion"))
        s.refresh_players()
        self.assertEqual(get_legal_moves_for_piece(s.game, s.players, (6, 3)), set())
        s.game.get_piece((6, 3)).rooted = False
        before = s.game.current_ap
        move(s, (6, 3), (5, 2))
        self.assertEqual(s.game.current_ap, before - 2)
        self.assertEqual(s.game.get_piece((5, 2)).owner, "white")
        self.assertIn((6, 3), s.game.tree_markers)

    def test_save_restores_tree_markers_corners_and_pending_purchase(self):
        with tempfile.TemporaryDirectory() as directory:
            s = ready()
            act(s, "uproot_tree", at=[2, 3])
            move(s, (2, 3), (3, 4))
            s.select_resource("mole")
            s.select_board((2, 1))
            path = Path(directory) / "session.json"
            save_session_file(s, path)
            restored = load_session_file(path)
            self.assertEqual(restored.game.tree_markers, {(2, 3)})
            self.assertEqual(restored.game.expanded_corners, {"lower_left"})
            self.assertFalse(restored.game.get_piece((3, 4)).rooted)
            self.assertEqual(restored.flow.phase, PHASE_SELECT_COST)
            self.assertEqual(restored.interaction.selected_cost, [(2, 1)])
            restored.select_board((2, 6))
            restored.select_board((4, 4))
            self.assertEqual(restored.game.get_piece((4, 4)).kind, "mole")

    def test_legal_play_reaches_all_seven_conditions_and_wish_ends_without_continuation(self):
        with tempfile.TemporaryDirectory() as directory:
            s = GameSession.new_g2()
            s.wish_save_directory = directory
            act(s, "choose_initial_token", side="white")
            move(s, (2, 5), (3, 5))
            act(s, "uproot_tree", at=[6, 3])
            move(s, (6, 3), (5, 4))
            act(s, "fast_time_token")
            move(s, (5, 4), (4, 3))
            move(s, (4, 3), (3, 4))
            move(s, (3, 4), (2, 5))
            act(s, "move_time_token")
            act(s, "uproot_tree", at=[2, 3])
            self.assertEqual(s.game.get_piece((2, 6)).kind, "tree")
            move(s, (2, 3), (3, 4))
            move(s, (3, 5), (4, 5))
            move(s, (2, 1), (3, 1))
            act(s, "move_time_token")
            move(s, (2, 6), (3, 5))
            move(s, (6, 5), (5, 5))
            move(s, (5, 5), (5, 4))
            move(s, (5, 4), (6, 4))
            move(s, (6, 4), (5, 4))
            move(s, (5, 4), (5, 5))
            self.assertEqual(s.flow.phase, PHASE_TIME_WISH)
            self.assertEqual(s.flow.time_wish_winner, "white")
            self.assertEqual(s.condition_results, [True] * 7)
            act(s, "time_wish", side="black")
            self.assertTrue(s.is_over())
            self.assertIsNone(s.game.time_token_owner)
            self.assertIsNone(s.wish_continuation_path)
            self.assertEqual(s.result()["winner"], "white")
            self.assertEqual(list(Path(directory).glob("*.json")), [])

    def test_condition_thresholds_and_missing_tree(self):
        s = ready()
        self.assertEqual(get_g2_condition_results(s.players["black"], s.game), [False, True, True, True, False, False, False])
        s.game.set_piece((6, 3), None)
        self.assertEqual(get_g2_condition_results(s.players["black"], s.game), [False] * 7)

    def test_version1_corner_save_preserves_occupants_and_pending_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            s = ready()
            act(s, "uproot_tree", at=[2, 3])
            path = Path(directory) / "session.json"
            save_session_file(s, path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("g2_corner_layout")
            data["expanded_corners"] = ["black"]
            board = data["board_matrix"]
            board[5][2], board[6][2] = board[6][2], None
            board[0][6], board[1][6] = board[1][6], None
            data["new_piece"] = [[6, 0]]
            data["interaction"]["selected_pos"] = [6, 0]
            data["interaction"]["selected_cost"] = [[6, 0], [2, 5]]
            data["flow"]["temp_removed"] = [{"position": [6, 0], "piece": {"owner": "white", "kind": "squirrel"}}]
            data["flow"]["temp_placed"] = [[2, 5]]
            path.write_text(json.dumps(data), encoding="utf-8")
            restored = load_session_file(path)
            self.assertEqual(restored.game.get_piece((2, 6)).owner, "black")
            self.assertEqual(restored.game.get_piece((6, 1)).owner, "white")
            self.assertIsNone(restored.game.get_piece((2, 5)))
            self.assertIsNone(restored.game.get_piece((6, 0)))
            self.assertEqual(restored.game.new_piece, [(6, 1)])
            self.assertEqual(restored.interaction.selected_pos, (6, 1))
            self.assertEqual(restored.interaction.selected_cost, [(6, 1), (2, 6)])
            self.assertEqual(restored.flow.temp_removed[0][0], (6, 1))
            self.assertEqual(restored.flow.temp_placed, [(2, 6)])
            save_session_file(restored, path)
            reloaded = load_session_file(path)
            self.assertEqual(reloaded.snapshot()["board"], restored.snapshot()["board"])

    def test_version2_corner_save_converts_flags_without_moving_pieces(self):
        with tempfile.TemporaryDirectory() as directory:
            s = ready()
            act(s, "uproot_tree", at=[2, 3])
            path = Path(directory) / "session.json"
            save_session_file(s, path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["g2_corner_layout"] = 2
            data["expanded_corners"] = ["black"]
            data["interaction"]["selected_pos"] = [2, 6]
            path.write_text(json.dumps(data), encoding="utf-8")
            restored = load_session_file(path)
            self.assertEqual(restored.snapshot()["board"], s.snapshot()["board"])
            self.assertEqual(restored.game.expanded_corners, {"lower_left"})
            self.assertEqual(restored.interaction.selected_pos, (2, 6))
            self.assertIn("##", "".join(agenthelper.board_map(restored.snapshot())["rows"]))

    def test_g1_keeps_its_setup_shop_checks_and_board_area(self):
        s = GameSession.new_g1()
        act(s, "choose_initial_token", side="black")
        s.game.current_ap = 20
        self.assertEqual(s.players["black"].num_squirrels, 3)
        self.assertEqual(s.game.get_piece((0, 3)).kind, "pine")
        self.assertEqual(len(get_g1_condition_results(s.players["black"], s.game)), 5)
        self.assertFalse(is_player_accessible(s.game, (6, 0)))
        types = {a["type"] for a in s.legal_actions()}
        self.assertIn("buy_elephant", types)
        self.assertNotIn("buy_mole", types)
        s.select_resource("elephant")
        s.select_board((2, 1))
        s.select_board((2, 3))
        s.select_board((4, 4))
        self.assertEqual(s.game.get_piece((4, 4)).kind, "elephant")

    def test_agent_mode_coordinates_snapshot_and_no_pygame_import(self):
        with tempfile.TemporaryDirectory() as directory:
            result = agenthelper.create_game(session_id="g2test", gamemode=2, save_directory=directory)
            self.assertTrue(result["ok"], result)
            agenthelper.choose_initial_token("g2test", "white")
            result = agenthelper.play_action("g2test", "uproot_tree", {"at": [2, 3]})
            self.assertTrue(result["ok"], result)
            cells = agenthelper.get_coordinates("g2test")["actionable_cells"]
            self.assertEqual(len(cells), 25)
            self.assertIn([2, 6], cells)
            self.assertNotIn([2, 5], cells)
            state = agenthelper.get_state("g2test")
            self.assertFalse(state["board"][3][2]["rooted"])
            self.assertIn("##", "".join(state["board_map"]["rows"]))
            headless = subprocess.run(
                [sys.executable, "-c", "import sys, agenthelper; assert 'pygame' not in sys.modules"],
                capture_output=True, text=True,
            )
            self.assertEqual(headless.returncode, 0, headless.stderr)
            agenthelper.close_session("g2test")


if __name__ == "__main__":
    unittest.main()
