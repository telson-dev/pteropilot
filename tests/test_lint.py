"""代码静态检查：抓「会变成用户可见 bug」的那一类问题。

起因：两次在错误分支里漏 import——server.py 缺 ``re``（``_run_log`` 一跑就
NameError）、alerts.py 缺 ``sys``（``--reset`` 删除失败时崩成堆栈而不是提示）。
这两条路平时走不到，测试全绿，但真实用户会撞上。

因此只开「会变成 bug」的规则（见 pyproject.toml 的 [tool.ruff.lint]），
不碰风格问题，避免一开就是几百条噪音。

没装 ruff 时跳过（本地裸环境也能跑 pytest）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _ruff() -> str | None:
    """找到可用的 ruff 入口；找不到返回 None。"""
    try:
        import ruff  # noqa: F401
    except ImportError:
        pass
    # 优先用当前解释器里的 ruff 模块
    try:
        out = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0:
            return "module"
    except (OSError, subprocess.SubprocessError):
        pass
    return None


@pytest.mark.skipif(_ruff() is None, reason="未安装 ruff")
def test_lint_is_clean() -> None:
    """src 与 tests 都不该有未定义名 / 未使用导入 / 未使用变量。"""
    out = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src", "tests",
         "--output-format", "concise"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert out.returncode == 0, (
        "静态检查不通过：\n" + (out.stdout or "") + (out.stderr or "")
    )


@pytest.mark.skipif(_ruff() is None, reason="未安装 ruff")
def test_lint_config_exists() -> None:
    """规则配置要留在 pyproject 里，别被谁顺手删了。"""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.ruff" in text
    for rule in ("F821", "F841", "F401"):
        assert rule in text, f"pyproject 里的 ruff 规则少了 {rule}"
