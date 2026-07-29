from app.advisor.monitor.run_at import scrub_run_at_email_body


def test_scrub_removes_email_confirm_wrapper():
    raw = """以下是完整的 **晚间行情研判报告**

## 一、市场回顾
沪指收涨。

📧 **邮件预览：** 收件人 `904925485@qq.com`，标题「晚间行情」。

**爸爸，报告写好了，这封邮件要发出去吗？** 👍
"""
    out = scrub_run_at_email_body(raw)
    assert "市场回顾" in out
    assert "邮件预览" not in out
    assert "要发出去吗" not in out
    assert "📧" not in out


def test_scrub_keeps_clean_report():
    raw = "## 晚间研判\n\n结论：观望。"
    assert scrub_run_at_email_body(raw) == raw
