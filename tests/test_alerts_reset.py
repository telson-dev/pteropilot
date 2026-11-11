"""`pteropilot-alerts --reset` 测试：清空冷却/确认状态。

用户常见场景：调完灵敏度想立刻验证、或服务器已修好但还卡在冷却期里看不到新告警。
"""

from __future__ import annotations

import json

from pteropilot import alerts


def _run_reset(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["pteropilot-alerts", "--reset"] + argv)
    try:
        alerts.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_reset_deletes_state_file(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state.json"
    state.write_text('{"srv": 1}', encoding="utf-8")
    assert state.exists()

    code = _run_reset(monkeypatch, ["--state-file", str(state)])
    assert code == 0
    assert not state.exists(), "状态文件应被删除"
    out = capsys.readouterr().out
    assert "已清空冷却状态" in out


def test_reset_missing_file_is_ok(tmp_path, monkeypatch, capsys):
    state = tmp_path / "missing.json"
    code = _run_reset(monkeypatch, ["--state-file", str(state)])
    assert code == 0
    assert "无需清空" in capsys.readouterr().out


def test_reset_dry_run_keeps_file(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state.json"
    state.write_text('{"srv": 1}', encoding="utf-8")
    code = _run_reset(monkeypatch, ["--state-file", str(state), "--dry-run"])
    assert code == 0
    assert state.exists(), "--dry-run 不应删除文件"
    assert "将删除" in capsys.readouterr().out


def test_reset_json_output_is_machine_readable(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    code = _run_reset(monkeypatch, ["--state-file", str(state), "--json"])
    assert code == 0
    out = capsys.readouterr().out.strip()
    data = json.loads(out)  # 必须是纯 JSON，不能混人类可读文本
    assert data["reset"] is True
    assert data["removed"] is True
    assert data["path"].endswith("state.json")


def test_reset_does_not_require_notification_channel(tmp_path, monkeypatch):
    """--reset 只动本地状态，没配 webhook/邮件也不应报错退出。"""
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    code = _run_reset(monkeypatch, ["--state-file", str(state)])
    assert code == 0
