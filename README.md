# MedRAG

面向医学生的本地医学教材问答助手。左侧管理文档和会话，中间提问，右侧核对引用。后端绑定 `127.0.0.1`，不部署公网，不含登录或患者资料功能。

## 启动

Windows 双击根目录的 **start-medrag.cmd**。首次运行自动安装依赖、构建页面，然后打开 `http://127.0.0.1:8000/`。再次启动复用已安装依赖。双击 **stop-medrag.cmd** 关闭启动器创建的服务。

首次使用需要联网，电脑需要 Python 3.11–3.13 与 Node.js 20.19+。在当前 Codex 工作区，启动器也支持使用已安装的 bundled Python。首次 BGE 入库会下载模型，之后使用本地缓存。服务日志位于 `.local/server.log` 和 `.local/server-error.log`。

如果 8000 端口被其他程序占用，启动器会报告失败；请关闭占用程序后重试。

## 使用

1. 点击文档库的“＋”导入文字版 PDF，或直接把 PDF 放到 `backend/app/data/books/`。单个文件支持到 200 MB；扫描件需先 OCR。
2. 点击书名填写作者、资料来源、许可证与来源链接。也可在 PDF 旁放同名 JSON，例如 `book.json`，字段为 `title`、`author`、`source`、`license`、`source_url`。
3. 点击“建立文档索引”。系统逐页解析 PDF，以 480 字为目标、50 字重叠切块，保留物理页码，批量写入 SQLite 与 Chroma。
4. 在“设置”中填写 DeepSeek API 密钥。密钥仅保存到 `backend/.env`，不会返回给前端或提交到 Git。调用时，问题、最近三轮上下文及检索到的教材片段发送给 DeepSeek。
5. 提问后点击答案中的编号，在右侧展开原文，或“打开原文”定位 PDF 页码。勾选文档右侧方框可限定检索范围，“全部教材”恢复全库。

**未配置密钥时使用“原文摘录模式”。** 此时仅展示检索原句和页码，不生成解释，不能作为 DeepSeek 回答质量的验收结果。

默认使用 `BAAI/bge-small-zh-v1.5` + BM25 + RRF。设置中可切换为明确标注的 BM25 轻量模式。切换检索方式必须重新入库，旧方式的索引不参与检索。为减少弱语义误命中，向量候选还需与查询共享至少一个有效词；这会保守地拒绝部分同义或跨语言问题。当前模型主要面向中文教材。

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

- 15 项后端自动化测试，包括三本独立测试 PDF、一本 500 页、10 个预设内容/页码、3 个教材外拒答、追问、持久化、反馈、删除、网页上传、非法 PDF、扫描件、原文件移走后缓存失效、生成引用校验与中断。
- 真实 BGE 模型加载及 Chroma 入库 smoke test；测试材料为软件夹具，不是正式医学教材。
- Vue TypeScript 与生产构建；浏览器中的桌面/移动布局、空库拒答与刷新后会话保留。

**当前未导入用户的真实教材，也未配置 DeepSeek 密钥。** 尚未验证真实教材引用正确率 ≥80%、多轮生成质量、首段 <3 秒、完整回答 <30 秒、每次 <0.01 元。API 费用依赖实际模型定价和 token 使用，应用不显示估算值作为已测成本。

## 开发与测试

```powershell
# 从项目根目录
.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.venv\Scripts\python.exe -m pytest backend/tests -q
.venv\Scripts\python.exe scripts/verify_bge.py

# 开发后端
cd backend
..\.venv\Scripts\python.exe run.py

# 另一个终端运行前端（代理 /api 到 8000）
cd frontend
npm ci
npm run dev
```

自动化测试在系统临时目录使用单独数据库与 PDF，不会污染正式文档库、会话或密钥。`verify_bge.py` 会使用本地 BGE 缓存，首次可能下载模型。

API OpenAPI 文档：`http://127.0.0.1:8000/docs`。原始接口与技术输入保存在 `docs/`。所有扩展接口使用 `/api` 前缀；统一错误对象为 `{ "error": { "code": "…", "message": "…" } }`。

## 技术栈

Vue 3、Vite、TypeScript、Pinia、Naive UI、markdown-it、highlight.js；FastAPI、Pydantic、SQLModel、SQLite、PyMuPDF、Chroma、Sentence Transformers、jieba、rank-bm25、OpenAI-compatible DeepSeek SDK、sse-starlette。无 LangChain / LlamaIndex。

系统不提供诊断、处方、治疗建议或临床决策支持。
