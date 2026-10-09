"""Ordered replies, public check evidence and notes-only requests, all offline."""
import json
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_play import AgentPlayController, ConnectionTester
from agent_prompts import NOTES_PROMPT
from agenttools import AgentTools
from agent_context import public_checks, evidence_sequence
from gameengine import GameSession, save_session_file, load_session_file
from note_review import NoteReviewer
from test_agent_play import AppStub, FakeRequest, response


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.app = AppStub()
        self.requests = []
        def factory(profile, payload):
            request = FakeRequest(profile, payload)
            self.requests.append(request)
            return request
        self.factory = factory
        self.play = self.app.agent_play = AgentPlayController(self.app, factory)
        self.notes = self.app.note_reviewer = NoteReviewer(self.app, factory)
        self.session = self.app.state.session
        self.session.game.current_ap = self.session.game.max_ap = 3

    def reply(self, *calls):
        self.requests[-1].response = response(*calls)
        self.play.tick()
        while self.play.pending:
            self.play.tick()

    def move(self, source=(2, 1), target=(3, 1), request_id="move"):
        return ("move_piece", {"source": list(source), "target": list(target),
                               "revision": self.session.revision, "request_id": request_id})

    def check_turn(self, immediate=True):
        self.session.resolve_checks_immediately = immediate
        self.session.game.current_ap = 1
        side = self.session.game.current_player
        tools = self.app.agent_tools_by_side[side]
        move = next(m for m in tools.get_legal_actions()["moves"]
                    if self.session.game.get_piece(tuple(m["source"])).kind == "squirrel")
        result = tools.move_piece(move["source"], move["target"], self.session.revision,
                                  "end-" + str(self.session.turn_number))
        self.assertTrue(result["ok"])
        return side

    def test_ordered_moves_rebase_and_duplicate_is_not_executed_twice(self):
        self.play.next()
        first = self.move()
        second = self.move((3, 1), (4, 1), "second")
        self.reply(first, first, second)
        self.assertIsNotNone(self.session.game.get_piece((4, 1)))
        self.assertEqual(self.session.game.current_ap, 1)
        self.assertEqual(self.play.state, "ready")

    def test_illegal_suffix_preserves_prefix_and_discards_later_calls(self):
        self.play.next()
        self.reply(self.move(), self.move((3, 1), (99, 99), "illegal"),
                   ("update_notes", {"text": "must not execute"}))
        self.assertIsNotNone(self.session.game.get_piece((3, 1)))
        self.assertEqual(self.session.game.current_ap, 2)
        self.assertNotEqual(self.session.agent_notes["black"], "must not execute")
        self.assertEqual(self.play.state, "stopped")

    def test_external_change_between_calls_invalidates_remaining_plan(self):
        self.play.next()
        self.requests[-1].response = response(self.move(), self.move((3, 1), (4, 1), "second"))
        self.play.tick()
        self.play.tick()
        self.session._touch()
        self.play.tick()
        self.assertIsNotNone(self.session.game.get_piece((3, 1)))
        self.assertIsNone(self.session.game.get_piece((4, 1)))
        self.assertFalse(self.play.pending)

    def test_select_and_move_in_one_reply_use_same_start_revision(self):
        revision = self.session.revision
        self.play.next()
        self.reply(("apply_action", {"action_id": "select_piece:at=2,1", "revision": revision}),
                   ("apply_action", {"action_id": "move:from=2,1;to=3,1", "revision": revision}))
        self.assertEqual(self.session.game.current_ap, 2)
        self.assertEqual(self.play.state, "ready")

    def test_no_ap_deadline_survives_read_queries_and_stream_output(self):
        self.app.gui.play_control_mode = "auto"
        with patch("agent_play.time.monotonic", return_value=100):
            self.play.next()
            self.reply(("get_notes", {}))
            self.play.tick()
        with patch("agent_play.time.monotonic", return_value=219):
            self.requests[-1].response = {"event": "progress", "reply": "Still thinking"}
            self.play.tick()
        with patch("agent_play.time.monotonic", return_value=220):
            self.play.tick()
        self.assertTrue(self.requests[-1].cancelled)
        self.assertIn("no AP cost", self.play.message)

    def test_only_ap_spending_resets_deadline(self):
        with patch("agent_play.time.monotonic", return_value=100):
            self.play.next()
        with patch("agent_play.time.monotonic", return_value=150):
            self.reply(("apply_action", {"action_id": "select_piece:at=2,1", "revision": self.session.revision}))
            self.assertEqual(self.play.no_ap_since, 100)
            self.play.next()
        with patch("agent_play.time.monotonic", return_value=180):
            self.reply(("apply_action", {"action_id": "move:from=2,1;to=3,1", "revision": self.session.revision}))
            self.assertEqual(self.play.no_ap_since, 180)
        with patch("agent_play.time.monotonic", return_value=900):
            self.play.tick()
        self.assertEqual(self.play.state, "ready")

    def test_checks_publish_after_animation_and_include_finishing_action(self):
        side = self.check_turn(immediate=False)
        self.assertEqual(self.session.public_check_events, [])
        self.assertIsNone(self.app.agent_tools_by_side[side].get_public_state()["last_completed_check"])
        self.session.resolve_checks()
        event = self.session.public_check_events[0]
        self.assertEqual(event["checked_player"], side)
        self.assertEqual(event["actions"][-1]["action_type"], "move")
        self.assertNotEqual(event["start_board"], event["end_board"])
        first_turn = event["turn_number"]
        other = self.check_turn()
        second = self.session.public_check_events[-1]
        self.assertNotEqual(other, side)
        self.assertEqual(second["turn_number"], first_turn + 1)
        self.assertEqual(second["actions"][-1]["action_type"], "move")
        self.assertTrue(all(a["revision_after"] > event["actions"][-1]["revision_after"] for a in second["actions"]))
        with tempfile.TemporaryDirectory() as directory:
            path = save_session_file(self.session, Path(directory) / "evidence.json")
            restored = load_session_file(path)
            self.assertEqual(restored.flow.last_public_check, second)

    def test_reviews_both_sides_once_for_each_players_turn_and_write_private_notes(self):
        self.session.agent_notes = {"black": "black-private", "white": "white-private"}
        self.check_turn()
        self.check_turn()
        for index, side in enumerate(("black", "white", "black", "white")):
            self.notes.tick()
            request = self.requests[-1]
            self.assertEqual(request.payload["messages"][0]["content"], NOTES_PROMPT)
            context = json.loads(request.payload["messages"][-1]["content"][0]["text"])
            self.assertEqual(context["your_side"], side)
            self.assertEqual(context["completed_check"]["checked_player"], "black" if index < 2 else "white")
            self.assertNotIn("white-private" if side == "black" else "black-private", str(request.payload))
            self.assertEqual([t["function"]["name"] for t in request.payload["tools"]], ["update_notes"])
            self.assertNotIn("tool_choice", request.payload)
            request.response = response(("update_notes", {"text": side + " hypothesis " + str(index)}))
            self.notes.tick()
        self.assertFalse(self.notes.busy)
        self.notes.tick()
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(self.session.agent_notes["black"], "black hypothesis 2")
        self.assertEqual(self.session.agent_notes["white"], "white hypothesis 3")
        self.assertEqual(self.session.llm_usage.summary()["total_tokens"], 40)
        self.assertEqual(self.session.llm_usage.summary()["llm_turns"], 0)
        self.assertTrue(all(r["purpose"] == "notes" for r in self.session.llm_usage.requests))

    def test_human_turn_triggers_llm_review_and_blocks_play_until_updated(self):
        self.app.gui.player_types["black"] = "human"
        self.check_turn()
        self.notes.tick()
        self.play.next()
        self.assertEqual(len(self.requests), 1)
        self.requests[-1].response = response(("update_notes", {"text": "New hypothesis"}))
        self.notes.tick()
        self.play.tick()
        self.assertEqual(len(self.requests), 1)
        self.play.next()
        self.assertEqual(len(self.requests), 2)
        self.assertIn("New hypothesis", str(self.requests[-1].payload))

    def test_paused_reviews_resume_with_next(self):
        self.play.pause()
        self.check_turn()
        self.notes.tick()
        self.assertEqual(self.requests, [])
        self.play.next()
        self.notes.tick()
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.play.paused)

    def test_game_replacement_cancels_review_and_discards_late_notes(self):
        self.check_turn()
        self.notes.tick()
        request = self.requests[-1]
        self.app.state.session = GameSession.new_game(2)
        request.response = response(("update_notes", {"text": "Old game"}))
        self.notes.tick()
        self.assertTrue(request.cancelled)
        self.assertFalse(self.notes.busy)
        self.assertEqual(self.app.state.session.agent_notes, {})

    def test_midturn_save_preserves_actions_for_later_check(self):
        tools = self.app.agent_tools_by_side["black"]
        self.assertTrue(tools.move_piece([2, 1], [3, 1], self.session.revision, "before-save")["ok"])
        prior_actions = list(self.session.flow.turn_actions)
        with tempfile.TemporaryDirectory() as directory:
            path = save_session_file(self.session, Path(directory) / "midturn.json")
            restored = load_session_file(path)
        restored.game.current_ap = 1
        result = AgentTools(restored, "black").move_piece([3, 1], [4, 1], restored.revision, "after-save")
        self.assertTrue(result["ok"])
        self.assertEqual(restored.flow.last_public_check["actions"][:len(prior_actions)], prior_actions)
        self.assertEqual(restored.flow.last_public_check["actions"][-1]["params"]["to"], [4, 1])

    def test_last_ap_stops_remaining_calls_at_check_boundary(self):
        self.session.resolve_checks_immediately = False
        self.session.game.current_ap = 1
        self.play.next()
        self.reply(self.move(), ("update_notes", {"text": "Must wait for public feedback"}))
        self.assertEqual(self.session.flow.phase, "checking_win")
        self.assertNotEqual(self.session.agent_notes["black"], "Must wait for public feedback")
        self.assertFalse(self.play.pending)

    def test_review_rejects_game_calls_and_keeps_notes(self):
        self.app.gui.player_types["white"] = "human"
        self.check_turn()
        self.notes.tick()
        old_notes = dict(self.session.agent_notes)
        revision = self.session.revision
        self.requests[-1].response = response(("update_notes", {"text": "bad"}), self.move())
        self.notes.tick()
        self.assertEqual(self.session.agent_notes, old_notes)
        self.assertEqual(self.session.revision, revision)
        self.assertEqual(self.session.llm_usage.requests[-1]["status"], "failed")

    def test_newer_notes_survive_inflight_review(self):
        self.app.gui.player_types["white"] = "human"
        self.check_turn()
        self.notes.tick()
        self.session.agent_notes["black"] = "Newer edited notes"
        self.requests[-1].response = response(("update_notes", {"text": "Old hypothesis"}))
        self.notes.tick()
        self.assertEqual(self.session.agent_notes["black"], "Newer edited notes")

    def test_review_keeps_reasoning_settings_and_requires_a_valid_notes_call(self):
        self.app.gui.player_types["white"] = "human"
        self.app.gui.api_profiles["black"].update(reasoning_effort="high", streaming=False)
        self.check_turn()
        self.notes.tick()
        request = self.requests[-1]
        old_notes = dict(self.session.agent_notes)
        self.assertNotIn("tool_choice", request.payload)
        self.assertEqual(request.payload["reasoning_effort"], "high")
        self.assertFalse(request.payload["stream"])
        request.response = response(content="Updated my notes")
        self.notes.tick()
        self.assertEqual(self.session.agent_notes, old_notes)
        self.assertEqual(self.session.llm_usage.requests[-1]["status"], "failed")

    def test_review_receives_one_full_board_and_lossless_starting_board_changes(self):
        self.check_turn()
        event = deepcopy(self.session.flow.last_public_check)
        self.notes.tick()
        context = json.loads(self.requests[-1].payload["messages"][-1]["content"][0]["text"])
        completed = context["completed_check"]
        self.assertEqual(completed["end_board"], event["end_board"])
        self.assertNotIn("start_board", completed)
        start = deepcopy(completed["end_board"])
        for change in completed["start_board_changes"]:
            x, y = change["at"]
            start[y][x] = change["piece"]
        self.assertEqual(start, event["start_board"])
        self.assertEqual(completed["conditions"], [{"condition_id": index, "result": result}
                                                  for index, result in enumerate(event["conditions"], 1)])
        self.assertEqual(self.session.flow.last_public_check, event)

    def test_saved_evidence_survives_note_replacement_and_reconstructs_historical_boards(self):
        self.check_turn()
        self.check_turn()
        events = deepcopy(self.session.public_check_events)
        with tempfile.TemporaryDirectory() as directory:
            path = save_session_file(self.session, Path(directory) / "public-evidence.json")
            restored = load_session_file(path)
        self.assertEqual(public_checks(restored), events)
        self.assertEqual(restored.public_check_events, [])
        restored.agent_notes["black"] = "A different hypothesis"
        self.assertEqual(public_checks(restored), events)
        self.app.state.session = restored
        self.app.agent_tools_by_side = {side: AgentTools(restored, side) for side in ("black", "white")}
        self.play.next()
        context = json.loads(self.requests[-1].payload["messages"][-1]["content"])
        self.assertEqual(context["get_notes"]["notes"], "A different hypothesis")
        reference = context["get_public_state"]["board"]
        self.assertNotIn("public_condition_evidence", context)
        archive = evidence_sequence(public_checks(restored), reference, "current_board")
        self.assertEqual(len(archive), 2)
        for evidence, event in zip(archive, reversed(events)):
            self.assertEqual(evidence["checked_player"], event["checked_player"])
            self.assertEqual(evidence["turn_number"], event["turn_number"])
            self.assertEqual(evidence["conditions"], [{"condition_id": index, "result": result}
                                                     for index, result in enumerate(event["conditions"], 1)])
            boards = {}
            for name in ("start_board", "end_board"):
                board = deepcopy(reference)
                for change in evidence[name + "_changes"]:
                    x, y = change["at"]
                    board[y][x] = change["piece"]
                self.assertEqual(board, event[name])
                boards[name] = board
            reference = boards["start_board"]

    def test_legacy_save_keeps_its_last_check_when_new_evidence_is_recorded(self):
        self.check_turn()
        event = deepcopy(self.session.flow.last_public_check)
        with tempfile.TemporaryDirectory() as directory:
            path = save_session_file(self.session, Path(directory) / "legacy-evidence.json")
            data = json.loads(path.read_text())
            data["flow"].pop("public_check_archive")
            path.write_text(json.dumps(data), encoding="utf-8")
            restored = load_session_file(path)
        self.assertEqual(public_checks(restored), [event])
        self.app.state.session = self.session = restored
        self.app.agent_tools_by_side = {side: AgentTools(restored, side) for side in ("black", "white")}
        self.check_turn()
        self.assertEqual(len(public_checks(restored)), 2)
        self.assertEqual(public_checks(restored)[0], event)

    def test_review_deadline_and_cancellation_do_not_restart_old_event(self):
        self.app.gui.player_types["white"] = "human"
        self.check_turn()
        with patch("note_review.time.monotonic", return_value=100):
            self.notes.tick()
        with patch("note_review.time.monotonic", return_value=220):
            self.notes.tick()
        self.assertTrue(self.requests[-1].cancelled)
        self.assertEqual(self.session.llm_usage.requests[-1]["status"], "failed")
        self.notes.cancel()
        self.notes.tick()
        self.assertEqual(len(self.requests), 1)


