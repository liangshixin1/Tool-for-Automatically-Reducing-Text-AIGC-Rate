import io
import json
import re
import requests
from flask import Flask, request, jsonify, render_template, Response, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

SYSTEM_PROMPT = """你是一名具有二十年以上从业经验的资深专业编审，长期供职于高等教育出版机构，专注于经贸类、国际商务类教材的审稿与编校工作。你当前承担《外贸谈判策略与实战》教材的三审三校任务。该教材定位为高等院校经贸类专业核心课程教材，面向具备一定英语基础和商务背景的本科生或高职生，须严格符合正式出版教材的体例规范与学术标准。

本教材为中英双语教材，每个知识点均有中文表述和对应的英文表述。审校时须同步处理中英两个版本，确保两种语言版本在内容上互相对应、在语言风格上各自符合本语言的专业表达规范。

请你在不改动原文核心观点与知识框架的前提下，对提交的段落进行系统性语言审校与润色，使其达到可直接付印的出版水准。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
一、AIGC痕迹识别与消除（最高优先级）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【结构性套话 — 须删除或改写】
・删除"首先……其次……再次……最后……"式的枚举过渡，以自然的逻辑连接词或句间语义衔接替代
・删除段落结尾的总结套语："综上所述""总的来说""由此可见，……的重要性不言而喻"等
・不使用"值得注意的是""不难发现""需要指出的是""有必要强调"等作为段落跳板的虚置引导语
・避免"第一、第二、第三"与正文散文段落混用造成的结构割裂

【评价性空话 — 须删除】
・删除缺乏实质内容的定性语句："这一点至关重要""具有重要意义""发挥着不可忽视的作用"
・避免以"可以看出""我们不难理解"等伪结论句收束论述
・删除对显而易见内容的反复强调与冗余解释

【模板化句式 — 须改写】
・改写"A是B的基础，B是C的前提，C决定D"式链条堆叠
・避免同一段落内多句共用相同的句型框架（如"通过……可以……；通过……可以……"）
・改写"能够……，从而……，进而……"式流水线推进句，使逻辑关系更为内在、自然
・精简名词并列堆砌（如连列六七项的罗列），聚焦实质性核心表达

【英文AIGC标记 — 须消除】
・删除"It is worth noting that / It is important to emphasize that / Needless to say"等虚置引导
・删除"In summary, / To conclude, / Overall, / In a nutshell,"等程式化收尾
・改写"not only...but also / on the one hand...on the other hand"的机械对仗堆砌
・避免"plays a crucial/vital/pivotal role in / is of paramount importance"等固定搭配滥用
・消除被动语态与现在分词的过度连缀

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
二、学术性与严谨性
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【中文术语规范】
・统一使用本领域规范术语：询盘、报盘、还盘、交易条件、贸易惯例等
・专业术语首次出现可附英文对应词，格式：中文（英文）
・涉及国际贸易惯例（CISG、UCP 600、Incoterms等）须准确援引，不得含糊表述

【英文术语规范】
・使用国际商务领域通用术语：enquiry/inquiry, offer, counter-offer, trade terms等
・同一概念前后一致，不随意替换近义词（如enquiry与inquiry在全文中择一统一）
・避免过度使用被动语态堆叠，保持主动与被动的合理比例

【逻辑论证】
・每一知识点的陈述须具备内在逻辑自洽性，因果关系、层进关系须在句法层面体现
・各段落间的承接关系须自然流畅，避免"另外""同时"等万能衔接词的泛用

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
三、文体与语言规范
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【中文】
・全程使用正式书面语，禁止口语化表达（咱们、其实、就是说、说白了等）
・避免"非常""十分""极其"等程度副词的滥用
・教材正文不使用第一人称，采用客观陈述语气
・标点符号遵守国家标准 GB/T 15834

【英文】
・使用正式学术书面英语，将口语化缩写还原：don't→do not，it's→it is
・句子长度适中，避免过度复杂的嵌套从句
・保持第三人称客观视角，全文语体一致

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
四、输出格式要求（严格执行）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

・直接输出修改后的完整文段
・严格保持与原文相同的段落结构和顺序（中文段落在前，英文段落在后）
・每个自然段作为独立一行输出，段落之间用空行分隔
・不添加任何说明、注释、括号标注、"编审说明"或解释性文字
・不输出原文，只输出修改后的版本
・若某段无需修改，原文照录
・不添加任何Markdown格式符号（**、##、- 等）"""


# ─── helpers ────────────────────────────────────────────────────────────────

def is_cjk(c: str) -> bool:
    cp = ord(c)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x20000 <= cp <= 0x2A6DF
        or 0xF900 <= cp <= 0xFAFF
    )


def detect_lang(text: str) -> str:
    if not text:
        return "en"
    cjk = sum(1 for c in text if is_cjk(c))
    return "zh" if cjk / max(len(text), 1) > 0.15 else "en"


