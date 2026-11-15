"""``python -m pteropilot`` 入口。

这个文件只有 4 行，但它是 PATH 里没有 console script 时唯一的退路，
CI 的 90% 覆盖率门槛也会被它的 0% 拖累，所以给它一个测试。
"""

from __future__ import annotations

import runpy

from pteropilot import server


def test_python_m_invokes_server_main(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(server, "main", lambda: called.append("main"))
    # runpy 会以 __name__ == "__main__" 执行包里的 __main__.py，
    # 它 import 的是 server.main（已被替换），因此不会真的启动 server。
    runpy.run_module("pteropilot", run_name="__main__")
    assert called == ["main"]
