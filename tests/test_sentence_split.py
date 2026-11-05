import pytest

from voice_agent.processors.sentence_split import FirstClauseAggregator, SentenceBuffer


def test_first_sentence_cuts_short_ack():
    # 口语首句经常是「好的，」，不能再等满 4 个字才送 TTS
    buf = SentenceBuffer()
    assert buf.push("好的，") == ["好的，"]
    assert buf.first_sent is True


def test_first_sentence_ignores_lone_punctuation():
    buf = SentenceBuffer()
    assert buf.push("，") == []
    assert buf.push("好的。") == ["，好的。"]


def test_first_sentence_cuts_on_comma_after_four_characters():
    buf = SentenceBuffer()
    assert buf.push("今天天气不错，我们出发吧。") == ["今天天气不错，"]
    assert buf.push("") == ["我们出发吧。"]


def test_later_sentence_ignores_comma():
    buf = SentenceBuffer()
    buf.push("今天天气不错，")
    assert buf.push("我们先去，再到那边。") == ["我们先去，再到那边。"]


def test_flush_emits_remaining_text():
    buf = SentenceBuffer()
    buf.push("今天天气不错，还有一点")
    assert buf.flush() == ["还有一点"]


def test_flush_splits_remaining_on_later_punctuation():
    buf = SentenceBuffer()
    assert buf.push("今天天气不错，我们出发吧。明天再看。") == ["今天天气不错，"]
    assert buf.flush() == ["我们出发吧。", "明天再看。"]


def test_push_skips_empty_sentences():
    buf = SentenceBuffer()
    first = buf.push("没问题，\n我来帮你。")
    later = buf.push("")
    assert "" not in first
    assert "" not in later
    assert later == ["我来帮你。"]


def test_reset_clears_buffer_and_allows_first_sentence_again():
    buf = SentenceBuffer()
    buf.push("今天天气不错，")
    assert buf.first_sent is True
    buf.reset()
    assert buf.first_sent is False
    assert buf.flush() == []
    assert buf.push("今天天气不错，我们出发吧。") == ["今天天气不错，"]


def test_flush_does_not_reset_first_sent():
    buf = SentenceBuffer()
    buf.push("今天天气不错，还有一点")
    assert buf.flush() == ["还有一点"]
    assert buf.first_sent is True
    assert buf.push("我们先去，再到那边。") == ["我们先去，再到那边。"]


@pytest.mark.asyncio
async def test_first_clause_aggregator_cuts_ack_then_sentence():
    aggregator = FirstClauseAggregator()
    first = [item async for item in aggregator.aggregate("好的，我来帮你。")]
    assert [item.text for item in first] == ["好的，"]
    remaining = await aggregator.flush()
    assert remaining is not None
    assert remaining.text == "我来帮你。"
