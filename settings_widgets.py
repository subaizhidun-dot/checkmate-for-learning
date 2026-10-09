"""Local settings field editing and numeric control ranges."""
from __future__ import annotations

import re


NUMERIC_SETTINGS = {
    "max_tokens": (0, 131072, 1024),
    "timeout": (5, 600, 5),
    "temperature": (0.0, 2.0, 0.05),
}


class LineEditor:
    def __init__(self):
        self.multiline = False
        self.text = ""
        self.cursor = 0
        self.anchor = None
        self.offset = 0
        self.dragging = False

    @property
    def selection(self):
        if self.anchor is None:
            return self.cursor, self.cursor
        return min(self.anchor, self.cursor), max(self.anchor, self.cursor)

    def load(self, text):
        self.text = text
        self.cursor = len(text)
        self.anchor = None
        self.offset = 0
        self.dragging = False

    def select_all(self):
        self.anchor, self.cursor = 0, len(self.text)

    def selected_text(self):
        start, end = self.selection
        return self.text[start:end]

    def insert(self, text):
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
        if not self.multiline:
            text = text.replace("\n", "")
        start, end = self.selection
        available = max(0, (65536 if self.multiline else 4096) - len(self.text) + end - start)
        text = text[:available]
        self.text = self.text[:start] + text + self.text[end:]
        self.cursor, self.anchor = start + len(text), None

    def set_cursor(self, position, extend=False):
        if extend:
            if self.anchor is None:
                self.anchor = self.cursor
        else:
            self.anchor = None
        self.cursor = max(0, min(len(self.text), position))

    def word_boundary(self, direction):
        if direction < 0:
            match = re.search(r"(?:\w+|[^\w\s]+)\s*$", self.text[:self.cursor])
            return match.start() if match else 0
        match = re.match(r"\s*(?:\w+|[^\w\s]+)\s*", self.text[self.cursor:])
        return self.cursor + match.end() if match else len(self.text)

    def move(self, direction, *, extend=False, word=False):
        start, end = self.selection
        if not extend and start != end:
            self.set_cursor(start if direction < 0 else end)
        else:
            position = self.word_boundary(direction) if word else self.cursor + direction
            self.set_cursor(position, extend)

    def delete(self, backward=True, word=False):
        start, end = self.selection
        if start == end:
            other = self.word_boundary(-1 if backward else 1) if word else self.cursor + (-1 if backward else 1)
            start, end = sorted((self.cursor, max(0, min(len(self.text), other))))
        self.text = self.text[:start] + self.text[end:]
        self.cursor, self.anchor = start, None


def read_clipboard():
    import sys
    if sys.platform == "win32":
        return _windows_clipboard()
    import pygame
    pygame.scrap.init()
    return (pygame.scrap.get(pygame.SCRAP_TEXT) or b"").decode("utf-8").rstrip("\x00")


def write_clipboard(text):
    import sys
    if sys.platform == "win32":
        _windows_clipboard(text)
        return
    import pygame
    pygame.scrap.init()
    pygame.scrap.put(pygame.SCRAP_TEXT, text.encode("utf-8") + b"\x00")


def _windows_clipboard(text=None):
    """Use Unicode clipboard data, including text copied from external apps."""
    import ctypes
    from ctypes import wintypes
    user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
    user.OpenClipboard.argtypes, user.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    user.GetClipboardData.argtypes, user.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
    user.SetClipboardData.argtypes, user.SetClipboardData.restype = [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE
    kernel.GlobalLock.argtypes, kernel.GlobalLock.restype = [wintypes.HANDLE], ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel.GlobalAlloc.argtypes, kernel.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], wintypes.HANDLE
    kernel.GlobalFree.argtypes, kernel.GlobalFree.restype = [wintypes.HANDLE], wintypes.HANDLE
    owner = None
    if text is not None:
        import pygame
        owner = pygame.display.get_wm_info().get("window")
        if not owner:
            raise OSError("Clipboard requires an active game window")
    if not user.OpenClipboard(owner):
        raise OSError("Clipboard is busy")
    allocation = None
    try:
        if text is None:
            handle = user.GetClipboardData(13)  # CF_UNICODETEXT
            if not handle:
                return ""
            pointer = kernel.GlobalLock(handle)
            if not pointer:
                raise OSError("Clipboard unavailable")
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel.GlobalUnlock(handle)
        data = text.encode("utf-16-le") + b"\x00\x00"
        allocation = kernel.GlobalAlloc(0x0002, len(data))
        pointer = kernel.GlobalLock(allocation) if allocation else None
        if not pointer:
            raise OSError("Clipboard allocation failed")
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            kernel.GlobalUnlock(allocation)
        if not user.EmptyClipboard() or not user.SetClipboardData(13, allocation):
            raise OSError("Clipboard unavailable")
        allocation = None  # Windows now owns this allocation.
    finally:
        if allocation:
            kernel.GlobalFree(allocation)
        user.CloseClipboard()
