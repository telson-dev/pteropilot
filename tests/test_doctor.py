"""`pteropilot --doctor` 环境自检测试。

用户报「连不上面板」时让他跑这个并贴输出，比自己猜环境快。
关键要求：结论与退出码必须如实反映问题（连不上 ≠ 环境正常），且输出不得泄露密钥。
"""

from __future__ import annotations

import sys

import pytest

from pteropilot import server as server_mod


def _run_doctor(monkeypatch, argv=("--doctor",), env=None):
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY", "PANEL_READONLY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.argv", ["pteropilot", *argv])
    try:
        server_mod.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_doctor_without_config_reports_problems(monkeypatch, capsys):
    code = _run_doctor(monkeypatch)
    out = capsys.readouterr().out
    assert code == 1
    assert "未设置 PANEL_URL" in out
    assert "未设置 PANEL_API_KEY" in out
    assert "个工具" in out  # 版本与工具数


def test_doctor_no_network_skips_connectivity(monkeypatch, capsys):
    """--no-network 只查配置，应快速返回且不因连不上而报错。"""
    code = _run_doctor(
        monkeypatch,
        argv=("--doctor", "--no-network"),
        env={"PANEL_URL": "https://panel.example.com", "PANEL_API_KEY": "ptlc_x"},
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "已跳过网络检查" in out


def test_doctor_shows_url_normalization(monkeypatch, capsys):
    """地址带 /api 时应显示已自动规范化（这条提示能直接定位用户配置错误）。"""
    code = _run_doctor(
        monkeypatch,
        argv=("--doctor", "--no-network"),
        env={"PANEL_URL": "https://panel.example.com/api/", "PANEL_API_KEY": "ptlc_x"},
    )
    out = capsys.readouterr().out
    assert "已自动规范化" in out
    assert "https://panel.example.com/api/" in out


def test_doctor_connect_failure_marks_problem(monkeypatch, capsys):
    """连不上面板必须算问题（不能报「环境正常」并以 0 退出）。"""
    monkeypatch.setenv("PANEL_URL", "https://panel.invalid.example")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_x")

    async def boom(self):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(server_mod.PanelClient, "client_servers", boom, raising=False)
    monkeypatch.setattr("sys.argv", ["pteropilot", "--doctor"])
    with pytest.raises(SystemExit) as exc:
        server_mod.main()
    assert int(exc.value.code or 0) == 1
    out = capsys.readouterr().out
    assert "无法连接面板" in out
    assert "环境正常" not in out


def test_doctor_never_prints_full_api_key(monkeypatch, capsys):
    secret = "ptlc_SUPERSECRETVALUE123"
    _run_doctor(
        monkeypatch,
        argv=("--doctor", "--no-network"),
        env={"PANEL_URL": "https://panel.example.com", "PANEL_API_KEY": secret},
    )
    out = capsys.readouterr().out
    assert secret not in out, "诊断输出泄露了完整 API Key"
    assert "ptlc_" in out  # 只显示前缀
