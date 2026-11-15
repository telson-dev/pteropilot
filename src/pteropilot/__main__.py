"""让 ``python -m pteropilot`` 等价于 ``pteropilot`` 命令。

入口本来只有 console script（``pteropilot = pteropilot.server:main``），
所以 ``python -m pteropilot --version`` 会报
"pteropilot is a package and cannot be directly executed"。
没装 script 的场合（比如 `pip install --user` 后 PATH 没配好）这是唯一的退路。
"""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    main()
