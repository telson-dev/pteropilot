"""``--doctor`` 的输出排版：分组 + 结论带项数。

为什么值得测：这份输出会被用户**整段贴进 issue / 聊天**求助。
「问题出在哪一块」「一共几项要处理」这两件事必须在版面上一眼可见，
否则读的人得自己数、自己分类——这正是加分组与计数的理由。
"""

from __future__ import annotations

import pytest

from pteropilot import playerlog, server


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """干净环境：无面板配置、无采样、无告警配置，HOME 指到临时目录。"""
    monkeypatch.delenv("PANEL_URL", raising=False)
    monkeypatch.delenv("PANEL_API_KEY", raising=False)
    monkeypatch.delenv("PANEL_APP_KEY", raising=False)
    for k in ("PTEROPILOT_CONCURRENCY", "PTEROPILOT_RETRIES", "PTEROPILOT_RETRY_BASE",
              "PANEL_READONLY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path / "playerlog")
    server.config.__init__()
    return tmp_path


def _doctor(capsys, *, skip: bool = True) -> str:
    server._run_doctor(skip_network=skip)
    return capsys.readouterr().out


# ------------------------------------------------------------------ 分组

def test_sections_present(clean_env, capsys) -> None:
    out = _doctor(capsys)
    assert "── 面板配置 " in out
    assert "── 本机状态 " in out


def test_section_order(clean_env, capsys) -> None:
    out = _doctor(capsys)
    assert out.index("面板配置") < out.index("本机状态")


def test_knobs_section_only_when_set(clean_env, capsys, monkeypatch) -> None:
    """一个参数都没设时不占版面——空的分组头只会让输出更长。"""
    out = _doctor(capsys)
    assert "── 运行参数 " not in out

    monkeypatch.setenv("PTEROPILOT_CONCURRENCY", "6")
    out2 = _doctor(capsys)
    assert "── 运行参数 " in out2
    assert "PTEROPILOT_CONCURRENCY: 6 ✅" in out2


def test_empty_config_section_has_placeholder(clean_env, capsys) -> None:
    """两条都没设时给占位行，避免出现一行内容都没有的分组头。"""
    out = _doctor(capsys)
    assert "（PANEL_URL / PANEL_API_KEY 都未设置）" in out


def test_partial_config_has_no_placeholder(clean_env, capsys, monkeypatch) -> None:
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    out = _doctor(capsys)
    assert "都未设置" not in out


def test_no_network_section_when_skipped(clean_env, capsys) -> None:
    out = _doctor(capsys)
    assert "── 网络 " not in out, "--no-network 时不该有网络分组"


# ------------------------------------------------------------------ 结论

def test_conclusion_counts_problems(clean_env, capsys) -> None:
    out = _doctor(capsys)
    line = [ln for ln in out.splitlines() if ln.startswith("结论：")]
    assert line, "必须有结论行"
    assert "2 项需要处理" in line[0], "缺 URL 与 KEY 共两项"


def test_conclusion_single_problem_count(clean_env, capsys, monkeypatch) -> None:
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    out = _doctor(capsys)
    assert "1 项需要处理" in out, "只缺 PANEL_API_KEY"


def test_ok_conclusion_when_skipping_network(clean_env, capsys, monkeypatch) -> None:
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    out = _doctor(capsys)
    assert "结论：未发现问题（已跳过网络检查 --no-network）" in out


def test_section_line_width_is_consistent(clean_env, capsys) -> None:
    """所有分组线等宽，否则贴出来长短不一。"""
    out = _doctor(capsys)
    widths = {
        server._display_width(ln) for ln in out.splitlines() if ln.startswith("── ")
    }
    assert len(widths) == 1, f"分组线宽度不一致: {widths}"


# ------------------------------------------------------------------ 网络组（不跳网络时）

def test_network_section_when_not_skipped(clean_env, capsys, monkeypatch) -> None:
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    monkeypatch.setattr(server, "_latest_release_version", lambda: None)

    async def boom(self):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(server.PanelClient, "client_servers", boom, raising=False)
    server._run_doctor(skip_network=False)
    out = capsys.readouterr().out
    assert "── 网络 " in out
    assert "查不到最新版" in out
    assert "无法连接面板" in out
