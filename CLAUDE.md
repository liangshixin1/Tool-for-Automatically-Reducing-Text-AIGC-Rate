# CLAUDE.md — 项目上下文与协作规范

> 本文件供 AI 编程助手（Claude Code 等）在新会话中快速理解项目背景、当前状态和协作规范。

---

## 项目简介

**自动文本 AI 降率工具** — 本地 Web 应用，用于教材 AIGC 痕迹消除的人工审批工作流。

- 用户上传中英双语教材 `.docx`
- 系统先检测每段的 AI 生成率（0–100%），再调用 DeepSeek 审校
- 用户逐段审批（批准/重新生成/手动编辑/跳过），支持随时暂停并恢复
- 支持**全自动审阅**（实验性）：AI 率 > 60% 自动审校并批准，≤ 60% 自动跳过
- 最终导出两种格式：**审阅版**（原文删除线 + 青绿色斜体对照）或**纯净版**（仅最终内容，无标记）

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

**无数据库，无构建工具**。所有状态保存在内存（请求生命周期）或前端 `localStorage`（进度持久化）。

---

## 技术栈

| 层 | 技术 | 备注 |
|----|------|------|
| 后端 | Python + Flask | SSE 流式推送，`flask-cors` |
| 前端 | Vue 3 (CDN) + Tailwind CSS (CDN Play) | 无 node/npm，单 HTML 文件 |
| 文档处理 | python-docx | 含 XML 操作保留 CJK 字体 |
| AI | DeepSeek API `deepseek-chat` | 审校 + 检测两个独立密钥 |
| 持久化 | 浏览器 `localStorage` | 进度 + 两个 API 密钥 |

---

## 后端核心结构（app.py）

### 全局常量
- `DEEPSEEK_API_URL`：`https://api.deepseek.com/v1/chat/completions`
- `SYSTEM_PROMPT`：审校系统提示词（~120 行），最高优先级是**零号规则**（条目结构彻底重写为散文）
- `DETECT_PROMPT`：AI 率检测提示词（~80 行），返回 JSON `{rate, indicators, confidence}`

### 关键辅助函数
- `_fmt_from_para(para)`：从 docx 段落提取 8 个格式属性（字体、字号、间距、缩进、对齐），存入 `fmt` 字典
- `_ea_font(run)`：通过 XML 读取东亚（CJK）字体名（`w:rFonts/w:eastAsia`）
- `_apply_pf(pfmt, fmt)` / `_set_font(run, fmt)` / `_add_para(...)`：导出时重建格式化段落的三个辅助函数
- `parse_docx(file_obj)`：解析文档，按编号标题（`^\d+(\.\d+)*`）或 Heading 样式分组，每段含 `{text, lang, style, fmt}` 字段
- `detect_lang(text)`：CJK 字符占比 > 15% 则为中文

### API 路由
| 路由 | 说明 |
|------|------|
| `GET /` | `send_from_directory("templates", "index.html")`，**绑过 Jinja2**（Vue 的 `{{}}` 与 Jinja2 冲突） |
| `POST /upload` | 解析 docx，返回 `{groups, total}`，每段含 `fmt` 格式数据 |
| `POST /review` | SSE 流式调用 DeepSeek，请求头 `X-API-Key`，body `{text, temperature}` |
| `POST /detect` | 非流式调用 DeepSeek，请求头 `X-API-Key-Detect`，`response_format: {type: json_object}`，返回 `{rate, indicators, confidence}` |
| `POST /export` | 接收 `{results: [...], mode}`，`mode` 为 `"tracked"`（审阅版，默认）或 `"clean"`（纯净版），生成并返回 `.docx` |
| `GET /health` | `{"status": "ok"}` |

### 导出格式（`mode` 参数）
- **`"tracked"`（审阅版，默认）**：approved/edited 段同时输出原文（删除线）+ 修改版（青绿色 `RGB 0,134,134` 斜体）；skipped/未处理原样复现
- **`"clean"`（纯净版）**：approved/edited 仅输出修改后文本，按段落位置索引映射回原始段落样式；skipped/未处理原样输出；无任何标记色或删除线
- 两种格式均保留：段落样式名、字体（ASCII + CJK 双通道）、字号、粗斜体、间距、缩进、对齐
- 组间一个空行隔开

---

## 前端核心结构（index.html）

### Vue 状态机

```
phase: 'upload' → 'review' → 'paused' → 'review'（继续）
                           ↘ 'complete'（全部处理完）
```

### 关键状态变量
| 变量 | 说明 |
|------|------|
| `phase` | 当前页面阶段 |
| `groups` | 所有段落组（含 `fmt` 格式数据） |
| `currentIdx` | 当前处理的段落组下标 |
| `detectPhase` | `idle \| detecting \| detected` |
| `aiRate` / `animatedRate` | 检测 AI 率 / 圆环动画值 |
| `reviewPhase` | `idle \| streaming \| done \| editing` |
| `results` | 已决策的结果数组，每条 `{id, original, modified, decision}` |
| `history` | 历史记录（含 `aiRate`、`retryCount` 用于右侧面板显示） |
| `sessionSavedAt` | 上次自动保存的时间字符串，显示在导航栏 |
| `isAutoMode` | 是否处于全自动审阅模式（`boolean`），影响 detect/review 完成后的自动决策行为 |
| `showAutoWarning` | 是否显示全自动模式确认弹窗 |

