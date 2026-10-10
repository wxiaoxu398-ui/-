# MedRAG

面向医学生的本地医学教材问答助手。左侧管理文档和会话，中间提问，右侧核对引用。后端绑定 `127.0.0.1`，不部署公网，不含登录或患者资料功能。

## 启动

Windows 双击根目录的 **start-medrag.cmd**。首次运行自动安装依赖、构建页面，然后打开 `http://127.0.0.1:8000/`。再次启动复用已安装依赖。双击 **stop-medrag.cmd** 关闭启动器创建的服务。

双击 **download-demo-books.cmd** 可从 NCBI 官方开放归档下载三本 Open RN 教材，并带上作者、来源、许可证。随后在网页点击“建立文档索引”。当前工作区已下载这三本教材；在其他电脑首次下载约 715 MiB，需保留原书中单独标注的许可例外。书籍清单、PDF 指纹与许可链接保存在 `eval/open-books.json`。

当前还加入四本中文学习资料：《实验室生物安全手册（第四版）》《风险评估》《个人防护装备》《压力之下，择要事为之：图解指南》，共 439 个 PDF 物理页。前三本是 WHO 专业学习手册，第四本是西安交通大学医学部护理学系翻译的心理健康教育指南；它们不等同于国内医学本科统编教材。逐本许可、官方链接与文件指纹见 `eval/chinese-books.json`。在其他电脑双击 **download-chinese-books.cmd** 可下载、启动服务并增量入库。

首次使用需要联网，电脑需要 Python 3.11–3.13 与 Node.js 20.19+。在当前 Codex 工作区，启动器也支持使用已安装的 bundled Python。首次 BGE 入库会下载模型，之后使用本地缓存。服务日志位于 `.local/server.log` 和 `.local/server-error.log`。

如果 8000 端口被其他程序占用，启动器会报告失败；请关闭占用程序后重试。

## 使用

1. 点击文档库的“＋”导入文字版 PDF，或直接把 PDF 放到 `backend/app/data/books/`。网页上传单个文件最多 200 MiB；更大的 PDF 可直接放入目录。扫描件需先 OCR。
2. 点击书名填写作者、资料来源、许可证与来源链接。也可在 PDF 旁放同名 JSON，例如 `book.json`，字段为 `title`、`author`、`source`、`license`、`source_url`。
3. 点击“建立文档索引”。系统逐页解析 PDF，以 480 字为目标、50 字重叠切块，保留物理页码，批量写入 SQLite 与 Chroma。
4. 点击聊天页面右上角“配置 API Key”（已配置时为“API 设置”），填写 DeepSeek API Key 并点击“保存配置”，无需重启或重建索引。输入框下方的“配置 DeepSeek”也可打开配置窗口。密钥仅保存到 `backend/.env`，不会返回给前端或提交到 Git。调用时，问题、最近三轮上下文及检索到的教材片段发送给 DeepSeek。
5. 提问后点击答案中的编号，在右侧展开原文，或“打开原文”查看指定物理页的原页预览；预览中可翻页、打开完整 PDF。勾选文档右侧方框可限定检索范围，“全部教材”恢复全库。

