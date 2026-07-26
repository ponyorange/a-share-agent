"""Guard: reject/correct hallucinated knowledge write success claims."""

from __future__ import annotations

import json

from app.advisor.agent.knowledge_write_guard import (
    KNOWLEDGE_WRITE_CORRECTION,
    apply_knowledge_write_guard,
    claims_knowledge_persisted,
    successful_knowledge_mutations,
)


def test_claims_knowledge_persisted_detects_common_phrases():
    assert claims_knowledge_persisted("✅ 02_量价基础 已写入成功！")
    assert claims_knowledge_persisted("全部 8 章节已写入完毕")
    assert claims_knowledge_persisted("5条一次性补写入库成功")
    assert claims_knowledge_persisted("已成功存入知识库")
    assert not claims_knowledge_persisted("📄 条目 2/8 预览——请确认后写入")
    assert not claims_knowledge_persisted("拟存入知识库的新条目")


def test_successful_mutations_from_tool_trace():
    ok_payload = json.dumps(
        {"ok": True, "item": {"id": "k1", "title": "纪律"}},
        ensure_ascii=False,
    )
    preview = json.dumps(
        {"ok": False, "needs_confirm": True, "preview": {"title": "纪律"}},
        ensure_ascii=False,
    )
    trace = [
        {"tool": "list_knowledge", "content": '{"ok": true}'},
        {"tool": "save_knowledge", "content": preview},
        {"tool": "save_knowledge", "content": ok_payload},
        {
            "tool": "delete_knowledge",
            "content": json.dumps({"ok": True, "deleted": True}, ensure_ascii=False),
        },
    ]
    mut = successful_knowledge_mutations(trace)
    assert mut["save"] == 1
    assert mut["delete"] == 1


def test_guard_appends_correction_when_claim_without_tool_success():
    text = "✅ **02_量价基础** 已写入成功！\n\n---\n预览下一条"
    out = apply_knowledge_write_guard(text, tool_trace=[])
    assert "已写入成功" in out
    assert KNOWLEDGE_WRITE_CORRECTION in out


def test_guard_noop_when_save_ok_true_present():
    text = "✅ **01_核心公理** 已写入成功！"
    ok_payload = json.dumps(
        {"ok": True, "item": {"id": "k1", "title": "01"}},
        ensure_ascii=False,
    )
    out = apply_knowledge_write_guard(
        text,
        tool_trace=[{"tool": "save_knowledge", "content": ok_payload}],
    )
    assert out == text
    assert KNOWLEDGE_WRITE_CORRECTION not in out


def test_guard_corrects_when_only_preview_tool_result():
    text = "已写入知识库！"
    preview = json.dumps(
        {"ok": False, "needs_confirm": True, "message": "未确认"},
        ensure_ascii=False,
    )
    out = apply_knowledge_write_guard(
        text,
        tool_trace=[{"tool": "save_knowledge", "content": preview}],
    )
    assert KNOWLEDGE_WRITE_CORRECTION in out


def test_system_prompt_forbids_claim_without_tool_ok():
    from app.advisor.agent.graph import SYSTEM_PROMPT

    assert "不得声称已写入" in SYSTEM_PROMPT
    assert "ok\": true" in SYSTEM_PROMPT or 'ok: true' in SYSTEM_PROMPT
    assert "禁止用纯文本假装预览" in SYSTEM_PROMPT
    assert "每一条都要有对应的 confirm=true" in SYSTEM_PROMPT
