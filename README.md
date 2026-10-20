# Research Assistant V0.7.2

**中文** | [English](README.en.md)

Windows 本地、只读、可溯源的 Zotero 科研证据助手。它从本地 PDF 中检索原文，保留
Zotero 条目、附件、DOI 和物理页码，并把证据区分为 DIRECT、SECONDARY、LIMITING、
CONTEXTUAL 和 UNCERTAIN。

V0.7.2 的稳定架构是：

```text
GUI / Codex MCP / future clients -> Persistent Core -> Evidence Engine -> local literature
```

Persistent Core 是 Evidence Engine、FAISS、BGE embedding 模型和 Evidence Report
handle cache 的唯一所有者。GUI 和 MCP 都是轻量客户端。所有科学查询均为只读操作。

## 当前能力

- Zotero Local API GET-only；不打开或修改 `zotero.sqlite`。
- PDF 逐物理页提取、原文展示和页码回溯。
- SQLite FTS5、BGE semantic search 和 RRF hybrid retrieval。
- 增量索引、内容哈希、明确的 Full Rebuild、失败隔离和取消；关键词索引与语义索引一次更新。
- 支撑信息（SI）：放在文献 Zotero storage 文件夹中、未在 Zotero 登记的 SI PDF 也会随该文献
  入库；证据输出中以 `supplementary` 标出来自 SI 的段落。
- Evidence Pack 与分类 Evidence Report。
- 引用溯源、保守的论文身份合并和来源去重。
- `find_evidence_for_claim`、`get_source` 和 `verify_citation`。
- PySide6 桌面 GUI、Persistent Core、Claude/Codex MCP 接口。
- Windows x64 便携 EXE；Core 常驻系统托盘，可右键打开 GUI 或明确退出。

Research Assistant 判断的是本地证据与论断的关系，不判断科学真理，也不使用网络搜索
或通用 LLM 补写缺失事实。

## 从源码运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start.ps1
```

也可分别启动：

```powershell
.\.venv\Scripts\python.exe -m app.core_service
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m app.external_api.mcp_server
```

Zotero 中需启用：Settings → Advanced →
**Allow other applications on this computer to communicate with Zotero**。
建立或更新索引时 Zotero 需要运行；完成后本地搜索不依赖 Zotero 进程。

## Windows 便携版

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\packaging\build-windows.ps1
# 只改了代码、依赖未变时：只替换现有便携版中的三个 EXE
.\packaging\build-incremental.ps1
```

产物位于 `release/ResearchAssistant-0.7.2-windows-x64.zip`。解压后运行
`ResearchAssistant.exe`。GUI 关闭后 Core 继续在 Windows 通知区域运行；双击托盘图标
或右键选择“打开 Research Assistant”可重新打开 GUI。

便携版数据目录：

```text
%LOCALAPPDATA%\ResearchAssistant\data
```

环境变量 `RESEARCH_ASSISTANT_DATA_DIR` 可以覆盖源码版和便携版默认目录。EXE、`_internal`
目录及随附文件必须保持在同一解压目录中。详见
[V0.7.1 发布说明](docs/v0.7.1-release.md)和 [V0.7.2 更新说明](docs/v0.7.2-release.md)。

## Codex MCP

源码环境：

```powershell
codex mcp add research-assistant-evidence -- `
  "C:\path\to\research-assistant\.venv\Scripts\python.exe" `
  "C:\path\to\research-assistant\app\external_api\mcp_server.py"
```

便携版：

```powershell
codex mcp add research-assistant-evidence -- `
  "C:\path\to\ResearchAssistantMCP.exe"
```

MCP 暴露四个工具：`find_evidence`、`find_evidence_for_claim`、`get_source`、
`verify_citation`。MCP proxy 不加载 Evidence Engine 或 BGE；必须有可发现的 Core 正在运行。

## CLI

```powershell
# 增量索引整个个人文库或一个集合（同时增量更新语义索引；GUI 的增量更新/Full Rebuild 也一样）
.\.venv\Scripts\python.exe -m app.cli index
.\.venv\Scripts\python.exe -m app.cli index --collection Strain

# 明确重新解析整个个人文库
.\.venv\Scripts\python.exe -m app.cli rebuild

# 搜索和统计
.\.venv\Scripts\python.exe -m app.cli search "strain bandgap In2Se3"
.\.venv\Scripts\python.exe -m app.cli stats

# 仅维护语义索引（例如更换模型后 --rebuild）
.\.venv\Scripts\python.exe -m app.cli semantic-index
.\.venv\Scripts\python.exe -m app.cli semantic-index --rebuild
```

## 测试与验收

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.acceptance
.\.venv\Scripts\python.exe -m tests.desktop_acceptance
.\.venv\Scripts\python.exe -m app.external_api.mcp_diagnostics
```

V0.7.0 release acceptance：253 tests、真实 201-paper 本地库、外部 Codex MCP 工作流、
handle 生命周期、Core PID 稳定性及数据库/语义索引 SHA-256 不变性均已验证。

## 数据与安全边界

- Zotero API 仅允许 loopback GET，请求不使用环境代理或云端 fallback。
- PDF 只读；缺失、损坏、加密和无文本 PDF 会明确报告。
- `literature.db` 和 `data/semantic/` 是应用自有数据，不是 Zotero 数据库。
- `get_source` 只能使用 Core 返回的 report/evidence handle，不能接受任意文件路径。
- `verify_citation` 只检查一个已知 passage，不执行新的文献检索。
- 便携 ZIP 不包含用户数据库、PDF、模型缓存、凭据或 MCP 客户端配置。

架构详见 [docs/architecture.md](docs/architecture.md)，科学工具契约详见
[docs/v0.7-scientific-evidence-tools.md](docs/v0.7-scientific-evidence-tools.md)。

## 许可证

[MIT](LICENSE) © 2026 Rino Wu
