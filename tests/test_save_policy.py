"""Checkpoints must follow real turn changes, not individual LLM steps."""
import unittest

from basicgame import Resource
from gameengine import GameSession, encode_action_id
from save_policy import AutosavePolicy


def act(session, kind, **params):
    return session.submit(encode_action_id(kind, params), session.revision)


class SavePolicyTests(unittest.TestCase):
    def setUp(self):
        self.session = GameSession.new_g3()
        act(self.session, "choose_initial_token", side="black")
        self.policy = AutosavePolicy()

    def test_normal_play_saves_once_per_revision(self):
        self.assertTrue(self.policy.should_save(self.session))
        self.policy.mark_saved(self.session)
        self.assertFalse(self.policy.should_save(self.session))
        act(self.session, "select_piece", at=[2, 1])
        self.assertTrue(self.policy.should_save(self.session))
        self.policy.mark_saved(self.session)
        self.assertFalse(self.policy.should_save(self.session))

    def test_llm_steps_wait_for_actual_human_turn(self):
        self.policy.configure(True, ("white",))
        act(self.session, "fast_time_token")
        self.assertEqual(self.session.game.current_player, "white")
        self.assertFalse(self.policy.should_save(self.session))
        act(self.session, "select_piece", at=[6, 1])
        self.assertFalse(self.policy.should_save(self.session))
        act(self.session, "move", **{"from": [6, 1], "to": [5, 1]})
        self.assertFalse(self.policy.should_save(self.session))
        act(self.session, "fast_time_token")
        self.assertEqual(self.session.game.current_player, "black")
        self.assertTrue(self.policy.should_save(self.session))
        self.policy.mark_saved(self.session)
        self.assertFalse(self.policy.should_save(self.session))

    def test_butterfly_extra_turn_does_not_checkpoint(self):
        self.policy.configure(True, ("black",))
        self.session.game.set_piece((3, 3), Resource("black", "butterfly"))
        self.session.game.current_ap = 5
        act(self.session, "butterfly_extra_turn", at=[3, 3])
        self.assertTrue(self.session.game.g3_turn.extra_turn)
        self.assertEqual(self.session.game.current_player, "black")
        self.assertFalse(self.policy.should_save(self.session))
        act(self.session, "fast_time_token")
        self.assertEqual(self.session.game.current_player, "white")
        self.assertTrue(self.policy.should_save(self.session))

    def test_animation_and_pending_decisions_are_not_human_turn_start(self):
        self.policy.configure(True, ("black",))
        self.assertFalse(self.policy.should_save(self.session))
        self.session.game.current_player = "white"
        for phase in ("checking_win", "pending_piece_limit", "time_wish"):
            self.session.flow.phase = phase
            self.assertFalse(self.policy.should_save(self.session))
        self.session.flow.phase = "playing"
        self.assertTrue(self.policy.should_save(self.session))

    def test_new_session_is_not_confused_with_matching_revision(self):
        self.policy.mark_saved(self.session)
        another = GameSession.new_g3()
        act(another, "choose_initial_token", side="black")
        self.assertEqual(another.revision, self.session.revision)
        self.assertTrue(self.policy.should_save(another))


if __name__ == "__main__":
    unittest.main()
