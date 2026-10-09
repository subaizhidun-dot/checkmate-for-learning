"""Offline Pygame interaction, pixel rendering and local prompt restoration."""
import os
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from agent_prompts import DEFAULT_PROMPTS
from test_agent_play import FakeRequest, response
import test_gui_agent_settings as fixtures


class ThinkingInteractionTests(unittest.TestCase):
    def setUp(self):
        fixtures.AgentSettingsGuiTests.setUp(self)
        self.gui = self.app.gui
        self.gui.resize_window((1600, 1000))

    def tearDown(self):
        fixtures.AgentSettingsGuiTests.tearDown(self)

    show_setting = fixtures.AgentSettingsGuiTests.show_setting
    start = fixtures.AgentSettingsGuiTests.start
    act = fixtures.AgentSettingsGuiTests.act

    def event(self, kind, **kwargs):
        import pygame
        event = pygame.event.Event(kind, **kwargs)
        return self.app.handle_setting_event(event) or self.gui.handle_sidebar_event(event)

    def click_setting(self, name):
        import pygame
        rect = self.show_setting(name)
        self.assertTrue(self.event(pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center))

    def point(self, source, offset):
        body = self.gui.exposure_body(self.gui.exposure_pane_rects()["thinking"])
        layout = self.gui.chat_layout(body.w)[0]
        item = next(entry for entry in layout if entry["source"] is source)
        row = max(i for i, start in enumerate(item["starts"]) if start <= offset)
        column = min(offset - item["starts"][row], len(item["lines"][row]))
        x = body.x + item["rect"].x + item["padding"] + self.gui.exposure_font.size(item["lines"][row][:column])[0]
        y = (body.y + item["rect"].y + item["padding"] +
             (row - self.gui.exposure_panes["thinking"].scroll) * (self.gui.exposure_font.get_linesize() + 3) + 8)
        return x, y

    def drag(self, source, start, target, end):
        import pygame
        body = self.gui.exposure_body(self.gui.exposure_pane_rects()["thinking"])
        def reveal(entry, offset):
            point = self.point(entry, offset)
            if not body.collidepoint(point):
                delta = round((point[1] - body.centery) / (self.gui.exposure_font.get_linesize() + 3))
                self.gui.scroll_exposure_pane(body.center, delta)
            return self.point(entry, offset)
        self.event(pygame.MOUSEBUTTONDOWN, button=1, pos=reveal(source, start))
        self.event(pygame.MOUSEMOTION, pos=reveal(target, end))
        self.event(pygame.MOUSEBUTTONUP, button=1, pos=self.point(target, end))

    def capture(self, name):
        import pygame
        directory = os.environ.get("CHECKMATE_GUI_CAPTURE_DIR")
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
            self.app.sync_play_controls()
            self.gui.draw(self.app.state)
            pygame.image.save(self.gui.window, str(Path(directory) / (name + ".png")))

    def test_restore_each_prompt_saves_only_that_prompt_and_keeps_inflight_request(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        self.start()
        self.gui.prompts = {name: "中文自定义：" + name for name in DEFAULT_PROMPTS}
        self.app.save_local_settings()
        controller = self.app.agent_play
        controller.request_id = "restore-fixture"
        controller.anchor = self.app.begin_llm_request(controller.request_id, "black")
        request = controller.request = Mock()
        before = deepcopy(self.app.state.session.snapshot())
        profiles = deepcopy(self.gui.api_profiles)
        for name, default in DEFAULT_PROMPTS.items():
            with self.subTest(name=name):
                old = dict(self.gui.prompts)
                self.app.focus_setting(name + "_text")
                self.gui.setting_buffer = "未保存的编辑"
                with patch.object(controller, "stop") as stop, patch.object(controller, "next") as next_request:
                    self.click_setting("restore_" + name)
                    stop.assert_not_called()
                    next_request.assert_not_called()
                self.assertIs(controller.request, request)
                request.cancel.assert_not_called()
                self.assertEqual(self.gui.prompt_text(name + "_text"), default)
                self.assertEqual(self.app.local_settings.load()["prompts"], self.gui.prompts)
                self.assertEqual({key: value for key, value in self.gui.prompts.items() if key != name},
                                 {key: value for key, value in old.items() if key != name})
        self.assertEqual(self.gui.api_profiles, profiles)
        self.assertEqual(self.app.state.session.snapshot(), before)
        self.gui.sidebars["left"].width = 180
        self.gui.update_viewport()
        for name in DEFAULT_PROMPTS:
            self.show_setting("restore_" + name)
            controls = self.gui.setting_controls()
            copy, restore = controls["copy_" + name], controls["restore_" + name]
            self.assertEqual(copy.y, restore.y)
            self.assertFalse(copy.colliderect(restore))
            self.assertTrue(self.gui.settings_body().contains(copy))
            self.assertTrue(self.gui.settings_body().contains(restore))
            self.gui.draw_sidebars()
            self.capture("restore-" + name)

    def test_copy_prompt_uses_unsaved_visible_content_and_restore_keeps_other_editor(self):
        self.app.configure_agent_play_mode(True, "black")
        self.app.focus_setting("notes_prompt_text")
        self.gui.setting_buffer = "界面当前内容\nEnglish  和中文"
        with patch("main.write_clipboard") as clipboard:
            self.click_setting("copy_notes_prompt")
            clipboard.assert_called_once_with("界面当前内容\nEnglish  和中文")
        self.assertEqual(self.gui.active_setting, "notes_prompt_text")
        self.gui.prompts["system_prompt"] = "动作自定义"
        self.click_setting("restore_system_prompt")
        self.assertEqual(self.gui.setting_buffer, "界面当前内容\nEnglish  和中文")
        self.assertEqual(self.gui.active_setting, "notes_prompt_text")

    def test_reloaded_restored_prompts_are_used_in_action_review_and_ng_requests(self):
        self.app.configure_agent_play_mode(True, "black")
        self.start()
        for profile in self.gui.api_profiles.values():
            profile.update(endpoint="https://example.test/v1", model="offline-fixture")
        self.gui.prompts = {name: "自定义 " + name for name in DEFAULT_PROMPTS}
        for name in DEFAULT_PROMPTS:
            self.click_setting("restore_" + name)
        self.gui.prompts = self.app.local_settings.load()["prompts"]
        requests = []
        def factory(profile, payload):
            request = FakeRequest(profile, payload)
            requests.append(request)
            return request
        play, notes = self.app.agent_play, self.app.note_reviewer
        play.request_factory = notes.request_factory = factory
        play.next()
        self.assertEqual(requests[-1].payload["messages"][0]["content"], DEFAULT_PROMPTS["system_prompt"])
        requests[-1].response = response(content="No action")
        play.tick()
        session = self.app.state.session
        session.game.current_ap = 1
        tools = self.app.agent_tools_by_side["black"]
        move = tools.get_legal_actions()["moves"][0]
        self.assertTrue(tools.move_piece(move["source"], move["target"], session.revision, "review-trigger")["ok"])
        notes.tick()
        self.assertEqual(requests[-1].payload["messages"][0]["content"], DEFAULT_PROMPTS["notes_prompt"])
        self.assertEqual([tool["function"]["name"] for tool in requests[-1].payload["tools"]], ["update_notes"])
        notes.cancel()
        # Isolate the NG+ request builder without completing a real match.
        session.game.time_token_owner = None
        self.gui.player_types[session.actor()] = "llm"
        self.app.bind_agent_tools()
        play.next()
        self.assertEqual(requests[-1].payload["messages"][0]["content"], DEFAULT_PROMPTS["ng_plus_prompt"])
        names = {tool["function"]["name"] for tool in requests[-1].payload["tools"]}
        self.assertFalse(names & {"record_intent", "get_rule_images", "update_notes", "get_notes"})
        play.stop()

    def test_mouse_selection_copies_raw_multiline_unicode_across_bubbles_and_diagnostics(self):
        import pygame
        self.gui.append_exposure_output("Reasoning\n中文  English  计划\n第二行\n\nReply：合法动作", side="black", turn=1)
        self.gui.append_exposure_output('Tool calls\nrecord_intent({"text":"中文计划"})', side="white", turn=1)
        self.gui.append_exposure_output("move_piece | Failed\nError: illegal_action", color=(235, 74, 72), turn=1)
        self.gui.append_exposure_output("Diagnostic\nAP 3 → 3", turn=1)
        messages = self.gui.thinking_messages()
        self.gui.exposure_panes["thinking"].scroll = 0
        self.gui.draw_sidebars()
        body = self.gui.exposure_body(self.gui.exposure_pane_rects()["thinking"])
        unselected_pixels = {tuple(self.gui.window.get_at((x, y)))[:3] for x in range(body.x, body.right)
                             for y in range(body.y, body.bottom)}
        self.assertIn((235, 74, 72), unselected_pixels)
        self.drag(messages[0], 10, messages[-1], len(messages[-1]["text"]))
        expected = "\n\n".join([messages[0]["text"][10:]] + [item["text"] for item in messages[1:]])
        self.assertEqual(self.gui.thinking_selection.selected_text(messages), expected)
        with patch("gui.write_clipboard") as clipboard:
            self.assertTrue(self.event(pygame.KEYDOWN, key=pygame.K_c, mod=pygame.KMOD_CTRL))
            clipboard.assert_called_once_with(expected)
        self.gui.draw_sidebars()
        body = self.gui.exposure_body(self.gui.exposure_pane_rects()["thinking"])
        pixels = {tuple(self.gui.window.get_at((x, y)))[:3] for x in range(body.x, body.right)
                  for y in range(body.y, body.bottom)}
        for color in ((55, 108, 174), (5, 5, 7), (245, 245, 242), (255, 255, 255)):
            self.assertIn(color, pixels)
        self.capture("thinking-selected")

    def test_gui_request_receipts_distinguish_received_pending_success_and_failure(self):
        import pygame
        self.app.configure_agent_play_mode(True, "black")
        self.start()
        self.gui.api_profiles["black"].update(endpoint="https://example.test/v1", model="offline-fixture")
        requests = []
        def factory(profile, payload):
            request = FakeRequest(profile, payload)
            requests.append(request)
            return request
        play = self.app.agent_play
        play.request_factory = factory
        session = self.app.state.session
        session.game.current_ap = session.game.max_ap = 3
        play.next()
        requests[-1].response = response(("record_intent", {"text": "只有计划，没有执行"}))
        play.tick()
        pane = self.gui.exposure_panes["thinking"]
        self.assertIn("Reply received", pane.text)
        self.assertIn("tools pending: record_intent", pane.text)
        self.assertNotIn("record_intent | Succeeded", pane.text)
        play.tick()
        self.assertIn("Read/intent tools completed; board unchanged. Continuing this decision.", pane.text)
        self.assertEqual(session.game.current_ap, 3)
        play.tick()
        requests[-1].response = response(("move_piece", {"source": [2, 1], "target": [3, 1],
                                        "revision": session.revision, "request_id": "gui-move"}))
        play.tick()
        play.tick()
        self.assertIn("move_piece | Succeeded | AP 3 → 2", pane.text)
        self.assertEqual(session.game.current_ap, 2)
        play.next()
        requests[-1].response = response(("submit_plan", {"actions": [
            {"type": "apply_action", "params": {"action_id": "place_squirrel:at=3,2"}}],
            "revision": session.revision, "request_id": "gui-illegal"}))
        play.tick()
        play.tick()
        self.assertIn("submit_plan | Failed", pane.text)
        self.assertIn("Rejected step 1:", pane.text)
        self.assertIn('"action_id": "place_squirrel:at=3,2"', pane.text)
        self.assertIn("illegal_action", pane.text)
        self.assertEqual(session.game.current_ap, 2)
        failed = next(entry for entry in self.gui.thinking_messages() if entry["text"].startswith("submit_plan | Failed"))
        self.drag(failed, 0, failed, len(failed["text"]))
        with patch("gui.write_clipboard") as clipboard:
            self.event(pygame.KEYDOWN, key=pygame.K_c, mod=pygame.KMOD_CTRL)
            clipboard.assert_called_once_with(failed["text"])
        self.gui.draw(self.app.state)
        self.capture("thinking-execution-receipts")

    def test_stream_append_and_header_completion_preserve_selection_and_reading_position(self):
        import pygame
        self.gui.append_exposure_output("Receiving\nReasoning\n中文 selected\n" + "more reasoning\n" * 60,
                                        side="black", request_id="live", turn=1)
        message = self.gui.thinking_messages()[0]
        self.gui.exposure_panes["thinking"].scroll = 0
        start = message["text"].index("中文")
        self.drag(message, start, message, start + len("中文 selected"))
        self.gui.append_exposure_output(message["text"] + "流式追加\n", side="black", request_id="live", turn=1)
        self.assertEqual(self.gui.exposure_panes["thinking"].scroll, 0)
        self.assertEqual(self.gui.thinking_selection.selected_text([message]), "中文 selected")
        self.gui.append_exposure_output(message["text"].replace("Receiving", "Reply received", 1) + "Tokens: 30",
                                        side="black", request_id="live", turn=1)
        self.assertEqual(self.gui.thinking_selection.selected_text([message]), "中文 selected")
        self.gui.append_exposure_output("new turn", side="white", turn=2)
        self.assertEqual(self.gui.thinking_turn, 1)
        self.gui.draw_sidebars()
        self.capture("thinking-stream-held")
        self.gui.select_thinking_turn(2)
        self.assertIsNone(self.gui.thinking_selection.anchor)
        self.gui.append_exposure_output("Receiving\nReasoning\n中文 unfinished", request_id="last-line", turn=2)
        last = self.gui.thinking_messages()[-1]
        start = last["text"].index("中文")
        self.gui.thinking_selection.start((last, start))
        self.gui.thinking_selection.cursor = (last, start + 2)
        self.gui.thinking_selection.dragging = False
        self.gui.append_exposure_output("Reply received\nReasoning\n中文 unfinished, final words\nTokens: 30",
                                        request_id="last-line", turn=2)
        self.assertEqual(self.gui.thinking_selection.selected_text(self.gui.thinking_messages()), "中文")

    def test_selection_survives_width_changes_wraps_and_new_game_clears_it(self):
        text = "中文计划  English words  " * 9 + "\n第二段\n第三段"
        self.gui.append_exposure_output(text, side="white", turn=1)
        message = self.gui.thinking_messages()[0]
        self.gui.thinking_selection.start((message, 2))
        self.gui.thinking_selection.cursor = (message, len(text)-2)
        self.gui.thinking_selection.dragging = False
        expected = text[2:-2]
        for size, width in (((1000, 700), 180), ((1920, 1080), 440), ((800, 600), 220)):
            self.gui.resize_window(size)
            self.gui.sidebars["right"].width = width
            self.gui.update_viewport()
            self.gui.draw_sidebars()
            self.assertEqual(self.gui.thinking_selection.selected_text([message]), expected)
            body = self.gui.exposure_body(self.gui.exposure_pane_rects()["thinking"])
            item = self.gui.chat_layout(body.w)[0][0]
            for line, start in zip(item["lines"], item["starts"]):
                self.assertEqual(line, text[start:start+len(line)])
            self.capture(f"thinking-resize-{size[0]}")
        self.app.start_game(2)
        self.assertIsNone(self.gui.thinking_selection.anchor)

    def test_drag_edge_autoscroll_and_readonly_shortcuts_do_not_capture_editor_keys(self):
        import pygame
        self.gui.append_exposure_output("中文行 English\n" * 100, side="black", turn=1)
        message = self.gui.thinking_messages()[0]
        pane = self.gui.exposure_panes["thinking"]
        pane.scroll = 0
        self.event(pygame.MOUSEBUTTONDOWN, button=1, pos=self.point(message, 0))
        body = self.gui.exposure_body(self.gui.exposure_pane_rects()["thinking"])
        self.event(pygame.MOUSEMOTION, pos=(body.centerx, body.bottom + 30))
        self.gui.update_thinking_selection_scroll(now=10**9)
        self.assertGreater(pane.scroll, 0)
        self.event(pygame.MOUSEBUTTONUP, button=1, pos=(body.centerx, body.bottom + 30))
        text = message["text"]
        for key in (pygame.K_v, pygame.K_x, pygame.K_BACKSPACE):
            self.assertTrue(self.event(pygame.KEYDOWN, key=key, mod=pygame.KMOD_CTRL))
        self.event(pygame.TEXTINPUT, text="不应插入")
        self.assertEqual(message["text"], text)
        self.app.configure_agent_play_mode(True, "black")
        self.app.focus_setting("system_prompt_text")
        self.gui.setting_editor.select_all()
        copied = self.gui.thinking_selection.selected_text([message])
        with patch("main.read_clipboard", return_value=copied):
            self.event(pygame.KEYDOWN, key=pygame.K_v, mod=pygame.KMOD_CTRL)
        self.assertEqual(self.gui.setting_buffer, copied)
        self.assertEqual(message["text"], text)


if __name__ == "__main__":
    unittest.main()
