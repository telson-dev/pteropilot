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
