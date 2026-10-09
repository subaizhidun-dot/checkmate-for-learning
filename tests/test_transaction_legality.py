"""Transaction rollback and shared human/agent sale legality."""
import tempfile
import unittest
from pathlib import Path

from basicgame import Resource, SELL_REFUND, get_legal_moves_for_piece
from gameengine import GameSession, EngineError, encode_action_id, save_session_file, load_session_file
from sl_func import deserialize_flow


def act(session, action_type, **params):
    return session.submit(encode_action_id(action_type, params), session.revision)


def sized_army(mode=3, count=7, token_owner="black", ap=3):
    session = GameSession.new_game(mode, resolve_checks_immediately=False)
    act(session, "choose_initial_token", side=token_owner)
    game = session.game
    game.board_matrix = [[None] * 9 for _ in range(7)]
    special = "mole" if mode == 2 else "elephant"
    game.set_piece((2, 3), Resource("black", special))
    cells = [(2, 1), (2, 2), (2, 4), (2, 5), (3, 1), (3, 2), (3, 4), (3, 5)]
    for cell in cells[:count-1]:
        game.set_piece(cell, Resource("black", "squirrel"))
    game.set_piece((6, 1), Resource("white", "squirrel"))
    game.new_piece = []
    game.current_ap = game.max_ap = ap
    session.refresh_players()
    return session


class TransactionLegalityTests(unittest.TestCase):
    def reload(self, session):
        with tempfile.TemporaryDirectory() as directory:
            path = save_session_file(session, Path(directory) / "game.json")
            return load_session_file(path, resolve_checks_immediately=session.resolve_checks_immediately)

    def test_single_token_transfer_is_legal_for_an_over_limit_recipient(self):
        from agenttools import AgentTools
        for mode in (1, 2, 3):
            with self.subTest(mode=mode):
                session = sized_army(mode, count=9, token_owner="white", ap=1)
                tools = AgentTools(session, "black")
                self.assertIn("move_time_token", {a["type"] for a in tools.get_legal_actions()["actions"]})
                result = tools.call("apply_action", {"action_id": "move_time_token", "revision": session.revision})
                self.assertTrue(result["ok"])
                self.assertEqual(session.game.time_token_owner, "black")
                self.assertEqual(session.game.current_ap, 0)
                self.assertEqual(session.flow.phase, "pending_piece_limit")
                self.assertEqual(session.actor(), "black")
                self.assertFalse(session.public_check_events)
                self.assertEqual({a["type"] for a in session.legal_actions()}, {"remove_piece_limit"})
                with self.assertRaises(EngineError):
                    act(session, "move_time_token")
                for cell in ((3, 5), (3, 4)):
                    act(session, "remove_piece_limit", at=list(cell))
                    self.assertEqual(session.game.current_ap, 0)
                self.assertEqual(session.players["black"].num_pieces, 7)
                self.assertEqual(session.flow.phase, "checking_win")

    def test_elephant_bonus_requires_removal_before_finishing_even_on_last_ap(self):
        for mode in (1, 3):
            for ap in (1, 3):
                with self.subTest(mode=mode, ap=ap):
                    session = sized_army(mode, ap=ap)
                    act(session, "select_piece", at=[2, 3])
                    act(session, "move", **{"from": [2, 3], "to": [3, 3]})
                    self.assertEqual(session.flow.phase, "pending_elephant_bonus")
                    act(session, "place_elephant_bonus", at=[2, 3])
                    self.assertEqual(session.players["black"].num_pieces, 8)
                    self.assertEqual(session.game.current_ap, ap-1)
                    self.assertEqual(session.flow.phase, "pending_piece_limit")
                    self.assertEqual(session.flow.pending_piece_limit_owner, "black")
                    self.assertFalse(session.public_check_events)
                    session = self.reload(session)
                    self.assertEqual(session.flow.phase, "pending_piece_limit")
                    self.assertEqual({a["type"] for a in session.legal_actions()}, {"remove_piece_limit"})
                    act(session, "remove_piece_limit", at=[2, 3])
                    self.assertFalse(session.game.is_new_piece((2, 3)))
                    self.assertEqual(session.players["black"].num_pieces, 7)
                    self.assertEqual(session.game.current_ap, ap-1)
                    self.assertIsNone(session.flow.pending_piece_limit_owner)
                    self.assertEqual(session.flow.phase, "playing" if ap > 1 else "checking_win")

    def test_nonholder_and_ng_elephant_bonus_allow_eight_pieces(self):
        for holder in ("white", None):
            with self.subTest(holder=holder):
                session = sized_army()
                session.game.time_token_owner = holder
                act(session, "select_piece", at=[2, 3])
                act(session, "move", **{"from": [2, 3], "to": [3, 3]})
                act(session, "place_elephant_bonus", at=[2, 3])
                self.assertEqual(session.players["black"].num_pieces, 8)
                self.assertEqual(session.flow.phase, "playing")
                self.assertIsNone(session.flow.pending_piece_limit_owner)

    def test_holder_bonus_at_seven_and_skipping_bonus_finish_normally(self):
        for count, bonus in ((6, True), (7, False)):
            with self.subTest(count=count, bonus=bonus):
                session = sized_army(count=count)
                act(session, "select_piece", at=[2, 3])
                act(session, "move", **{"from": [2, 3], "to": [3, 3]})
                if bonus:
                    act(session, "place_elephant_bonus", at=[2, 3])
                else:
                    act(session, "skip_elephant_bonus")
                self.assertEqual(session.players["black"].num_pieces, 7)
                self.assertEqual(session.flow.phase, "playing")

    def test_paid_placement_and_completed_refund_enforce_the_holder_limit(self):
        session = sized_army(ap=4)
        act(session, "place_squirrel", at=[3, 3])
        self.assertEqual(session.flow.phase, "pending_piece_limit")
        self.assertEqual(session.game.current_ap, 1)
        act(session, "remove_piece_limit", at=[3, 3])
        self.assertEqual(session.flow.phase, "playing")
        session = sized_army(ap=3)
        session.game.set_piece((2, 3), Resource("black", "butterfly"))
        session.refresh_players()
        act(session, "sell_butterfly", at=[2, 3])
        act(session, "place_refund", at=[2, 3])
        self.assertEqual(session.flow.phase, "pending_refund")
        self.assertEqual(session.game.current_ap, 3)
        act(session, "place_refund", at=[3, 3])
        self.assertEqual(session.flow.phase, "pending_piece_limit")
        self.assertEqual(session.players["black"].num_pieces, 8)
        self.assertEqual(session.game.current_ap, 2)
        self.assertIsNone(session.flow.temp_new_pieces)

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
