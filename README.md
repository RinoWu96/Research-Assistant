# Research Assistant V0.1 + V0.2 semantic proof of concept

Windows 本地 Zotero 文献检索工具：输入关键词，找到论文、原文和 PDF 页码。
已实现 Zotero 只读连接、PDF 逐页提取、SQLite FTS5 索引及 PySide6 桌面界面。
V0.1 桌面功能保持不变。V0.2c 已加入持久本地语义索引、增量 embedding、语义检索、
RRF 混合检索、过滤、结果多样化和论文级聚合，并已集成 Keyword / Semantic / Hybrid
桌面搜索模式。默认使用 Hybrid；仍不使用 LLM、MCP 或云 API。

V0.3b 提供完整 Evidence Pack 工作流：从 Hybrid 候选池构建按论文组织的中性
Candidate Evidence，保留 PDF 原文、物理页码和完整来源信息。桌面界面支持论文选择、
打开 PDF、复制 Evidence Pack、Copy for AI，以及 Markdown/JSON 导出。

V0.2 证明命令：

```powershell
.\.venv\Scripts\python.exe -m app.search.semantic_poc "curvature induced strain" --papers 10
```

它只读 SQLite 中 5–10 篇已索引论文，按页创建约 350 词、75 词重叠的确定性 chunks，
在 CPU 上加载 `BAAI/bge-small-en-v1.5`，建立临时 FAISS 索引并打印前 5 条原文。
首次运行会从 Hugging Face 下载免费模型；推理和后续缓存运行均在本地。临时索引退出后删除，
不修改 SQLite、Zotero 或 GUI。详见 [V0.2 集成计划](docs/v0.2-semantic-plan.md)。

持久语义后端：

```powershell
# 增量更新；不重复计算未变化论文的 embedding
.\.venv\Scripts\python.exe -m app.cli semantic-index

# 仅在模型/维度/chunker 设置不兼容时明确执行
.\.venv\Scripts\python.exe -m app.cli semantic-index --rebuild

# 比较四个固定查询的 Keyword / Semantic / Hybrid / Paper 结果
.\.venv\Scripts\python.exe -m app.search.evaluate_retrieval

# 可选集合过滤（集合名重复时使用 Zotero key）
.\.venv\Scripts\python.exe -m app.search.evaluate_retrieval --collection 'for In2Se3'
```

生产文件位于 `data/semantic/`，被 git 忽略。`chunks.sqlite` 只缓存坐标和 embedding；
原文、页码和文献元数据始终从 `literature.db` 读取。混合模式在语义索引不可用时自动回退
到关键词检索。桌面界面通过统一搜索服务检索。Windows 上语义模型运行在独立的本地
辅助进程中，避免 PyTorch 初始化阻塞 Qt；请求和结果使用临时 JSON 文件传递，通信与推理均在本机完成。

## 快速启动

此工作区的虚拟环境、依赖和 `Strain` 集合索引已准备好。
**双击 `start.cmd`**，或在 VS Code PowerShell 终端运行：

```powershell
cd 'C:\path\to\research-assistant'
.\.venv\Scripts\python.exe main.py
```

输入 **`strain bandgap In2Se3`**，点击 **搜索原文**。
选择结果查看完整页原文；点击 **查看 PDF 原页** 可准确打开该物理页的只读预览。
**打开文件** 使用系统默认阅读器，它不保证自动跳到指定页。

已索引：`Strain` 集合的 **26 篇文献、26 份 PDF、237 页**。
查询返回 **2 篇论文、8 个匹配页**。第一条结果为
*Giant Bandgap Engineering in Two-Dimensional Ferroelectric α-In₂Se₃*，
**PDF 第 5 页**，DOI `10.1021/acs.jpclett.2c00618`。
未预先索引整个 Zotero 文库；其他集合可在界面中选择后建立索引。

## 环境与安装

- Windows、VS Code、Python 3.10+。
- Zotero 支持 Local API，建立/更新索引时需运行。
- httpx、PyMuPDF、PySide6；SQLite 使用 Python 标准库，运行时验证 FTS5。

