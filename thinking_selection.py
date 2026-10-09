"""Read-only selection anchored to messages and original Unicode offsets."""
from difflib import SequenceMatcher


class ThinkingSelection:
    def __init__(self):
        self.clear()

    def clear(self):
        self.anchor = self.cursor = None
        self.focused = self.dragging = False
        self.position = None
        self.scroll_due = 0
        self.error = ""

    def start(self, point, extend=False):
        if not extend or self.anchor is None:
            self.anchor = point
        self.cursor = point
        self.focused = self.dragging = True
        self.error = ""

    def bounds(self, messages):
        def locate(point):
            if point is None:
                return None
            entry, offset = point
            index = next((i for i, item in enumerate(messages) if item is entry), None)
            return None if index is None else (index, min(offset, len(entry["text"])))
        a, b = locate(self.anchor), locate(self.cursor)
        return sorted((a, b)) if a is not None and b is not None else None

    def selected_text(self, messages):
        bounds = self.bounds(messages)
        if not bounds:
            return ""
        (first, start), (last, end) = bounds
        return "\n\n".join(entry["text"][start if i == first else 0:end if i == last else None]
                            for i, entry in enumerate(messages) if first <= i <= last)

    def range_for(self, entry, messages):
        bounds = self.bounds(messages)
        index = next((i for i, item in enumerate(messages) if item is entry), None)
        if not bounds or index is None:
            return 0, 0
        (first, start), (last, end) = bounds
        if not first <= index <= last:
            return 0, 0
        return start if index == first else 0, end if index == last else len(entry["text"])

    def replace_text(self, entry, text):
        """Keep endpoints on surviving text when a request header changes."""
        old = entry["text"]
        if (text.startswith(old) or not any(point is not None and point[0] is entry
                                           for point in (self.anchor, self.cursor))):
            return  # Appending must not expand or clear a selection.
        old_lines, new_lines = old.splitlines(keepends=True), text.splitlines(keepends=True)
        def positions(lines):
            result = [0]
            for line in lines:
                result.append(result[-1] + len(line))
            return result
        old_positions, new_positions = positions(old_lines), positions(new_lines)
        changes = SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes()
        for name in ("anchor", "cursor"):
            point = getattr(self, name)
            if point is None or point[0] is not entry:
                continue
            offset = point[1]
            for tag, a, b, c, d in changes:
                start, end = old_positions[a], old_positions[b]
                if a == b or not start <= offset <= end:
                    continue
                new_start, new_end = new_positions[c], new_positions[d]
                relative = offset - start
                if tag == "equal":
                    offset = new_start + relative
                else:
                    # A last, unfinished reasoning line can grow at completion
                    # while the request header changes in a separate block.
                    previous, replacement = old[start:end], text[new_start:new_end]
                    prefix = 0
                    while prefix < min(len(previous), len(replacement)) and previous[prefix] == replacement[prefix]:
                        prefix += 1
                    suffix = 0
                    while (suffix < min(len(previous), len(replacement)) - prefix
                           and previous[-1-suffix] == replacement[-1-suffix]):
                        suffix += 1
                    offset = (new_start + relative if relative <= prefix else
                              new_end - (end - offset) if relative >= len(previous) - suffix else new_start + prefix)
                break
            setattr(self, name, (entry, max(0, min(len(text), offset))))
