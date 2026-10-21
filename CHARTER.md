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

## 4. 竞品快照（2026-10-21 GitHub 实测更新）

**格局一句话**：① 依旧碎 —— 20+ 散装仓库、绝大多数 ★0–3、最高仍 ★11（BlockMind）；② 出现新范式 —— 从「独立 MCP server」转向「Pterodactyl v2 面板内扩展」，直接以面板用户身份走 OAuth；③ 无人有托管版、无中文、文档普遍差，缝还在。

| 仓库 | ★ | 最后 push | 语言 | 定位 / 弱点 |
|---|---|---|---|---|
| PrimeEcto/BlockMind | 11 | 2026-09 | TS | 多主机聚合型（18 主机+自托管），星最多但面向 MC server 管理，非纯面板运维 |
| pavalso/pterodactyl-mcp | 3 | 2026-09 | TS | 通用 AI 代理；维护稀疏 |
| PixlFlip-Enterprises/PterodactylMCP | 3 | 2026-05 | Py | 面板 API；半年未更 |
| BSpoones/Pterodactyl-MCP | 2 | 2026-09 | TS | 纯 API 调用层，功能薄 |
| jackh54/pterodactyl-panel-mcp | 1 | 2026-10 | PHP | **面板 v2 扩展**（OAuth 2.1，以用户身份操作）；新范式代表 |
| CCBlueX/Pterodactyl-Extension-MCP | 1 | 2026-10 | PHP | **面板 v2 扩展**；官方生态成员 CCBlueX |
| PotenFYR-Studios/PteroOps-MCP | 1 | 2026-10 | TS | 监控+管理，已上 Glama |
| Bippy-s-AI-Helpers/pterodactyl-mcp | 0 | 2026-10 | TS | **105 工具**、default-deny 高危门禁、websocket 控制台；新、功能全但 0 星 |
| RealmsNetwork/MAP | 0 | 2026-10 | Py | **多面板聚合网关**（Pterodactyl/Pelican/Crafty/AMP/MCSManager/PufferPanel） |
| 其余 ~12 个（mediavee、sj58320、Showdown76py/PteroMCP、N1jada、oscardagrach 等） | 0–1 | 分散 | Py/TS | 单面板、无文档、多数断档或已归档 |

**关键读取**：
- 兼容 Pelican 的仍是少数（PteroMCP、mediavee、MAP 多面板型）。双面板仍是差异化，但 MAP 已把「多面板聚合」做到更宽。
- 新威胁：CCBlueX 的「面板扩展」路线 —— 若 Pterodactyl 社区自己推扩展生态，独立 MCP server 的位子会被挤。PteroPilot 的护城河在**质量（测试/覆盖率/安全）+ 中文 + uvx 易装 + 后市托管**，而非面板本身。
- 弱点共性依旧成立：文档差、无托管、维护断档、安全粗糙、无中文。

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