首次安装（已有 `.venv` 时跳过创建）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

使用虚拟环境中的可执行文件无需激活 PowerShell 环境。
`requirements-lock.txt` 记录本机验证过的完整依赖版本，可用于复现安装。

## Zotero 设置和只读约束

Zotero Settings → Advanced，勾选
**Allow other applications on this computer to communicate with Zotero**。
Local API 地址为 `http://localhost:23119/api/`。桌面和索引命令默认使用同一服务的
`http://127.0.0.1:23119/api/`，避免本机观察到的 localhost IPv6 回退延迟。
连接测试仍默认 localhost，可通过 `--base-url` 切换。
[官方 Local API 文档](https://www.zotero.org/support/dev/web_api/v3/local_api)。

仅发送 GET 请求；禁止自动重定向、环境代理和非本机 API 地址。
**从不读取或写入 `zotero.sqlite`，不修改、删除、移动或重命名 Zotero 条目/PDF，
不请求写入权限，也不改变存储配置。** 附件路径由 API 获取，未硬编码 Zotero 数据目录。
仅应用自己的 `data/literature.db` 和日志可写。PDF 文件只读打开。

## 建立索引与搜索

1. 刷新 Zotero，选择集合，点击 **增量更新索引**。
2. 单个集合仅包含直接成员，不递归包含子集合。选择「全部」并建立索引会处理整个个人文库。
3. 索引显示进度并支持取消；未变化的 PDF 跳过，单文件失败继续处理其他文件。
4. 输入关键词，可加集合、年份或期刊全名过滤。
5. 点击结果查看完整页，复制原文/引用，或查看 PDF 原页。

搜索要求所有关键词出现在**同一 PDF 页**，按 BM25 排序，最多每批 100 条并支持加载更多。
支持英文词干变化、上下标兼容字符、`In 2 Se 3` 等化学式空格及 `band gap`/`bandgap`。
这是规则驱动的关键词匹配，不会自动扩展概念或放宽为任一词匹配。
原始提取文本独立保存，检索规范化不改变原文。

页码是从 1 开始的 **PDF 物理页码**，可能不同于期刊印刷页码。
查询使用本地索引，Zotero 关闭后仍可搜索。PDF 内容在索引后改变时，原页预览会提示更新索引。
更新会比较元数据、集合成员关系、附件路径和标题，并以 SHA-256 校验 PDF 内容。
元数据变更只更新元数据，不重复解析未变化的 PDF；内容、解析器或规范化版本变化才重建对应附件的页索引。
大小和时间均未变化的 PDF 内容变更也能被发现。校验需要读取文件，但不修改文件。

进度与最终统计按**条目**计数：indexed（新增）、updated（变更）、skipped（未变更）、failed（失败）。
每个处理完的条目仅计入一类；一个附件失败会将所属条目标为 failed，即使其他附件成功。
PDFs parsed 单独报告实际解析的文件数量，统计在搜索后仍保留。

**Full Rebuild** 是单独的可选操作：重新解析**整个个人文库**，不受集合选择限制。
它逐项替换应用索引、保留稳定标识，不删除数据库文件，不改变 Zotero。
正常增量更新不会隐式调用它。CLI 对应 `python -m app.cli rebuild`；旧 `index --force`
仍支持明确强制刷新指定集合。

Schema v1 会原地升级到 v2，保留现有页和 FTS 索引。首次更新补录缺失的 tags/item type
元数据及内容哈希；已有 PDF 的大小/时间和解析器指纹一致时直接建立哈希基线，不重复解析。
首次建立基线之前、同时保留旧大小/时间的文件变更无法追溯；基线建立后可检测此类变更。

## 命令行与测试

```powershell
# 连接、集合选择、文献和附件列表
.\.venv\Scripts\python.exe -m tests.test_zotero_connection --collection Strain --base-url http://127.0.0.1:23119/api/

# 真实 PDF，打印前两页并校验文件未改变
.\.venv\Scripts\python.exe -m tests.test_pdf_parser --collection Strain --base-url http://127.0.0.1:23119/api/

# 增量索引一个集合；省略 --collection 会索引整个个人文库
.\.venv\Scripts\python.exe -m app.cli index --collection Strain

# 仅在明确需要全量重新解析时执行（整个个人文库）
.\.venv\Scripts\python.exe -m app.cli rebuild

# 关键词搜索；不需要 Zotero 运行
.\.venv\Scripts\python.exe -m app.cli search 'strain bandgap In2Se3'
.\.venv\Scripts\python.exe -m app.cli stats

# 离线自动测试；Qt 界面测试使用隔离临时数据
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 真实语料验收：重复搜索并重新核对每个 PDF 页
.\.venv\Scripts\python.exe -m tests.acceptance

# Windows 原生界面验收，会短暂打开窗口并保存截图
.\.venv\Scripts\python.exe -m tests.desktop_acceptance
```

`app.cli` 支持 `--database PATH`（写在子命令之前）；`main.py` 也支持该选项。
索引命令存在单文件失败时返回退出码 2，致命错误为 1；其余成功为 0。

## 架构

```text
app/
  config.py             配置与应用数据位置
  zotero/               GET-only Local API、文献/附件模型
  parser/               PyMuPDF 逐页解析、只读原页渲染
  search/
    database.py         独立 SQLite schema、外键、事务和 FTS5
    indexer.py          增量索引、进度、取消、失败隔离
    search_service.py   同页关键词查询、排序和结果
    text.py             文本规范化和原文片段定位
  ui/                   PySide6 窗口、单个后台工作线程
  external_api/         V0.4-A：只读 MCP 服务，将 Evidence Engine 暴露为 find_evidence
  evidence/             Candidate Evidence 聚合、去重及 Markdown/JSON 导出
  cli.py                无 GUI 的后端命令入口
data/literature.db      应用自有索引，不是 Zotero 数据库
tests/                  自动测试和显式真实数据验收
main.py                 桌面启动与依赖组装
```

详见 [架构说明](docs/architecture.md) 和 [验收记录](docs/verification.md)。
日志位于 `data/research-assistant.log`，本地轮转，未上传。

## V0.4-A：MCP 外部工具（find_evidence）

在不改动检索、分类、引用溯源或排序逻辑的前提下，将已验证的 V0.3.2 Evidence Engine
暴露为一个只读 MCP 工具 `find_evidence`，供 Claude Code / Codex 等外部 agent 调用：

```powershell
.\.venv\Scripts\python.exe -m app.external_api.mcp_server
```

详见 [V0.4-A 说明](docs/v0.4-mcp.md)（架构、精确 schema、错误处理、Claude Code 连接配置）。

## 限制及错误处理

- 仅个人文库；不支持群组文库、OCR、网络 file URL 或自动下载缺失附件。
- 扫描 PDF 无文本时标为 `TEXT_EXTRACTION_FAILED`；加密、损坏、缺失文件分别报告。
- PDF 双栏布局、复杂公式和字体编码可能影响提取顺序/字符；可查看原 PDF 页核对。
- 参考文献页也属于关键词匹配范围；结果表示词汇共现，不保证论文支持某个科学结论。
- 旧 Zotero API 不提供 server ID 时，无法自动区分用户切换文库；切换后须使用新的应用数据库。
- 首次索引大量文献可能较慢；索引期间窗口保持响应，但搜索/渲染等待当前任务结束或取消。
- Zotero 断开、空集合和本地数据库不可用均显示错误或状态。完整读取失败不会清空缓存。
- V0.1 从 Python 环境启动，尚未打包成独立安装程序。

## 未来功能（未实现）

LLM、MCP server、REST API、Codex、Claude、ChatGPT 集成及稿件论断核查。后续适配器可
调用统一后端搜索服务，不依赖桌面界面。