def is_numbered_heading(text: str) -> bool:
    """Match patterns: 1.  /  1.1  /  1.2.3  (with trailing space + content)"""
    return bool(re.match(r"^\d+(\.\d+)*\.?\s+\S", text.strip()))


def parse_docx(file_obj):
    from docx import Document

    doc = Document(file_obj)
    groups: list[dict] = []
    current: dict | None = None
    gid = 0

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        style_lower = para.style.name.lower()
        is_heading_style = "heading" in style_lower or "标题" in style_lower
        is_numbered = is_numbered_heading(text)

        if is_heading_style or is_numbered:
            if current and current["paragraphs"]:
                groups.append(current)
            gid += 1
            current = {
                "id": gid,
                "heading": text,
                "paragraphs": [{"text": text, "lang": "heading"}],
                "combined_text": text,
            }
        else:
            if current is None:
                gid += 1
                current = {
                    "id": gid,
                    "heading": "(前言)",
                    "paragraphs": [],
                    "combined_text": "",
                }
            lang = detect_lang(text)
            current["paragraphs"].append({"text": text, "lang": lang})
            sep = "\n\n" if current["combined_text"] else ""
            current["combined_text"] += sep + text

    if current and current["paragraphs"]:
        groups.append(current)

    return groups


# ─── routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "未找到上传文件"}), 400

    f = request.files["file"]
    if not f.filename or not f.filename.endswith(".docx"):
        return jsonify({"error": "请上传 .docx 格式的文件"}), 400

    try:
        groups = parse_docx(f)
        if not groups:
            return jsonify({"error": "文档中未找到任何段落组，请确认文档包含编号标题（如 1. / 1.1）"}), 400
        return jsonify({"groups": groups, "total": len(groups)})
    except Exception as exc:
        return jsonify({"error": f"文件解析失败：{exc}"}), 500


@app.route("/review", methods=["POST"])
def review():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    temperature = float(data.get("temperature", 0.3))
    temperature = max(0.1, min(temperature, 1.0))
    api_key = request.headers.get("X-API-Key", "").strip()

    if not api_key:
        return jsonify({"error": "请先在右上角输入 API 密钥"}), 401
    if not text:
        return jsonify({"error": "文本内容为空"}), 400

    def generate():
        try:
            resp = requests.post(
                DEEPSEEK_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "temperature": temperature,
                    "stream": True,
                    "max_tokens": 4096,
                },
                stream=True,
                timeout=120,
            )

            if resp.status_code != 200:
                try:
                    err_body = resp.json()
                    msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
                except Exception:
                    msg = f"HTTP {resp.status_code}"
                yield f"data: {json.dumps({'error': msg})}\n\n"
                return

            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    yield "data: [DONE]\n\n"
                    return
                try:
                    chunk = json.loads(payload)
                    content = chunk["choices"][0]["delta"].get("content", "")
                    if content:
                        yield f"data: {json.dumps({'content': content})}\n\n"
                except Exception:
                    pass

        except requests.exceptions.Timeout:
            yield f"data: {json.dumps({'error': 'API 请求超时（120 s），请重试'})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/export", methods=["POST"])
def export_doc():
    from docx import Document
    from docx.shared import RGBColor, Pt

    data = request.get_json(silent=True) or {}
    results: list[dict] = data.get("results", [])

    if not results:
        return jsonify({"error": "没有可导出的内容"}), 400

    doc = Document()

    # Clear default empty paragraph
    for p in list(doc.paragraphs):
        p._element.getparent().remove(p._element)

    TEAL = RGBColor(0, 134, 134)
    BLACK = RGBColor(0, 0, 0)

    for idx, result in enumerate(results):
        originals: list[dict] = result.get("original", [])
        modified: str = result.get("modified", "")
        decision: str = result.get("decision", "approved")

        if decision == "skipped":
            for pd in originals:
                p = doc.add_paragraph()
                run = p.add_run(pd["text"])
                if pd.get("lang") == "heading":
                    run.bold = True
        else:
            # Original paragraphs with strikethrough
            for pd in originals:
                p = doc.add_paragraph()
                run = p.add_run(pd["text"])
                run.font.strike = True
                run.font.color.rgb = BLACK

            # Modified paragraphs – teal italic, no blank separator from originals
            if modified:
                lines = [ln for ln in modified.split("\n") if ln.strip()]
                for ln in lines:
                    p = doc.add_paragraph()
                    run = p.add_run(ln)
                    run.font.italic = True
                    run.font.color.rgb = TEAL

        # Blank paragraph between groups (except last)
        if idx < len(results) - 1:
            doc.add_paragraph()

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    return send_file(
        buf,
        as_attachment=True,
        download_name="revised.docx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
