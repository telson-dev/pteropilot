# 发布 pteropilot 到 PyPI（需要账号 WinbowJi 的 API 令牌）

# 1) 在 https://pypi.org/manage/account/token/ 创建（scope: entire account 或 project pteropilot）
# 2) 设置令牌后执行：
#    $env:UV_PUBLISH_TOKEN="pypi-..."   （PowerShell）
#    uv publish
# 产物已备好：dist/pteropilot-0.2.5-py3-none-any.whl + dist/pteropilot-0.2.5.tar.gz
# 发布后：uvx pteropilot 即装到 v0.2.5，PyPI 描述自动换成新版 README。
