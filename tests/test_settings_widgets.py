"""Selection replacement and navigation behavior for settings text fields."""
import unittest
from settings_widgets import LineEditor


class SettingsEditorTests(unittest.TestCase):
    def test_partial_selection_replaces_only_selected_characters(self):
        editor = LineEditor()
        editor.load("https://old.example/v1")
        editor.set_cursor(8)
        editor.set_cursor(11, extend=True)
        self.assertEqual(editor.selected_text(), "old")
        editor.insert("new")
        self.assertEqual(editor.text, "https://new.example/v1")
        self.assertEqual(editor.cursor, 11)

    def test_reverse_selection_cut_and_unicode_paste(self):
        editor = LineEditor()
        editor.load("abcdef")
        editor.set_cursor(5)
        editor.set_cursor(2, extend=True)
        self.assertEqual(editor.selected_text(), "cde")
        editor.delete()
        self.assertEqual(editor.text, "abf")
        editor.insert("中文🦋\r\n")
        self.assertEqual(editor.text, "ab中文🦋f")

    def test_word_navigation_and_selection_collapse(self):
        editor = LineEditor()
        editor.load("alpha beta")
        editor.move(-1, word=True)
        self.assertEqual(editor.cursor, 6)
        editor.move(1, extend=True, word=True)
        self.assertEqual(editor.selected_text(), "beta")
        editor.move(-1)
        self.assertEqual(editor.cursor, 6)
        self.assertEqual(editor.selected_text(), "")
        editor.delete(word=True)
        self.assertEqual(editor.text, "beta")

    def test_input_limit_preserves_unselected_tail(self):
        editor = LineEditor()
        editor.load("a" * 4090 + "suffix")
        editor.set_cursor(0)
        editor.set_cursor(2, extend=True)
        editor.insert("replacement")
        self.assertEqual(len(editor.text), 4096)
        self.assertTrue(editor.text.endswith("suffix"))
