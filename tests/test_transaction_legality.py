"""Transaction rollback and shared human/agent sale legality."""
import tempfile
import unittest
from pathlib import Path

from basicgame import Resource, SELL_REFUND, get_legal_moves_for_piece
from gameengine import GameSession, encode_action_id, save_session_file, load_session_file
from sl_func import deserialize_flow


def act(session, action_type, **params):
    return session.submit(encode_action_id(action_type, params), session.revision)


class TransactionLegalityTests(unittest.TestCase):
    def reload(self, session):
        with tempfile.TemporaryDirectory() as directory:
            path = save_session_file(session, Path(directory) / "game.json")
            return load_session_file(path)

    def test_cancel_purchase_restores_new_piece_markers_including_after_load(self):
        for reload in (False, True):
            with self.subTest(reload=reload):
                session = GameSession.new_g1()
                act(session, "choose_initial_token", side="black")
                session.game.current_ap = 10
                act(session, "place_squirrel", at=[3, 2])
                act(session, "place_squirrel", at=[4, 2])
                before = list(session.game.new_piece)
                act(session, "buy_elephant", at=[4, 3])
                act(session, "select_cost", kind="elephant", at=[3, 2])
                act(session, "select_cost", kind="elephant", at=[2, 1])
                if reload:
                    session = self.reload(session)
                act(session, "cancel")
                self.assertEqual(session.game.new_piece, before)
                self.assertEqual(get_legal_moves_for_piece(session.game, session.players, (3, 2)), set())
                self.assertTrue(get_legal_moves_for_piece(session.game, session.players, (2, 1)))
                self.assertIsNone(session.flow.temp_new_pieces)

    def test_cancel_partial_sale_restores_new_special_after_refund_occupies_its_cell(self):
        for reload in (False, True):
            with self.subTest(reload=reload):
                session = GameSession.new_g3()
                act(session, "choose_initial_token", side="black")
                session.game.current_ap = 5
                session.game.set_piece((4, 3), Resource("black", "butterfly"))
                session.game.mark_new_piece((4, 3))
                session.refresh_players()
                act(session, "sell_butterfly", at=[4, 3])
                act(session, "place_refund", at=[4, 3])
                if reload:
                    session = self.reload(session)
                act(session, "cancel")
                self.assertEqual(session.game.get_piece((4, 3)).kind, "butterfly")
                self.assertEqual(session.game.new_piece, [(4, 3)])
                self.assertEqual(get_legal_moves_for_piece(session.game, session.players, (4, 3)), set())
                self.assertNotIn("butterfly_extra_turn", {a["type"] for a in session.legal_actions()})
                self.assertFalse(session.game.g3_turn.resources_changed)

    def test_committed_sale_keeps_refund_new_and_clears_rollback_snapshot(self):
        session = GameSession.new_g3()
        act(session, "choose_initial_token", side="black")
        session.game.current_ap = 5
        session.game.mark_new_piece((2, 3))
        act(session, "sell_elephant", at=[2, 3])
        act(session, "place_refund", at=[3, 3])
        self.assertEqual(session.game.new_piece, [(3, 3)])
        self.assertIsNone(session.flow.temp_new_pieces)
        act(session, "select_piece", at=[2, 1])
        act(session, "cancel")
        self.assertEqual(session.game.new_piece, [(3, 3)])

    def test_legacy_flow_without_marker_snapshot_still_loads(self):
        self.assertIsNone(deserialize_flow({"phase": "playing"}).temp_new_pieces)

    def test_sale_space_boundary_matches_human_agent_and_mate_checks(self):
        for mode, kind in ((1, "elephant"), (1, "lion"), (2, "mole"), (3, "butterfly")):
            for spaces in (0, 1):
                with self.subTest(kind=kind, spaces=spaces):
                    session = GameSession.new_game(mode)
                    session.flow.phase = "playing"
                    game = session.game
                    game.board_matrix = [[None] * 9 for _ in range(7)]
                    for y in range(1, 6):
                        for x in range(2, 7):
                            game.set_piece((x, y), Resource("white", "squirrel"))
                    game.set_piece((2, 1), Resource("black", kind))
                    game.mark_new_piece((2, 1))
                    if spaces:
                        game.set_piece((6, 5), None)
                    session.refresh_players()
                    allowed = spaces + 1 >= SELL_REFUND[kind]
                    sale = encode_action_id(f"sell_{kind}", {"at": [2, 1]})
                    self.assertEqual(sale in {a["action_id"] for a in session.legal_actions()}, allowed)
                    self.assertEqual(session._check_mate_if_stuck(), not allowed)
                    if allowed:
                        self.assertEqual(session.select_board((2, 1)), "selected")
                        self.assertEqual(get_legal_moves_for_piece(game, session.players, (2, 1)), set())
                        self.assertEqual(session.select_resource(kind), "selling")
                        session.select_board((2, 1))
                        if SELL_REFUND[kind] == 2:
                            session.select_board((6, 5))
                        self.assertEqual(game.get_piece((2, 1)).kind, "squirrel")
                        self.assertEqual(session.players["black"].num_squirrels, SELL_REFUND[kind])


if __name__ == "__main__":
    unittest.main()
