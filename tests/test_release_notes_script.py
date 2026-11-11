"""release notes 同步脚本的纯函数部分（章节提取 / 小标题翻译）测试。

不触网：只验证从 CHANGELOG 里抽取与整理内容的逻辑。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sync_release_notes as srn  # noqa: E402


def test_changelog_section_extracts_latest_block():
    sec = srn.changelog_section("0.2.22")
    assert sec is not None
    assert "list_schedules" in sec
    # 不能把下一个版本的内容吃进来
    assert "0.2.21" not in sec


def test_changelog_section_unknown_version_returns_none():
    assert srn.changelog_section("9.9.9") is None


def test_translate_sections_turns_english_headers_to_chinese():
    out = srn.translate_sections("### Added\n- a\n\n### Fixed\n- b")
    assert "### 新增" in out
    assert "### 修复" in out
    assert "### Added" not in out


def test_translate_sections_keeps_unknown_headers():
    out = srn.translate_sections("### Whatever\n- x")
    assert "### Whatever" in out


def test_install_section_includes_version_and_selfcheck():
    text = srn.INSTALL_SECTION.format(version="0.2.22")
    assert "pteropilot-0.2.22-py3-none-any.whl" in text
    assert "pteropilot --version" in text


def test_script_usage_requires_token():
    """没有 GH_TOKEN 时应给明确提示，而不是 traceback。"""
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_release_notes.py"), "--dry-run"],
        capture_output=True,
        text=True,
        env={"GH_TOKEN": "", "PATH": ""},
        cwd=ROOT,
    )
    assert out.returncode == 1
    assert "GH_TOKEN" in (out.stdout + out.stderr)



def test_retryable_classifies_network_errors():
    """网络抖动应重试；HTTPError 是逻辑问题不该重试。"""
    import http.client
    import urllib.error

    assert srn._retryable(http.client.RemoteDisconnected("x"))
    assert srn._retryable(TimeoutError("x"))
    assert srn._retryable(ConnectionError("x"))
    assert srn._retryable(urllib.error.URLError("x"))
    assert not srn._retryable(ValueError("x"))


def test_api_retries_then_succeeds(monkeypatch):
    """第一次网络断开、第二次成功时应自动重试并返回结果。"""
    import http.client
    import urllib.request

    calls = {"n": 0}

    class _Resp:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, data=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise http.client.RemoteDisconnected("boom")
        return _Resp()

    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda *_: None)  # 脚本内 import time as _time，patch 全局即可
    code, data = srn.api("GET", "/x")
    assert calls["n"] == 2
    assert code == 200 and data == {"ok": True}