### 会话持久化流程
1. 每次 `commitResult()`（做出决策后）→ `saveSession()` 写入 `localStorage`
2. 每 60 秒后台静默保存一次（`setInterval`）
3. `beforeunload` 事件触发保存
4. 页面加载时 `loadSession()` 检查是否有已保存进度，有则显示蓝色恢复横幅

### 流式中断（暂停）
`startReview()` 使用 `AbortController`。暂停时调用 `reviewAbort.abort()`，fetch 抛出 `AbortError`，被静默捕获。

### 关键函数
| 函数 | 说明 |
|------|------|
| `startGroup()` | 进入新段落组：重置状态，按有无检测密钥决定先 detect 还是直接 review |
| `startDetect()` | POST `/detect`，rate > 60 自动调 `startReview()` |
| `startReview()` | POST `/review`，读取 SSE 流，用 `AbortController` 支持中断 |
| `commitResult(decision, modified)` | 记录决策，自动保存，推进到下一组或进入 complete |
| `pauseReview()` | 中断流式 → 重置 `isAutoMode` → `saveSession()` → `phase='paused'` |
| `resumeReview()` | `phase='review'` → `startGroup()` |
| `confirmAutoMode()` | 确认启动全自动审阅：根据当前状态直接 approve/skip/继续，后续组全自动处理 |
| `stopAutoMode()` | 将 `isAutoMode` 置 false，回到手动模式（不暂停，继续当前组） |
| `exportPartial(mode)` | 已处理结果 + 剩余组标记为 skipped → 调 `/export`，`mode` 决定审阅版或纯净版 |
| `exportDoc(payload, filename, mode)` | 通用导出，`mode` 默认 `'tracked'`，触发浏览器下载 |

---

## 已知设计决策（修改前需了解）

1. **Jinja2 冲突**：前端通过 `send_from_directory` 而非 `render_template` 提供，避免 Vue `{{}}` 被 Jinja2 解析。**不要改回 `render_template`**，除非在整个 HTML 外层加 `{% raw %}{% endraw %}`。

2. **格式保留**：`/upload` 返回的每个段落对象含 `fmt` 字段，前端原样传回给 `/export`。这是保留原文档格式的关键链路，**不能简化 `fmt` 字段或用纯文本替代**。

3. **两个 API 密钥**：审校（`X-API-Key`）和检测（`X-API-Key-Detect`）分开。检测密钥留空时跳过检测直接审校，这是合法的降级行为。

4. **SSE 流式**：`/review` 路由用 Flask `Response(generate(), mimetype='text/event-stream')`；前端用 `fetch` + `ReadableStream`，而非 `EventSource`（因为是 POST 请求）。

5. **temperature 递增**：每次重新生成 +0.15，上限 0.9。在 `regenerate()` 函数中实现，重置时机是 `startGroup()` 调用时。

6. **检测阈值**：AI 率 > 60 才自动触发审校。`DETECT_PROMPT` 的评分起点是 10 分（非 0），以"宁可高判"为原则。

7. **`response_format: {type: json_object}`**：`/detect` 路由在调用 DeepSeek 时设置了此参数，确保返回合法 JSON。如果切换到不支持该参数的模型，需要同步移除或用 prompt 约束替代。

8. **全自动模式安全机制**：所有 `setTimeout` 延迟回调（detect 后跳过、review 后批准）都会在执行前重新检查 `isAutoMode.value`。这样在延迟期间调用 `stopAutoMode()` 或 `pauseReview()` 后，回调会静默跳过而不执行自动决策。**不要将这些检查改写为提前捕获变量的闭包**。

---

## 开发注意事项

- **不使用任何构建工具**，前端改动直接编辑 `templates/index.html`，刷新浏览器即可
- **不引入数据库**，所有会话数据在内存或 `localStorage`
- **不改动 DeepSeek 模型名**（`deepseek-chat`）除非用户明确要求
- API 密钥**永远不要**硬编码进代码，必须从请求头读取
- 修改 `SYSTEM_PROMPT` 时，**零号规则（条目结构彻底重写）必须保持最高优先级**，位于所有其他规则之前
- 修改 `DETECT_PROMPT` 时，保持评分起点 10 分和"宁可高判"原则
- 前端新增状态变量后，**必须在 `setup()` 末尾的 `return {}` 中导出**，否则模板无法访问
- `commitResult()` 之后的 `saveSession()` 调用和 `clearSession()`（complete 时）不能遗漏

---

## 用户偏好与历史反馈

- 界面风格：深色学术工具风，参考 Notion/Linear 克制美学，**无渐变色**
- 代码风格：精简，不过度抽象，不加不必要的注释
- 改动原则：只做被要求的改动，不主动重构周边代码
