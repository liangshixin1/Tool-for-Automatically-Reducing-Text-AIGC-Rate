# CLAUDE.md — 项目上下文与协作规范

> 本文件供 AI 编程助手（Claude Code 等）在新会话中快速理解项目背景、当前状态和协作规范。

---

## 项目简介

**自动文本 AI 降率工具** — 本地 Web 应用，支持三道工作流：

- **一审**：上传 `.docx` → AI 率检测 → 逐段流式审校 → 人工批准/编辑/跳过 → 导出（审阅版/纯净版）
- **二审**：一审后继续，逐段语言/知识/逻辑深度校验，给出修改建议
- **零审**：上传多章节 `.docx` → 按 Heading 1 拆分章节 → 每章段落分配到五模块 → 逐章手动编辑 → 导出结构化 Word

---

## 文件结构

```
.
├── app.py                  # Flask 后端（所有路由、解析、SSE、导出逻辑）
├── templates/
│   └── index.html          # Vue 3 前端（CDN，单文件，含全部 JS）
├── requirements.txt        # flask, flask-cors, python-docx, requests
└── README.md
```

**无数据库，无构建工具**。所有状态保存在内存（请求生命周期）或前端 `localStorage`。

---

## 技术栈

| 层 | 技术 | 备注 |
|----|------|------|
| 后端 | Python + Flask | SSE 流式推送，`flask-cors` |
| 前端 | Vue 3 (CDN) + Tailwind CSS (CDN Play) | 无 node/npm，单 HTML 文件 |
| 文档处理 | python-docx | 含 XML 操作保留 CJK 字体 |
| AI | DeepSeek API `deepseek-chat` | 多个独立密钥 |
| 持久化 | 浏览器 `localStorage` | 进度 + API 密钥 + 口语库 |

---

## 后端核心结构（app.py）

### 全局常量
- `SYSTEM_PROMPT`：一审审校提示词，最高优先级是**零号规则**（条目结构彻底重写为散文）
- `DETECT_PROMPT`：AI 率检测提示词，返回 JSON `{rate, indicators, confidence}`，评分起点 10 分
- `ZERO_ASSIGN_PROMPT`：零审段落分配提示词，输出 `{"assignments": {"id": "模块名|丢弃", ...}}`
- `ZERO_RECOMMEND_PROMPT`：口语话题推荐提示词
- `ZERO_CLASSIFY_PROMPT`：旧版逐段分类提示词（已被 ZERO_ASSIGN_PROMPT 替代，保留兼容）

### 关键辅助函数
- `_fmt_from_para(para)`：从 docx 段落提取 8 个格式属性（字体、字号、间距、缩进、对齐）
- `_ea_font(run)`：通过 XML 读取东亚（CJK）字体名（`w:rFonts/w:eastAsia`）
- `parse_docx(file_obj)`：解析文档为平铺段落组列表，供一审/二审使用
- `parse_chapters(file_obj)`：**零审专用**，按 Heading 1 样式边界拆分章节，返回 `[{title, groups}]`
- `_style_depth(para)`：从 Word 段落样式读取标题深度（1/2/3），用于章节拆分
- `is_numbered_heading(text)`：匹配编号标题模式（`1.` / `1.1` / `1.2.3`）
- `detect_lang(text)`：CJK 字符占比 > 15% 则为中文

### API 路由

| 路由 | 说明 |
|------|------|
| `GET /` | `send_from_directory("templates", "index.html")`，**绕过 Jinja2** |
| `POST /upload` | 一审/二审：解析 docx，返回平铺 `{groups, total}` |
| `POST /review` | 一审：SSE 流式审校，请求头 `X-API-Key` |
| `POST /detect` | AI 率检测，`X-API-Key-Detect`，`response_format: json_object`，非流式 |
| `POST /export` | 一审/二审导出，`{results, mode}`，`mode: tracked\|clean` |
| `POST /proofread` | 二审：SSE 流式校验，`X-API-Key-Proofread` |
| `POST /zero/upload` | **零审上传**：`parse_chapters()` 拆分，返回 `{chapters, total}` |
| `POST /zero/organize` | **零审分配**：SSE + DeepSeek `stream:True`，输出 `{type:done, assignments:{}}` |
| `POST /zero/recommend-oral` | 口语话题推荐，`X-API-Key-Zero` |
| `POST /zero/export` | 零审导出，接受 `{chapters:[{title,modules}]}`，生成 H1/H2/H3 结构化 Word |
| `POST /zero/classify` | 旧版逐段分类（保留兼容，不再使用） |
| `GET /health` | `{"status": "ok"}` |

