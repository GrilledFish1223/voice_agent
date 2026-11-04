from voice_agent.processors.sentence_split import SentenceBuffer


def test_first_sentence_waits_until_four_characters():
    buf = SentenceBuffer()
    assert buf.push("你好，") == []
    assert buf.push("世界。") == ["你好，世界。"]
    assert buf.first_sent is True


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
