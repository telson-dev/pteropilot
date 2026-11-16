"""文档与代码的一致性守卫。

命令行面已经涨到 9 个 flag，散落在三处：``main()`` 的分发、
``_print_help()`` 的速查表、README 的示例。改动其中一个而忘了另外两个，
用户就会看到「帮助里写着的命令跑不通」或者「跑得通的命令没人知道」。

这个测试把三者钉在一起，漂移立刻变红。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from pteropilot import server

ROOT = Path(__file__).resolve().parent.parent

# 顶层分发用的大写 flag（main() 里 if "--x" in argv 的那些）
TOP_LEVEL = {
    "--help", "--version", "--doctor", "--status",
    "--players", "--log", "--broadcast", "--power",
}

# 只作为某个子命令的选项存在、不单独分发的 flag
SUB_OPTIONS = {
    "--no-network", "--json", "--lines", "--grep", "--watch", "--interval",
    "--rounds", "--days", "--top", "--servers", "--confirm", "--in", "--at",
    "--backups", "--backup-days", "--record",
}

SHORTHANDS = {"-h", "-V", "-v", "help"}


def _dispatched_flags() -> set[str]:
    """从 main() 的函数体里抠出真正被分发的 flag。

    必须限定在 ``if "..." in argv`` 这种条件里——``skip_network = "--no-network"
    in argv`` 这类赋值也含 ``in argv``，但它不是顶层分发。
    """
    src = inspect.getsource(server.main)
    return set(re.findall(r'if\s+"(--[a-z][a-z-]*)"\s+in\s+argv', src))


def _help_text() -> str:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        server._print_help()
    return buf.getvalue()


@pytest.fixture(scope="module")
def dispatched() -> set[str]:
    return _dispatched_flags()


# ------------------------------------------------------------------ 代码 vs 帮助

def test_every_dispatched_flag_is_in_help(dispatched: set[str]) -> None:
    """跑得通的 flag 必须出现在 --help 里，否则没人知道它存在。"""
    help_text = _help_text()
    missing = sorted(f for f in dispatched if f not in help_text)
    assert not missing, f"--help 里没有这些能用的 flag: {missing}"


def test_help_has_no_phantom_flags(dispatched: set[str]) -> None:
    """--help 里提到的 flag 必须真的能用，或者是某个子命令的选项。"""
    help_flags = set(re.findall(r"(--[a-z][a-z-]*)", _help_text()))
    phantom = sorted(help_flags - dispatched - SUB_OPTIONS)
    assert not phantom, f"--help 写了但代码里不存在的 flag: {phantom}"


def test_help_lists_all_top_level(dispatched: set[str]) -> None:
    assert dispatched == TOP_LEVEL, (
        f"分发面变了。新增/删除 flag 时请同步更新本文件的 TOP_LEVEL 与 --help。\n"
        f"  代码里有但清单没有: {sorted(dispatched - TOP_LEVEL)}\n"
        f"  清单里有但代码没有: {sorted(TOP_LEVEL - dispatched)}"
    )


# ------------------------------------------------------------------ 帮助 vs README

def test_readme_flags_all_exist() -> None:
    """README 示例里出现的 flag 必须是真实存在的，不能是幻想出来的。

    只从 ``pteropilot ...`` 的示例里取；``pteropilot-alerts`` 是另一个 console
    script，它的 ``--webhook`` / ``--smtp-host`` 等等不在本文件的范围内。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    used = set(re.findall(r"pteropilot\s+(--[a-z][a-z-]*)", readme))
    phantom = sorted(used - TOP_LEVEL - SUB_OPTIONS - {"--version", "--doctor"})
    assert not phantom, f"README 用了不存在的 flag: {phantom}"


@pytest.mark.parametrize("readme_name", ["README.md", "README.zh-CN.md"])
def test_chinese_readme_covers_the_new_cli(readme_name: str) -> None:
    """两份 README 都得跟上命令行：--status/--log/--players 是"不开 AI 也能用"
    的全部理由，缺一个中文用户就找不到。"""
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    for flag in ("--status", "--log", "--players", "--watch"):
        assert flag in text, f"{readme_name} 没写 {flag}"


@pytest.mark.parametrize("readme_name", ["README.md", "README.zh-CN.md"])
def test_readme_documents_multi_server_log(readme_name: str) -> None:
    """--log 支持 "*" 与 a,b 之后，两份 README 都必须写出来。"""
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    assert '--log "*"' in text or "--log smp,creative" in text, (
        f"{readme_name} 没写 --log 的多台用法（\"*\" / a,b）"
    )


def test_readme_does_not_promise_auto_retry_for_writes() -> None:
    """重试规则最容易写错：只读会重试，写操作不会。README 必须说清。"""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "never retried" in readme or "从不重试" in readme, (
        "README 必须说明写操作不会自动重试"
    )


# ------------------------------------------------------------------ 帮助自身

def test_help_fits_one_screen() -> None:
    lines = _help_text().rstrip().splitlines()
    assert len(lines) <= 45, f"--help 有 {len(lines)} 行，终端一屏放不下"


def test_help_shows_confirm_for_dangerous() -> None:
    assert "--confirm" in _help_text()