### 零审段落分配（`/zero/organize`）关键设计

使用 DeepSeek `"stream": True` + Python requests `stream=True`，通过 `resp.iter_lines()` 逐行消费 token：
- 每收到一个 delta → 累积到 `full` 字符串 → 向浏览器 yield `{"type":"ping"}`（SSE 心跳）
- 所有 token 到齐后 `json.loads(full)` → yield `{"type":"done","assignments":{...}}`
- 错误时 yield `{"type":"error","msg":"..."}`

**为什么不用 `stream:False`**：DeepSeek 生成 400+ 条 assignments 时，non-streaming 大响应容易被 socket 截断（"Response ended prematurely"）。

### 零审导出（`/zero/export`）Word 层级

```
H1（18pt 黑体）= 章节标题
  H2（14pt 黑体）= 模块名（理论精讲/谈判技巧/口语实例/核心练习/术语汇总&例句）
    H3（12pt 黑体）= 节标题（匹配 ^\d+(\.\d+)*\.?\s+\S 或 第X节/章）
    Normal（11pt 宋体）= 正文
```

接受单章 `{modules, chapterTitle}` 或多章 `{chapters:[{title,modules}]}` 两种格式。

---

## 前端核心结构（index.html）

### Vue 状态机

```
phase:
  'upload'
    ├─[一审上传]→ 'review' → 'paused' ↔ 'review'
    │                      ↘ 'complete' → [二审] → 's2review' → 's2complete'
    └─[零审上传]→ 'zero-classify'（SSE 分配中）
                    ↘ 'zero-assemble'（编辑五模块）
                        ↘ [保存&下一章] → 'zero-classify'（下一章）
                        ↘ [最后一章完成] → 'zero-complete'
```

### 零审关键状态变量

| 变量 | 说明 |
|------|------|
| `zeroChapters` | `[{title, groups}]` — `/zero/upload` 返回的所有章节 |
| `zeroChapterIdx` | 当前处理的章节下标 |
| `zeroAllAssembled` | `[{title, modules}]` — 已完成章节的结果，供导出使用 |
| `zeroGroups` | 当前章节的段落组列表 |
| `zeroChapterTitle` | 当前章节标题 |
| `zeroAssembled` | 当前章节五模块内容 `{模块名: 文本}` |
| `zeroOralInserts` | 当前章节口语插入记录 `{模块名: [{topic,sectionType,content}]}` |
| `zeroOrganizeErr` | SSE 分配错误信息（显示在 zero-classify 加载屏） |
| `oralLibrary` | 口语实例库数组（localStorage 持久化） |
| `zeroRecommended` | AI 推荐的口语话题列表 |

### 零审关键函数

| 函数 | 说明 |
|------|------|
| `doZeroUpload(file)` | POST `/zero/upload` → 存 `zeroChapters` → 调 `_startZeroChapter(0)` |
| `_startZeroChapter(idx)` | 加载第 idx 章的 groups/title → phase='zero-classify' → `startZeroOrganize()` |
| `startZeroOrganize()` | POST `/zero/organize`，SSE 解析，`ping` 忽略，`done` 调 `_assembleFromAssignments()` |
| `_assembleFromAssignments(assignments)` | 遍历 `zeroGroups`，按 assignment 将 `combined_text` 归入对应模块（`combined_text` 已含标题行，不重复追加 heading） |
| `saveChapterAndNext()` | 将当前 `zeroAssembled` 推入 `zeroAllAssembled` → 处理下一章或 phase='zero-complete' |
| `zeroExport()` | 合并 `zeroAllAssembled` + 当前章 → POST `/zero/export` → 浏览器下载 |
| `triggerOralRecommend()` | POST `/zero/recommend-oral`，填充 `zeroRecommended` |
| `insertOralContent()` / `insertAllOralTopic()` | 将口语素材追加到对应模块 textarea |