class ConnectionCapabilityTests(unittest.TestCase):
    # A separate base avoids sharing any live network endpoint or game callbacks.
    def setUp(self):
        WorkflowTests.setUp(self)
        self.app.gui.connection_status = {"black": "idle", "white": "idle"}
        self.probe = ConnectionTester(self.app, self.factory)

    def test_tool_http_failure_still_checks_images_with_the_configured_reasoning(self):
        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                self.app.gui.api_profiles["black"].update(reasoning_effort="high", streaming=streaming, vision=True)
                self.probe.start("black")
                self.requests[-1].response = response(content="OK")
                self.probe.tick()
                request = self.requests[-1]
                self.assertNotIn("tool_choice", request.payload)
                self.assertEqual([tool["function"]["name"] for tool in request.payload["tools"]], ["connection_echo"])
                self.assertEqual(request.payload["reasoning_effort"], "high")
                self.assertEqual(request.payload["stream"], streaming)
                request.response = {"error": "API returned HTTP 400. Tool calling unavailable."}
                self.probe.tick()
                job = self.probe.jobs["black"]
                self.assertEqual(job["stage"], "Image input")
                self.assertEqual(self.probe.results["black"]["Text"], "passed")
                self.assertEqual(self.probe.results["black"]["Tool call"], "failed")
                self.assertEqual(self.probe.results["black"]["Tool result"], "skipped")
                self.assertNotIn("tools", self.requests[-1].payload)
                self.assertEqual(self.requests[-1].payload["reasoning_effort"], "high")
                self.assertEqual(self.requests[-1].payload["stream"], streaming)
                self.requests[-1].response = response(content=job["color"])
                self.probe.tick()
                self.assertEqual(self.probe.results["black"]["Image input"], "passed")
                self.assertEqual(self.app.gui.connection_status["black"], "failed")
                self.assertFalse(self.probe.jobs)
                self.assertEqual(self.session.llm_usage.requests, [])

    def test_text_failure_and_receipt_failure_have_independent_image_results(self):
        for failed_stage in ("Text", "Tool result"):
            with self.subTest(stage=failed_stage):
                self.probe.start("black")
                self.requests[-1].response = response(content="Incorrect" if failed_stage == "Text" else "OK")
                self.probe.tick()
                job = self.probe.jobs["black"]
                self.requests[-1].response = response(("connection_echo", {"nonce": job["nonce"]}))
                self.probe.tick()
                self.assertNotIn("tool_choice", self.requests[-1].payload)
                self.assertEqual(self.requests[-1].payload["messages"][1]["reasoning_content"], "Provider-returned reasoning")
                self.requests[-1].response = response(content="Incorrect" if failed_stage == "Tool result" else job["receipt"])
                self.probe.tick()
                self.assertEqual(job["stage"], "Image input")
                self.requests[-1].response = response(content=job["color"])
                self.probe.tick()
                self.assertEqual(self.probe.results["black"][failed_stage], "failed")
                self.assertEqual(self.probe.results["black"]["Image input"], "passed")
                self.assertEqual(self.app.gui.connection_status["black"], "failed")

    def test_text_only_tool_answer_fails_local_validation(self):
        self.app.gui.api_profiles["black"]["vision"] = False
        self.probe.start("black")
        self.requests[-1].response = response(content="OK")
        self.probe.tick()
        self.requests[-1].response = response(content="I called connection_echo")
        self.probe.tick()
        self.assertEqual(self.probe.results["black"], {"Text": "passed", "Tool call": "failed",
                                                      "Tool result": "skipped", "Image input": "skipped"})
        self.assertFalse(self.probe.requests)

    def test_receipt_followup_preserves_an_empty_reasoning_field(self):
        self.probe.start("black")
        self.requests[-1].response = response(content="OK")
        self.probe.tick()
        job = self.probe.jobs["black"]
        returned = response(("connection_echo", {"nonce": job["nonce"]}))
        returned["data"]["choices"][0]["message"]["reasoning_content"] = ""
        self.requests[-1].response = returned
        self.probe.tick()
        assistant = self.requests[-1].payload["messages"][1]
        self.assertIn("reasoning_content", assistant)
        self.assertEqual(assistant["reasoning_content"], "")

    def test_bad_tool_or_image_answer_is_failure(self):
        for vision in (False, True):
            self.app.gui.api_profiles["black"]["vision"] = vision
            self.probe.start("black")
            self.requests[-1].response = response(content="OK")
            self.probe.tick()
            self.requests[-1].response = response(("connection_echo", {"nonce": "wrong"}))
            self.probe.tick()
            self.assertEqual(self.app.gui.connection_status["black"], "failed")
            if vision:
                job = self.probe.jobs["black"]
                self.assertEqual(job["stage"], "Image input")
                self.assertEqual(job["results"]["Tool result"], "skipped")
                self.requests[-1].response = response(content=job["color"])
                self.probe.tick()
                self.assertEqual(self.probe.results["black"]["Image input"], "passed")
                self.assertEqual(self.app.gui.connection_status["black"], "failed")
        self.probe.start("black")
        self.requests[-1].response = response(content="OK")
        self.probe.tick()
        job = self.probe.jobs["black"]
        self.requests[-1].response = response(("connection_echo", {"nonce": job["nonce"]}))
        self.probe.tick()
        self.requests[-1].response = response(content=job["receipt"])
        self.probe.tick()
        self.requests[-1].response = response(content="unknown")
        self.probe.tick()
        self.assertEqual(self.app.gui.connection_status["black"], "failed")

    def test_vision_disabled_finishes_after_tool_roundtrip(self):
        self.app.gui.api_profiles["black"]["vision"] = False
        self.probe.start("black")
        self.requests[-1].response = response(content="OK")
        self.probe.tick()
        job = self.probe.jobs["black"]
        self.requests[-1].response = response(("connection_echo", {"nonce": job["nonce"]}))
        self.probe.tick()
        self.requests[-1].response = response(content=job["receipt"])
        self.probe.tick()
        self.assertEqual(self.app.gui.connection_status["black"], "success")
        self.assertEqual(len(self.requests), 3)
        self.assertFalse(self.probe.jobs)
        self.assertEqual(self.session.llm_usage.requests, [])

    def test_probe_stream_cannot_extend_stage_deadline(self):
        with patch("agent_play.time.monotonic", return_value=100):
            self.probe.start("black")
            request = self.requests[-1]
        with patch("agent_play.time.monotonic", return_value=220):
            self.requests[-1].response = {"event": "progress", "reply": "O"}
            self.probe.tick()
        self.assertTrue(request.cancelled)
        self.assertEqual(self.probe.jobs["black"]["stage"], "Tool call")
        self.assertEqual(self.app.gui.connection_status["black"], "failed")
