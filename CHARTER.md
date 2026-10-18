# 立项卡 — PteroPilot（Pterodactyl / Pelican 的 AI 管理 MCP）

- 日期：2026-09-26（立项）· 2026-10-18（更新）
- 状态：🟢 已上线（v0.2.1，PyPI 已发，MineBBS 首帖已发）
- 代号：**PteroPilot**（2026-09-27 改名，见 §7）
- 母品牌：telson（telson.dev）
- 项目根目录：D:\Projects\pteropilot

## 1. 一句话
给游戏服务器管理面板（Pterodactyl / Pelican）做一个开源 MCP server：让 AI（Claude 等）代替人管理游戏服务器。**开源免费获客 → 托管版收订阅**。

## 2. 目标用户
- 游戏服务器**托管商**（几千家，已有付费习惯：主题 / 模块 / WHMCS 生态）
- 社区服主 / 单人运维（省人力）
- 中文用户（差异化：双语文档）

## 3. 当前状态（截至 2026-10-18）
- **版本**：v0.2.1（PyPI 已发，`uvx pteropilot` 一键装）
- **仓库**：github.com/telson-dev/pteropilot
- **首发引流**：MineBBS 首帖已发 —— https://www.minebbs.com/threads/ai-mc-pteropilot.50303/
- **上架**：awesome-mcp-servers PR、mcp.so 待补（材料见 docs/listing-submission.md）
- **已完成功能**（v0 MVP 全部落地）：
  - [x] 服务器/节点列表（在线状态、资源占用）
  - [x] 电源控制：启动 / 停止 / 重启 / 强杀（danger 二次确认）
  - [x] 实时控制台：读日志（get_console_log）、发指令
  - [x] 文件管理：列表 / 读取 / 写入 / 上传 / 重命名 / 删除 / 新建文件夹 / 下载链接
  - [x] 备份：创建 / 列表 / 恢复 / 删除
  - [x] 安全：只读模式开关、确认机制、不泄漏 token

## 4. 竞品快照（2026-09-26 GitHub 实测）
- 10+ 个散装仓库，多为 ★0–3；最高 ★11（BlockMind，多主机聚合型）
- 大多只支持 Pterodactyl；个别已兼容 Pelican（PteroMCP、mediavee 等）
- 共性弱点：文档差、无托管版、维护断档、安全粗糙、无中文
- （后续全网补充：MineCraftAI、PixelCloud 为产品级竞品，PteroPilot 的缝是「已有面板的 AI 运维层」）

## 5. 差异化（4 张牌）
1. **双面板**：Pterodactyl + Pelican 都支持（行业正在换代迁移，卡位时机）
2. **质量**：测试 + 语义化版本 + CHANGELOG + 认真做安全（token 处理、危险操作确认）
3. **易用**：`uvx` 一键安装 + 中英双语文档
4. **托管版**（后置变现）：零部署云版

## 6. 技术方案
- Python 3.12+；`mcp` SDK；httpx
- 目录：`src/pteropilot/`（client / server / config）+ `tests/`
- 配置走环境变量：PANEL_URL / PANEL_API_KEY / PANEL_READONLY
- 分发：PyPI + uvx
- 不做：计费 / WHMCS 集成（v1 之后再谈）

## 7. 名字
**PteroPilot** — 翼龙领航员（2026-09-27 季总定名）。
改名原因：原名 PanelPilot 与硬件品牌 Lascar PanelPilot 及多个工控/PLC 仓撞名。
查重（2026-09-27）：GitHub 0 仓、npm/PyPI 均 free；`Ptero-` 直指 Pterodactyl，语义更强。

## 8. 下一步（backlog）
- [ ] awesome-mcp-servers PR + mcp.so 上架
- [ ] 真机端到端实测（见 deploy/DEPLOY.md，阿里云 Linux 跑一套面板 + MC 服）
- [ ] 功能：查玩家在线 / 崩服告警 / 批量开服（用户提的需求方向）
- [ ] 托管版（后置变现）

## 9. 分工
- **小虾**：竞品、代码、文案、上架、进度跟踪
- **季总**：GitHub 账号、拍板、用户访谈、发帖

## 10. 成功指标 & 退路
- 第 1 周：repo 上线 + 目录收录 ✅
- 第 2–4 周：有真实 star / issue / 至少 1 人问「有没有托管」→ 进行中
- 4 周无信号 → 按框架换方向（不恋战）