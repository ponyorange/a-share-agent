from __future__ import annotations

from app.advisor.committee.chat_stream import (
    IncrementalChatMessageParser,
    message_id_for,
    system_message,
)


def test_parser_emits_only_decoded_chat_message_text():
    parser = IncrementalChatMessageParser()
    chunks = [
        '{"chat_mes',
        'sage":"看多\\n沪深',
        '300，目标\\u6743重 20%。","confidence":0.8}',
    ]
    assert [delta for chunk in chunks for delta in parser.feed(chunk)] == [
        "看多\n沪深",
        "300，目标权重 20%。",
    ]


def test_parser_decodes_newline_when_escape_introducer_spans_chunks():
    parser = IncrementalChatMessageParser()
    assert parser.feed('{"chat_message":"A\\') == ("A",)
    assert parser.feed('nB","thesis":"不得展示"}') == ("\nB",)
    assert parser.feed(" trailing") == ()


def test_parser_decodes_literal_backslash_when_second_backslash_spans_chunks():
    parser = IncrementalChatMessageParser()
    assert parser.feed('{"chat_message":"A\\') == ("A",)
    assert parser.feed('\\B","thesis":"不得展示"}') == ("\\B",)
    assert parser.feed(" trailing") == ()


def test_parser_merges_utf16_surrogate_pair_in_same_chunk():
    parser = IncrementalChatMessageParser()
    assert parser.feed('{"chat_message":"\\uD83D\\uDE00"}') == ("😀",)


def test_parser_merges_utf16_surrogate_pair_across_chunks():
    parser = IncrementalChatMessageParser()
    assert parser.feed('{"chat_message":"\\uD83') == ()
    assert parser.feed("D\\uDE") == ()
    assert parser.feed('00"}') == ("😀",)


def test_parser_rejects_isolated_high_surrogate():
    parser = IncrementalChatMessageParser()
    assert parser.feed('{"chat_message":"\\uD83D"}') == ()
    assert parser.compatible is False


def test_parser_rejects_isolated_low_surrogate():
    parser = IncrementalChatMessageParser()
    assert parser.feed('{"chat_message":"\\uDE00"}') == ()
    assert parser.compatible is False


def test_parser_rejects_leading_non_object_prefix():
    parser = IncrementalChatMessageParser()
    assert parser.feed('prefix {"chat_message":"x"}') == ()
    assert parser.compatible is False


def test_parser_rejects_missing_colon_after_key():
    parser = IncrementalChatMessageParser()
    assert parser.feed('{"chat_message" "x"}') == ()
    assert parser.compatible is False


def test_parser_waits_when_chunk_ends_at_valid_boundary():
    parser = IncrementalChatMessageParser()
    assert parser.feed('{"chat_message"') == ()
    assert parser.compatible is True
    assert parser.feed(': "hi"}') == ("hi",)


def test_parser_rejects_non_string_value():
    parser = IncrementalChatMessageParser()
    assert parser.feed('{"chat_message":123}') == ()
    assert parser.compatible is False


def test_parser_rejects_unknown_escape():
    parser = IncrementalChatMessageParser()
    assert parser.feed('{"chat_message":"\\q"}') == ()
    assert parser.compatible is False


def test_parser_waits_for_object_start_across_chunks():
    parser = IncrementalChatMessageParser()
    assert parser.feed("  ") == ()
    assert parser.compatible is True
    assert parser.feed('{"chat_message":"ok"}') == ("ok",)


def test_stable_message_id_includes_attempt_and_round():
    assert message_id_for("r1", 1, "bull", 1) == message_id_for("r1", 1, "bull", 1)
    assert message_id_for("r1", 1, "bull", 1) != message_id_for("r1", 2, "bull", 1)
    assert message_id_for("r1", 1, "bull", 1) != message_id_for("r1", 1, "bull", 2)


def test_system_message_references_expandable_card():
    payload = system_message(
        run_id="r1",
        attempt=2,
        node="backtest",
        sequence=9,
        content="回测通过，综合得分 0.81。",
        card_kind="backtest_verdict",
    )
    assert payload.status == "completed"
    assert payload.card_ref is not None
    assert payload.card_ref.model_dump() == {
        "attempt": 2,
        "node": "backtest",
        "kind": "backtest_verdict",
    }
