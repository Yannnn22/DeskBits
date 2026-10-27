# DeskBits 发布说明

## v0.1.0 · 本地优先桌面 AI 工作台

本仓库是 DeskBits 的首个公开快照，包含 Electron + Vue 桌面端、Python Agent Service、设计文档与本地演示素材。

### 包含内容

- **桌面端**：对话、工作区、项目文件树、知识库、任务、提醒、设置、桌宠浮窗。
- **Agent Service**：FastAPI + WebSocket、LangGraph Agent、工具注册表、Skills、记忆 / 画像、知识库 RAG、权限审批。
- **本地存储**：SQLite 主库、本地文件、JSONL trace；Redis / Milvus 可选。
- **模型接入**：DeepSeek / OpenAI 兼容接口；没有 Key 时回退 FakeModelProvider。
- **工程化**：`start:mac` 自动拉起 Python 子进程、随机本地 token、Fernet 加密 Key、allowed roots。

### 验证基线

以下数据来自 v0.1.0 发布基线（2026-08-06）：

| 项目 | 结果 |
| --- | --- |
| 后端 pytest | 332 passed |
| 后端 ruff | clean |
| 前端构建 | `vue-tsc --noEmit` + `vite build` 通过 |
| 前端端到端 | 29 / 29 通过 |
| 合并验收 | 全新实例 + 真实库，10 / 10 验收点通过 |

> 本仓库快照包含桌面端后续的工作区 UI 更新；上面的数字反映发布基线，不替代当前分支的全量回归。

### 已知边界

1. 大文档总结当前取前 8 个分片（约 4000 字），不是全文摘要。
2. 默认使用 fake embedding；可切换 OpenAI 兼容 embedding，或启用 Milvus。
3. 保存知识条目需要权限确认，这是有意的安全设计。
4. 知识库未命中时的检索阈值目前没有独立配置项。
5. 桌面打包与首次启动引导尚未纳入本版本。

### 下一阶段

- 打通 Today / Files / Tasks / Knowledge / Chat 五个入口。
- 完善 `read → diff → patch → apply → confirm` 工具链与统一任务模型。
- 桌宠桌面控制、剪贴板收藏与自动冒烟。
- 多 Agent 编排与更细粒度的项目权限。

### 升级提示

- 修改后端代码后需要完全退出并重启 Electron，Python 子进程才会重新加载。
- `.env`、`keys.enc`、`~/.desktopmate` 只属于本机，不要提交或分享。
- 升级前建议备份 `~/.desktopmate/desktopmate.db`。
