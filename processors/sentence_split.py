from __future__ import annotations

from collections.abc import AsyncIterator

from pipecat.utils.text.base_text_aggregator import Aggregation, BaseTextAggregator, TextType


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
            cut = self._find_cut(marks, min_chars=2 if not self.first_sent else 0)
            if cut is None:
                return ready
            is_first = not self.first_sent
            sentence = self._text[: cut + 1].strip()
            self._text = self._text[cut + 1 :]
            self.first_sent = True
            if sentence:
                ready.append(sentence)
            if is_first:
                return ready

    def flush(self) -> list[str]:
        ready: list[str] = []
        while True:
            cut = self._find_cut(LATER_MARKS, min_chars=0)
            if cut is None:
                break
            sentence = self._text[: cut + 1].strip()
            self._text = self._text[cut + 1 :]
            if sentence:
                ready.append(sentence)
        tail = self._text.strip()
        self._text = ""
        if tail:
            ready.append(tail)
        return ready

    def reset(self) -> None:
        self._text = ""
        self.first_sent = False

    def _find_cut(self, marks: set[str], min_chars: int) -> int | None:
        for index, char in enumerate(self._text):
            if char not in marks:
                continue
            if min_chars and len(self._text[: index + 1].strip()) < min_chars:
                continue
            return index
        return None


class FirstClauseAggregator(BaseTextAggregator):
    """首句按逗号切开，后续按句号切开，尽快把文本交给腾讯云 TTS。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(aggregation_type=TextType.SENTENCE, **kwargs)
        self._buffer = SentenceBuffer()

    @property
    def text(self) -> Aggregation:
        return Aggregation(text=self._buffer._text.strip(), type=TextType.SENTENCE)

    async def aggregate(self, text: str) -> AsyncIterator[Aggregation]:
        for sentence in self._buffer.push(text or ""):
            yield Aggregation(text=sentence, type=TextType.SENTENCE)

    async def flush(self) -> Aggregation | None:
        parts = self._buffer.flush()
        if not parts:
            return None
        return Aggregation(text="".join(parts), type=TextType.SENTENCE)

    async def handle_interruption(self) -> None:
        self._buffer.reset()

    async def reset(self) -> None:
        self._buffer.reset()
