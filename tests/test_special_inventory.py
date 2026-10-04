"""One tradable special of each kind per owner, with board-derived flags."""
import json
import tempfile
import unittest
from pathlib import Path

from basicgame import Resource, SPECIAL_KINDS, can_buy_special
from gameengine import GameSession, EngineError, encode_action_id, save_session_file, load_session_file


def act(session, kind, **params):
    return session.submit(encode_action_id(kind, params), session.revision)


class SpecialInventoryTests(unittest.TestCase):
    def test_each_owned_kind_blocks_repeat_purchase_in_interface_and_engine(self):
        for mode, kind in ((1, "elephant"), (1, "lion"), (2, "mole"), (3, "butterfly")):
            with self.subTest(kind=kind):
                session = GameSession.new_game(mode)
                act(session, "choose_initial_token", side="black")
                session.game.set_piece((4, 4), Resource("black", kind))
                for pos in ((3, 2), (3, 3), (4, 3), (5, 4)):
                    session.game.set_piece(pos, Resource("black", "squirrel"))
                session.game.current_ap = 10
                session.refresh_players()
                self.assertTrue(getattr(session.players["black"], f"has_{kind}"))
                self.assertFalse(can_buy_special(session.game, session.players, kind))
                self.assertNotIn(f"buy_{kind}", {action["type"] for action in session.legal_actions()})
                session.select_resource(kind)
                self.assertEqual(session.flow.phase, "playing")
                before = session.snapshot()
                with self.assertRaises(EngineError):
                    session._place_bought_special({"at": [5, 3]}, kind)
                self.assertEqual(session.snapshot(), before)

    def test_initial_elephant_is_already_owned(self):
        session = GameSession.new_g3()
        act(session, "choose_initial_token", side="black")
        self.assertTrue(session.players["black"].has_elephant)
        self.assertNotIn("buy_elephant", {action["type"] for action in session.legal_actions()})

    def test_sale_and_cancel_restore_flag_and_purchase_availability(self):
        session = GameSession.new_g2()
        act(session, "choose_initial_token", side="black")
        session.game.current_ap = 10
        session.game.set_piece((3, 3), Resource("black", "mole"))
        session.refresh_players()
        act(session, "sell_mole", at=[3, 3])
        self.assertFalse(session.players["black"].has_mole)
        self.assertNotIn("buy_mole", {action["type"] for action in session.legal_actions()})
        act(session, "cancel")
        self.assertTrue(session.players["black"].has_mole)
        act(session, "sell_mole", at=[3, 3])
        act(session, "place_refund", at=[4, 3])
        self.assertFalse(session.players["black"].has_mole)
        self.assertIn("buy_mole", {action["type"] for action in session.legal_actions()})

    def test_all_flags_saved_and_old_missing_flags_rebuilt_from_board(self):
        session = GameSession.new_g3()
        for pos, kind in zip(((3, 2), (4, 2), (5, 2), (4, 4)), SPECIAL_KINDS):
            session.game.set_piece(pos, Resource("black", kind))
        session.refresh_players()
        with tempfile.TemporaryDirectory() as directory:
            path = save_session_file(session, Path(directory) / "game.json")
            data = json.loads(path.read_text())
            for kind in SPECIAL_KINDS:
                self.assertTrue(data["players"]["black"][f"has_{kind}"])
                del data["players"]["black"][f"has_{kind}"]
            self.assertNotIn("has_tree", data["players"]["black"])
            path.write_text(json.dumps(data))
            loaded = load_session_file(path)
            for kind in SPECIAL_KINDS:
                self.assertTrue(getattr(loaded.players["black"], f"has_{kind}"))

    def test_both_rooted_and_uprooted_trees_are_protected_from_piece_limit_removal(self):
        for rooted in (False, True):
            session = GameSession.new_g2()
            act(session, "choose_initial_token", side="black")
            session.game.get_piece((2, 3)).rooted = rooted
            session.flow.phase = "pending_piece_limit"
            session.flow.pending_piece_limit_owner = "black"
            self.assertNotIn((2, 3), session._piece_limit_targets("black"))
            with self.assertRaises(EngineError):
                act(session, "remove_piece_limit", at=[2, 3])
            self.assertIsNotNone(session.game.get_piece((2, 3)))

    def test_inventory_tracks_owner_when_lion_moves_enemy_piece(self):
        session = GameSession.new_g2()
        act(session, "choose_initial_token", side="black")
        session.game.current_ap = 10
        session.game.set_piece((4, 4), Resource("black", "lion"))
        session.game.set_piece((4, 2), Resource("white", "mole"))
        session.refresh_players()
        act(session, "select_piece", at=[4, 2])
        act(session, "move", **{"from": [4, 2], "to": [5, 2]})
        self.assertTrue(session.players["white"].has_mole)
        self.assertFalse(session.players["black"].has_mole)


if __name__ == "__main__":
    unittest.main()