默认生成模型为 `deepseek-flash`，显式关闭思考模式，默认最多生成 600 tokens。旧 `.env` 中的 `deepseek-chat` / `deepseek-reasoner` 自动转换为当前模型；这些旧 API 名称已由 DeepSeek 于 2026-07-24 停用。[官方变更说明](https://api-docs.deepseek.com/news/news260424/)

**未配置密钥时使用“原文摘录模式”。** 此时仅展示检索原句和页码，不生成解释，不能作为 DeepSeek 回答质量的验收结果。

默认使用 `BAAI/bge-small-zh-v1.5` + BM25 + RRF。设置中可切换为明确标注的 BM25 轻量模式。切换检索方式必须重新入库，旧方式的索引不参与检索。为减少弱语义误命中，向量候选还需与查询共享至少一个有效词；这会保守地拒绝部分同义或跨语言问题。当前模型主要面向中文教材。

Windows 中项目路径包含中文时，原生 HNSW 文件自动保存到 `%LOCALAPPDATA%/MedRAG/vector-cache/<路径指纹>/`，避免中文路径下索引无法落盘。SQLite、书籍与会话仍在项目目录。启动时检查向量完整性并预热检索缓存；索引丢失或迁移后，文档改为待入库并提示重建。不要在后台运行额外进程修改同一个向量目录。

CPU 嵌入默认使用 4 个计算线程，避免入库时占满处理器。原有三本 Open RN 教材为英文；提问时保留英文术语有助于命中，例如“药代动力学 pharmacokinetics 包含哪些阶段？”。新增中文手册可直接中文提问；资料就绪后首页显示其覆盖的中文示例。纯中文跨语言问题仍可能保守拒答。

## 已实现的范围

- P0：目录扫描、文档信息/状态、增量与全量入库、SSE 问答、来源展开、PDF 原页、资料不足拒答、固定免责声明、SQLite 会话历史/切换/删除。
- P1：最近三轮上下文、追问检索、反馈持久化、停止生成及保存部分回答、Markdown 表格/列表/加粗、代码高亮。
- P2：指定文档筛选、20 题评估页、逐题回答/来源/耗时、人工标注引用准确性、结果 JSON 导出。
- 辅助功能：网页上传、来源许可证编辑、密钥设置、移动端布局、错误提示、索引进度、原文件变化检测。

可选的“纯 LLM / RAG 并排对比”尚未实现。当前应用始终围绕有资料依据的问答。

## 引用与拒答的边界

系统提示限制模型使用本轮教材证据；检索阶段筛除低相关结果；生成阶段按段落缓冲，并检查有效编号和逐句引用后才流式展示。编号错误、无引用结论、API 失败或超时会中断输出，已输出内容保留并标记未完成。

**这些检查不能证明每条语义结论都被引用支持。** 引用准确性需回到原文人工核对。模型仍可能给有效编号配上错误解读；不应把结构检查当成“零幻觉”保证。表格建议每个数据行带引用；未通过逐句规则的复杂格式会被拦截。

引用页码是 PDF 从 1 开始的物理页码，可能不同于印刷页码。每条来源保存文件 SHA-256 与原文快照；原 PDF 被移走或替换时，历史片段仍可查看，但不会打开错误版本的原页。

只导入有合法使用权限的资料。OpenStax、NCBI Bookshelf、WHO、PMC Open Access、维基教科书中各项资料的许可需逐项核实，系统不自动声称其许可已获验证。界面显示用户填写的来源与许可证，未填写时明确提示待补充。

## 评估与当前验证状态

`eval/questions.json` 含 17 条教材问题与 3 条教材外问题。这是通用模板，未绑定实际教材和页码。需要先导入覆盖这些题目的资料，再运行评估，逐题打开原页标注“引用准确/存在错误”。只有人工标注的结果计入引用准确率；无资料拒答不等于教材内问答通过。

当前工作区已验证：

- 20 项后端自动化测试，包括三本独立测试 PDF、一本 500 页、10 个预设内容/页码、3 个教材外拒答、追问、持久化、反馈、删除、网页上传、非法 PDF、扫描件、原文件移走后缓存失效、生成引用校验、中断、token 用量记录、旧数据库迁移、指定物理页预览，以及定义题不被大量术语重复干扰的回归检查。
- 真实 BGE 模型加载及 Chroma 入库 smoke test；测试材料为软件夹具，不是正式医学教材。
- Vue TypeScript 与生产构建；浏览器中的桌面/移动布局、空库拒答与刷新后会话保留。
- 三本完整 Open RN 英文教材：965、1,499、1,432 页，共 12,978 个片段。10 个英文问题的预期页召回率为 100%；本次检索排序调整后混合检索首条预期页命中 90%，BM25 原基准为 90%。60 条混合检索来源逐条核对文件指纹、物理页与原文映射。3 个教材外问题及追问检索通过。原基准记录见 `docs/04-验收记录.md`，最新摘要见 `eval/retrieval-benchmark.json`。
- 新增四本中文学习资料共 439 页、733 个片段；8 个中文问题在指定书内的预期页命中 8/8，全库检索为 7/8。48 条来源的原文与物理页映射核对通过，正式索引在独立进程重新打开及逐书向量查询通过。详细记录见 `docs/05-中文资料入库记录.md`。

**当前仍未配置 DeepSeek 密钥。** 真实资料的检索与页码映射已经验证；尚未验证生成答案引用准确率 ≥80%、多轮生成质量、首段 <3 秒、完整回答 <30 秒、每次 <0.01 元。此次基准使用英文问题，不能证明纯中文跨语言检索质量。

API 返回的 token 用量随会话保存、随评估导出。费用栏标明“高峰单价估算”，按 2026-10-11 [官方人民币价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)快照计算，未知模型或未返回用量时不估算；它不能代替实际账单。停止或失败的流可能不返回最终用量，此时费用保持未知。

## 开发与测试

```powershell
# 从项目根目录
.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.venv\Scripts\python.exe -m pytest backend/tests -q
.venv\Scripts\python.exe scripts/verify_bge.py
.venv\Scripts\python.exe scripts/download_open_books.py
.venv\Scripts\python.exe scripts/download_chinese_books.py --output backend/app/data/books
.venv\Scripts\python.exe scripts/verify_chinese_books.py
.venv\Scripts\python.exe scripts/verify_real_corpus.py
.venv\Scripts\python.exe scripts/verify_real_corpus.py --backend lexical
.venv\Scripts\python.exe scripts/verify_persistence.py
# 在网页设置中填写密钥后，运行实际生成验收；答案仍需逐条人工标注
.venv\Scripts\python.exe scripts/verify_real_corpus.py --live

# 开发后端
cd backend
..\.venv\Scripts\python.exe run.py

# 另一个终端运行前端（代理 /api 到 8000）
cd frontend
npm ci
npm run dev
```

自动化测试在系统临时目录使用单独数据库与 PDF，不会污染正式文档库、会话或密钥。`verify_bge.py` 会使用本地 BGE 缓存，首次可能下载模型。

真实教材验收使用 `.local/acceptance/` 的单独数据库、文档副本和向量缓存。`open-book-questions.json` 固定独立核对的原页，不从检索结果自动生成答案标准。运行报告包含所有来源、耗时及未标注的回答；完整报告不提交 Git，仅提交不含全文片段的基准摘要。

API OpenAPI 文档：`http://127.0.0.1:8000/docs`。原始接口与技术输入保存在 `docs/`。所有扩展接口使用 `/api` 前缀；统一错误对象为 `{ "error": { "code": "…", "message": "…" } }`。

## 技术栈

Vue 3、Vite、TypeScript、Pinia、Naive UI、markdown-it、highlight.js；FastAPI、Pydantic、SQLModel、SQLite、PyMuPDF、Chroma、Sentence Transformers、jieba、rank-bm25、OpenAI-compatible DeepSeek SDK、sse-starlette。无 LangChain / LlamaIndex。

系统不提供诊断、处方、治疗建议或临床决策支持。