### 一审/二审关键变量（原有）

| 变量 | 说明 |
|------|------|
| `phase` | 当前页面阶段 |
| `groups` | 一审段落组（含 `fmt` 格式数据） |
| `currentIdx` | 当前处理的段落组下标 |
| `results` | 已决策的结果数组 `{id, original, modified, decision}` |
| `isAutoMode` | 全自动审阅模式，影响 detect/review 完成后的自动决策 |
| `sessionSavedAt` | 上次自动保存时间 |

---

## 已知设计决策（修改前需了解）

1. **Jinja2 冲突**：前端通过 `send_from_directory` 而非 `render_template` 提供，避免 Vue `{{}}` 被 Jinja2 解析。不要改回 `render_template`。

2. **格式保留（一审/二审）**：`/upload` 返回的每个段落对象含 `fmt` 字段，前端原样传回给 `/export`，是保留原文档格式的关键链路，不能简化。

3. **多个 API 密钥**：`X-API-Key`（审校）、`X-API-Key-Detect`（检测）、`X-API-Key-Proofread`（二审）、`X-API-Key-Zero`（零审）。检测/零审密钥留空时有相应降级行为。

4. **SSE 流式**：所有流式路由用 `Response(generate(), mimetype='text/event-stream')`；前端用 `fetch` + `ReadableStream` 手动解析 SSE（非 `EventSource`，因为是 POST 请求）。SSE 解析使用 `\n\n` 作为事件边界，`line.replace(/^data:\s*/,'')` 提取 payload。

5. **零审 `combined_text` 结构**：`parse_chapters` 中每个 group 的 `combined_text` 以标题行开头（如 `"1.1 询盘的重要性\n\n正文..."`）。`_assembleFromAssignments` 直接使用 `combined_text`，**不再额外 prepend `heading`**，否则标题重复。

6. **零审多章节导出**：`/zero/export` 同时支持 `{modules, chapterTitle}`（单章向后兼容）和 `{chapters:[{title,modules}]}`（多章）两种格式。

7. **temperature 递增（一审）**：每次重新生成 +0.15，上限 0.9，重置时机是 `startGroup()` 调用时。

8. **全自动模式安全机制**：所有 `setTimeout` 延迟回调在执行前重新检查 `isAutoMode.value`，不要改写为提前捕获变量的闭包。

9. **`response_format: json_object`**：仅 `/detect` 使用（非流式）。零审 `/zero/organize` 使用 `stream:True`，不设 `response_format`，依赖 prompt 约束输出 JSON，在服务端积累后 `json.loads`。

---

## 开发注意事项

- **不使用任何构建工具**，前端改动直接编辑 `templates/index.html`，刷新浏览器即可
- **不引入数据库**，所有会话数据在内存或 `localStorage`
- **不改动 DeepSeek 模型名**（`deepseek-chat`）除非用户明确要求
- API 密钥**永远不要**硬编码，必须从请求头读取
- 修改 `SYSTEM_PROMPT` 时，**零号规则必须保持最高优先级**
- 前端新增状态变量后，**必须在 `setup()` 末尾的 `return {}` 中导出**，否则模板无法访问
- 零审导出时，节标题识别正则为 `^\d+(\.\d+)*\.?\s+\S`，覆盖 `1.`、`1.1`、`1.1.1` 及带空格的英文节名（如 `5.   Packing list`）

---

## 用户偏好与历史反馈

- 界面风格：深色学术工具风，参考 Notion/Linear 克制美学，**无渐变色**
- 代码风格：精简，不过度抽象，不加不必要的注释
- 改动原则：只做被要求的改动，不主动重构周边代码
