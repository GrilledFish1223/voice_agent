from __future__ import annotations


FIRST_MARKS = set("，,。！？；：.!?;:\n")
LATER_MARKS = set("。！？.!?\n")


class SentenceBuffer:
    def __init__(self) -> None:
        self._text = ""
        self.first_sent = False

    def push(self, chunk: str) -> list[str]:
        self._text += chunk
        ready: list[str] = []
        while True:
            marks = FIRST_MARKS if not self.first_sent else LATER_MARKS
            cut = self._find_cut(marks, require_four=not self.first_sent)
            if cut is None:
                return ready
            is_first = not self.first_sent
            ready.append(self._text[: cut + 1].strip())
            self._text = self._text[cut + 1 :]
            self.first_sent = True
            if is_first:
                return ready

    def flush(self) -> list[str]:
        tail = self._text.strip()
        self._text = ""
        return [tail] if tail else []

    def _find_cut(self, marks: set[str], require_four: bool) -> int | None:
        for index, char in enumerate(self._text):
            if char not in marks:
                continue
            if require_four and len(self._text[: index + 1].strip()) < 4:
                continue
            return index
        return None
