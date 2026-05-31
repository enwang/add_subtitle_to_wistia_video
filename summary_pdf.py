from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


PAGE_WIDTH = 1654
PAGE_HEIGHT = 2339
A4_WIDTH_POINTS = 595
A4_HEIGHT_POINTS = 842
TITLE_FONT_NAME = "PingFang SC"
BODY_FONT_NAME = "PingFang SC"
CONTENT_TOP = 220
CONTENT_BOTTOM = 2140
CONTENT_LEFT = 132


class SubtitleLike(Protocol):
    start: float
    end: float
    text: str


@dataclass
class ThemeSection:
    title: str
    start: float
    end: float
    text: str
    examples: list[str]


def build_summary_pdf(
    ffmpeg: str,
    output_video_path: Path,
    pdf_path: Path,
    segments: list[SubtitleLike],
    subtitle_count: int,
    detected_language: str | None,
    input_url: str,
    include_images: bool,
    ffprobe_binary: str | None,
) -> None:
    del subtitle_count, detected_language, input_url
    theme_sections = build_theme_sections(segments)
    llm_summary = generate_llm_summary(segments)
    summary_blocks = build_summary_blocks(segments, theme_sections, llm_summary=llm_summary)

    with tempfile.TemporaryDirectory(prefix="video_summary_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        font_path = font_file()
        page_images: list[Path] = []

        summary_pages = paginate_blocks(summary_blocks)
        for index, page_lines in enumerate(summary_pages, start=1):
            page_path = tmp_root / f"summary-page-{index:02}.jpg"
            title = "视频摘要" if index == 1 else ""
            render_text_page(ffmpeg, title, page_lines, page_path)
            page_images.append(page_path)

        if include_images:
            for index, section in enumerate(theme_sections[:3], start=1):
                frame_path = tmp_root / f"frame-{index:02}.png"
                image_page = tmp_root / f"moment-page-{index:02}.jpg"
                timestamp_seconds = section.start + max((section.end - section.start) / 2, 0.5)
                extract_frame(ffmpeg, ffprobe_binary, output_video_path, timestamp_seconds, frame_path)
                caption_lines = wrap_text_block(
                    f"[{clock_timestamp(section.start)} - {clock_timestamp(section.end)}]\n"
                    f"{section.title}\n"
                    f"{summarize_text(section.text, 220)}",
                    64,
                )
                render_image_page(
                    ffmpeg,
                    font_path,
                    frame_path,
                    f"Representative frame {index}",
                    caption_lines,
                    image_page,
                )
                page_images.append(image_page)

        build_pdf_from_images(ffprobe_binary, page_images, pdf_path)


def wrap_text_block(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            if len(current) >= width:
                lines.append(current)
                current = char
            else:
                current += char
        if current:
            lines.append(current)
    return lines


def wrap_cjk_text(text: str, width: int) -> list[str]:
    cleaned = simplify_summary_text(normalize_summary_text(text))
    if not cleaned:
        return []
    paragraphs = re.split(r"\n+", cleaned)
    wrapped: list[str] = []
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            if wrapped and wrapped[-1] != "":
                wrapped.append("")
            continue
        paragraph = re.sub(r"\s+", " ", paragraph)
        tokens = re.findall(r"[A-Za-z0-9.+%-]+|.", paragraph)
        current = ""
        for token in tokens:
            separator = " " if current and re.match(r"[A-Za-z0-9]", current[-1]) and re.match(r"[A-Za-z0-9]", token[0]) else ""
            trial = f"{current}{separator}{token}"
            if len(trial) > width and current:
                wrapped.append(current)
                current = token
            else:
                current = trial
        if current:
            wrapped.append(current)
    return wrapped


def summarize_text(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    clipped = normalized[: limit - 1].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return f"{clipped}..."


def normalize_summary_text(text: str) -> str:
    replacements = {
        "OK": "",
        "ok": "",
        "  ": " ",
        "就是就是": "就是",
        "即是即是": "即是",
        "咦": "",
        "行不行": "",
        "可以嗎": "",
        "客觀的陳述": "",
        "客觀描述": "",
        "純粹是作為一個市場觀察": "",
        "作為學術的探討": "",
    }
    normalized = text
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip(" ,，。")


def simplify_summary_text(text: str) -> str:
    phrase_replacements = [
        ("這條影片", "这条视频"),
        ("影片", "视频"),
        ("篩選器", "筛选器"),
        ("篩選", "筛选"),
        ("強勢", "强势"),
        ("板塊", "板块"),
        ("領導股票", "领涨股"),
        ("領導板塊", "领涨板块"),
        ("領導股", "领涨股"),
        ("誕生", "诞生"),
        ("觀察", "观察"),
        ("買入", "买入"),
        ("買賣建議", "买卖建议"),
        ("學術", "学术"),
        ("這時", "这时"),
        ("這個", "这个"),
        ("這些", "这些"),
        ("這種", "这种"),
        ("這點", "这点"),
        ("過去一天", "过去一天"),
        ("過去一周", "过去一周"),
        ("過去一個月", "过去一个月"),
        ("圖表", "图表"),
        ("電網", "电网"),
        ("讓我看到", "让我看到"),
        ("之後", "之后"),
        ("與", "与"),
        ("類型", "类型"),
        ("趨化劑", "催化剂"),
        ("數據中心", "数据中心"),
        ("光學", "光学"),
        ("光通訊", "光通信"),
        ("矽光子", "硅光子"),
        ("龜光子", "硅光子"),
        ("歷史新高", "历史新高"),
        ("大盤", "大盘"),
        ("週線", "周线"),
        ("這裏", "这里"),
        ("還有", "还有"),
        ("還會", "还会"),
        ("還是", "还是"),
        ("資料中心", "数据中心"),
        ("簡體", "简体"),
        ("穩定幣", "稳定币"),
        ("關鍵", "关键"),
        ("點線", "天线"),
        ("條件", "条件"),
        ("強勁", "强劲"),
        ("低海高走", "低开高走"),
        ("上升低了高走", "低开高走"),
    ]
    char_map = str.maketrans(
        {
            "這": "这",
            "條": "条",
            "個": "个",
            "點": "点",
            "線": "线",
            "畫": "画",
            "塊": "块",
            "導": "导",
            "勢": "势",
            "誕": "诞",
            "觀": "观",
            "買": "买",
            "賣": "卖",
            "學": "学",
            "術": "术",
            "覺": "觉",
            "變": "变",
            "壓": "压",
            "讓": "让",
            "邊": "边",
            "與": "与",
            "類": "类",
            "圖": "图",
            "達": "达",
            "還": "还",
            "長": "长",
            "將": "将",
            "對": "对",
            "為": "为",
            "麼": "么",
            "開": "开",
            "後": "后",
            "應": "应",
            "電": "电",
            "網": "网",
            "產": "产",
            "業": "业",
            "發": "发",
            "體": "体",
            "氣": "气",
            "價": "价",
            "漲": "涨",
            "跌": "跌",
            "創": "创",
            "億": "亿",
            "雲": "云",
            "訊": "讯",
            "穩": "稳",
            "幣": "币",
            "關": "关",
            "鍵": "键",
            "講": "讲",
            "實": "实",
            "轉": "转",
            "簡": "简",
            "號": "号",
            "裡": "里",
            "屬": "属",
            "礎": "础",
            "設": "设",
            "備": "备",
            "劃": "划",
            "級": "级",
            "種": "种",
            "門": "门",
            "強": "强",
        }
    )
    simplified = text
    for source, target in phrase_replacements:
        simplified = simplified.replace(source, target)
    return simplified.translate(char_map)


def merge_segment_text(segments: list[SubtitleLike]) -> str:
    return normalize_summary_text(" ".join(segment.text.strip() for segment in segments if segment.text.strip()))


def extract_tickers(text: str, limit: int = 5) -> list[str]:
    counts: dict[str, int] = {}
    for ticker in re.findall(r"\b[A-Z]{2,5}\b", text):
        if ticker in {"OK", "EPS", "RS", "MA", "AI"}:
            continue
        counts[ticker] = counts.get(ticker, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [ticker for ticker, _ in ranked[:limit]]


def theme_title_from_text(text: str) -> str:
    normalized = normalize_summary_text(text)
    title_map = (
        ("替代能源", "替代能源"),
        ("供電", "供電 / 核電"),
        ("核電", "供電 / 核電"),
        ("太空", "太空"),
        ("低延遲", "低延遲 / AI Agent 基建"),
        ("AI agent", "低延遲 / AI Agent 基建"),
        ("Agentic", "低延遲 / AI Agent 基建"),
        ("光", "光通訊 / 光子"),
        ("矽光子", "光通訊 / 光子"),
        ("硅光子", "光通訊 / 光子"),
        ("龜光子", "光通訊 / 光子"),
        ("Data Center", "AI Data Center"),
    )
    for needle, title in title_map:
        if needle in normalized:
            return title
    return summarize_text(normalized, 30) or "主题"


def detect_theme_anchor(text: str) -> bool:
    patterns = (
        "第一個主題",
        "第一類的主題",
        "另外一個主題",
        "第三個主題",
        "第四個主題",
        "第五類的主題",
        "第五個主題",
        "下一個主題",
        "最後一個主題",
    )
    return any(pattern in text for pattern in patterns)


def build_theme_sections(segments: list[SubtitleLike]) -> list[ThemeSection]:
    anchors = [index for index, segment in enumerate(segments) if detect_theme_anchor(segment.text)]
    if not anchors:
        return []

    sections: list[ThemeSection] = []
    for order, anchor_index in enumerate(anchors):
        end_index = anchors[order + 1] if order + 1 < len(anchors) else len(segments)
        section_segments = segments[anchor_index:end_index]
        section_text = merge_segment_text(section_segments)
        title = theme_title_from_text(section_text)
        sections.append(
            ThemeSection(
                title=title,
                start=section_segments[0].start,
                end=section_segments[-1].end,
                text=section_text,
                examples=extract_tickers(section_text),
            )
        )

    merged: list[ThemeSection] = []
    for section in sections:
        if merged and merged[-1].title == "光通訊 / 光子" and section.title == "AI Data Center":
            merged[-1] = ThemeSection(
                title="光通訊 / 光子 / AI Data Center",
                start=merged[-1].start,
                end=section.end,
                text=f"{merged[-1].text} {section.text}".strip(),
                examples=(merged[-1].examples + [item for item in section.examples if item not in merged[-1].examples])[:6],
            )
            continue
        merged.append(section)
    return merged


def _fmt_time(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02}:{m:02}:{sec:02}"


def _chunk_transcript(segments: list[SubtitleLike], chunk_minutes: float = 15.0) -> list[tuple[float, float, str]]:
    """Split segments into time-based chunks. Returns list of (start, end, text)."""
    if not segments:
        return []
    chunk_secs = chunk_minutes * 60
    chunks: list[tuple[float, float, str]] = []
    current_texts: list[str] = []
    chunk_start = segments[0].start
    chunk_end_target = chunk_start + chunk_secs
    for seg in segments:
        current_texts.append(seg.text.strip())
        if seg.end >= chunk_end_target:
            text = normalize_summary_text(" ".join(t for t in current_texts if t))
            if text:
                chunks.append((chunk_start, seg.end, text))
            current_texts = []
            chunk_start = seg.end
            chunk_end_target = chunk_start + chunk_secs
    if current_texts:
        text = normalize_summary_text(" ".join(t for t in current_texts if t))
        if text:
            chunks.append((chunk_start, segments[-1].end, text))
    return chunks


def _map_chunk(client: object, chunk_index: int, total_chunks: int, start: float, end: float, text: str) -> str:
    """Summarize a single transcript chunk with Claude Haiku (map phase).

    The reduce phase needs raw material to compose specific narrative paragraphs,
    so we ask the map phase to preserve numbers, tickers, and reasoning verbatim.
    """
    prompt = (
        f"以下是一段粤语/普通话财经视频的第 {chunk_index}/{total_chunks} 段字幕"
        f"（时间 {_fmt_time(start)} - {_fmt_time(end)}）。\n\n"
        "请用简体中文整理这段内容，目标是为下一步生成详细摘要保留全部原始素材。请按以下结构输出：\n\n"
        "【主题/方向】：本段讨论的核心话题（一两句话）。\n"
        "【提到的公司/票号/板块】：完整列出，逐个用顿号分隔。\n"
        "【具体数字/数据】：完整保留涨跌幅、营收增长率、净留存率、短仓占比、年份、估值、"
        "价格区间、筛选条件等任何数字。这一项不要总结，要按出现顺序逐条列举。\n"
        "【讲者论点 & 推理链条】：用 3-6 句话还原讲者的论证（“因为...所以...”、“如果...则...”、"
        "“市场原本担心 X，但是 Y”、举例说明等）。\n"
        "【金句/原话片段】：摘抄 1-2 句最能代表讲者立场的原话（保留口语风格即可）。\n\n"
        f"字幕内容：\n{text}\n\n"
        "请直接输出上述五个标签段，不要加任何前言或结语。"
    )
    import anthropic as _anthropic
    response = _anthropic.Anthropic().messages.create(
        model="claude-haiku-4-5-20251001",  # cheapest model
        max_tokens=1400,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    return response.content[0].text  # type: ignore[union-attr]


def _duration_str(total_duration_seconds: float, chunk_count: int) -> str:
    if total_duration_seconds >= 3600:
        return f"约{total_duration_seconds / 3600:.1f}小时"
    if total_duration_seconds >= 60:
        return f"约{int(total_duration_seconds / 60)}分钟"
    return f"{chunk_count}段"


def _call_tool(prompt: str, tool: dict, max_tokens: int = 6000) -> dict:
    """Call Claude with a single tool and return the tool input dict."""
    import anthropic as _anthropic
    response = _anthropic.Anthropic().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == tool["name"]:  # type: ignore[union-attr]
            return block.input  # type: ignore[union-attr]
    raise ValueError(f"No {tool['name']} tool_use block found in response")


def _reduce_sections(summaries_text: str, tickers_str: str, duration_str: str) -> dict:
    """Single reduce call: build a two-part summary (executive narrative + deep-dive outline).

    Returns a dict with:
      - one_line_takeaway: 20-char hook
      - executive_summary: 4-8 narrative paragraphs ({label, content}) — the "影片重點總結" layer
      - outline: 3-6 strings — the "大綱" listing of deep-dive topics
      - sections: list of {title, points: [{label?, content, sub_points?: [{label?, content}]}]} —
                  the "圖表教學總結" layer
      - keywords: up to 24 keywords
    """
    prompt = (
        f"以下是一个 {duration_str} 财经视频的分段总结。请调用 write_summary 工具，"
        "输出一份**两层结构**的中文摘要：\n"
        "  (A) executive_summary — 高层叙事段落（导读层）；\n"
        "  (B) outline + sections — 大纲 + 多章节深入展开（深入层）。\n\n"
        "视频类型可能是：图表教学、市场分析、直播 Q&A、个股深度、宏观点评、交易策略复盘等。"
        "请根据**视频实际内容**为两层结构各取一个**自然的中文标题**：\n"
        "  • executive_title：导读层的标题（4-10 字），例如 “影片重点总结”、“直播要点”、"
        "“市场速读”、“核心观点速览”等，由内容决定。\n"
        "  • deep_dive_title：深入层的标题（4-10 字），例如 “图表教学总结”、“分主题展开”、"
        "“热门板块深读”、“Q&A 整理”等，由内容决定。\n"
        "不要套用模板；如果视频是 Q&A，标题应反映 Q&A；如果是个股深度，应反映个股。\n\n"
        f"视频中提到的股票代码：{tickers_str}\n\n"
        f"分段总结：\n{summaries_text}\n\n"
        "—— 输出语言：默认简体中文；若分段总结明显以繁体中文出现专有词（如“軋空”、“板塊輪動”），可保留繁体。"
        "不要用 bullet points 写成短语堆叠，必须使用完整中文句子。\n\n"
        "—— one_line_takeaway：一句话（20字以内）总结视频最核心的信息。\n\n"
        "—— executive_summary：4-8 段叙事性段落，每段包含：\n"
        "    • label：6-14 字的小标题，反映该段的核心论点（标题应反映视频实际内容）\n"
        "    • content：100-260 字的完整段落。要求：\n"
        "        ① 用陈述句把讲者的观点串成可读的小故事，不要列点；\n"
        "        ② 完整保留具体数字（涨跌幅、营收增长率、短仓占比、年份、净留存率等）；\n"
        "        ③ 提到的公司/板块要带英文票号或正式名称；\n"
        "        ④ 体现讲者的“因为...所以...”推理链条；\n"
        "        ⑤ 段落之间要可以独立阅读，但合在一起像一篇导读。\n\n"
        "—— outline：3-6 项主题词（10 字以内），将作为 sections 的目录索引展示在深入层开头。"
        "如果视频结构上不适合大纲（例如完全自由的对谈），可以返回空数组。\n\n"
        "—— sections：3-6 个章节深入展开。每个章节：\n"
        "    • title：10-20 字章节标题（不要加“一、”前缀，代码自动加）\n"
        "    • points：2-5 个编号要点。每个要点包含：\n"
        "        - label：（可选）6-15 字小标题\n"
        "        - content：80-260 字段落。必须包含具体数字、股票或板块代码、讲者推理。"
        "如果要点实质内容主要在 sub_points 中，content 可以是 1-2 句引子或空字符串\n"
        "        - sub_points：（可选）0-5 个子要点。每个：\n"
        "            * label：（可选）4-12 字标签（如“通讯与身分验证服务”、“设计平台（如 Figma）”、"
        "“极高的轧空机会”）\n"
        "            * content：40-200 字具体内容\n\n"
        "—— keywords：最多 20 个关键词（股票代码、板块名称、核心概念）。\n\n"
        "————————————————————————\n"
        "（仅作结构示例；标题与内容必须根据视频实际素材决定，不要照抄。）\n"
        "executive_summary 段落样例：\n"
        "  {label: \"市场高风险警告\", content: \"讲者对近期市场发出警示，提醒投资者必须提高风险"
        "意识。他指出，如果投资者“过贪”，可能会将今年以来累积的主要获利全部回吐。\"}\n"
        "  {label: \"板块轮动与中期调整\", content: \"讲者观察到市场领头羊及多个板块出现了下跌迹象，"
        "包括光通讯、AI 以及 NVIDIA、AMD、Intel 等相关科技股均出现单日数个百分比的跌幅。他警告市场"
        "极有可能进入“中期调整”，在此情况下，大型股可能面临 10% 到 20% 的修正，而中小型股的跌幅"
        "甚至可达 30%。\"}\n\n"
        "sections 章节样例（结构参考）：\n"
        "  {title: \"半导体与核心 AI 股票的风险警告\", points: [\n"
        "    {label: \"估值极端 & 均值回归风险\", content: \"目前的半导体类股（如 SOXX、SMH）以及 "
        "AI 领先指标股票的估值已经来到了非常极端的水平，面临“均值回归”的风险...\"},\n"
        "    {label: \"投资焦点转移建议\", content: \"\", sub_points: [\n"
        "        {label: \"获利了结部分持股\", content: \"...\"},\n"
        "        {label: \"转向其他具备潜力的产业\", content: \"...\"},\n"
        "    ]},\n"
        "  ]}\n\n"
        "重要原则：\n"
        "- 严格依据视频实际内容，不要编造未提及的数据或公司\n"
        "- 完整保留所有具体数字（涨跌幅、日期、估值、占比、营收增长率、短仓占比、时间周期等）\n"
        "- 保留讲者的推理链条（不只是结论，还要有“为什么”）\n"
        "- executive_summary 与 sections 之间允许有内容重叠，但叙事角度不同：前者是连续叙事，"
        "  后者是分主题展开\n"
        "- 不要使用泛泛而谈的套话（如“投资者应注意风险”、“建议谨慎操作”）"
    )
    para_schema = {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["content"],
    }
    tool = {
        "name": "write_summary",
        "description": "Output a two-part summary: executive narrative + structured deep-dive",
        "input_schema": {
            "type": "object",
            "properties": {
                "one_line_takeaway": {"type": "string"},
                "executive_title": {"type": "string"},
                "deep_dive_title": {"type": "string"},
                "executive_summary": {
                    "type": "array",
                    "maxItems": 10,
                    "items": para_schema,
                },
                "outline": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string"},
                },
                "sections": {
                    "type": "array",
                    "maxItems": 7,
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "points": {
                                "type": "array",
                                "maxItems": 8,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "content": {"type": "string"},
                                        "sub_points": {
                                            "type": "array",
                                            "maxItems": 6,
                                            "items": para_schema,
                                        },
                                    },
                                    "required": ["content"],
                                },
                            },
                        },
                        "required": ["title", "points"],
                    },
                },
                "keywords": {"type": "array", "maxItems": 24, "items": {"type": "string"}},
            },
            "required": ["one_line_takeaway", "sections"],
        },
    }
    return _call_tool(prompt, tool, max_tokens=16000)


def _reduce_summary(client: object, chunk_summaries: list[str], all_tickers: list[str], total_duration_seconds: float = 0) -> dict:
    """Synthesize chunk summaries into a structured section-tree summary."""
    del client
    summaries_text = "\n\n".join(f"【第{i + 1}段总结】\n{s}" for i, s in enumerate(chunk_summaries))
    tickers_str = "、".join(all_tickers[:20]) if all_tickers else "（未检测到）"
    dur = _duration_str(total_duration_seconds, len(chunk_summaries))
    return _reduce_sections(summaries_text, tickers_str, dur)


def generate_llm_summary(segments: list[SubtitleLike]) -> dict | None:
    """Generate AI-powered summary using Claude API. Returns None if unavailable."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic as _anthropic  # noqa: F401
    except ImportError:
        print("  [summary] anthropic package not installed; using template fallback.", flush=True)
        return None

    all_tickers = extract_tickers(merge_segment_text(segments), limit=20)
    chunks = _chunk_transcript(segments, chunk_minutes=15.0)
    total_duration = segments[-1].end if segments else 0
    print(f"  [summary] Summarising {len(chunks)} transcript chunks with Claude Haiku...", flush=True)

    chunk_summaries: list[str] = [""] * len(chunks)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(_map_chunk, None, i + 1, len(chunks), start, end, text): i
                for i, (start, end, text) in enumerate(chunks)
            }
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                chunk_summaries[idx] = future.result()
                print(f"  [summary] ✓ Chunk {idx + 1}/{len(chunks)} done", flush=True)

        print("  [summary] Synthesising final summary with Claude Haiku...", flush=True)
        result = _reduce_summary(None, chunk_summaries, all_tickers, total_duration_seconds=total_duration)
        print("  [summary] ✓ LLM summary complete.", flush=True)
        return result
    except Exception as exc:
        print(f"  [summary] LLM summarisation failed ({exc}); using template fallback.", flush=True)
        return None


def screener_rules(segments: list[SubtitleLike]) -> list[str]:
    text = merge_segment_text(segments[:220])
    rules: list[str] = []
    if "過去一天上升6%" in text:
        rules.append("在市場下跌日，先篩過去一天仍然上升 6% 的股票。")
    if "過去一周上升10%" in text:
        rules.append("再看過去一周上升 10% 的股票，確認短線強度。")
    if "過去一個月升30%" in text or "過去一個月上升30%" in text:
        rules.append("再看過去一個月上升 30% 的股票，找在弱市中逆勢走強的名字。")
    if "跌穿200天線" in text or "200天線" in text:
        rules.append("特別在大盤接近或跌穿 200 天線時做篩選，因為這時更容易看出真正的相對強弱。")
    return rules


def build_intro_paragraphs(segments: list[SubtitleLike]) -> list[str]:
    paragraphs = [
        "这条视频的重点不是推荐买哪只股票，而是示范在大盘走弱、甚至跌破 200 天线时，怎样用筛选器把真正逆势走强的股票和主题先筛出来，再回头理解背后的原因。"
    ]
    intro_text = merge_segment_text([segment for segment in segments if segment.start <= 130])
    if "是不一定在市場見底那天才誕生的" in intro_text:
        paragraphs.append("讲者反复强调，强势板块、领涨板块和领涨股，并不一定要等到市场正式见底当天才出现。很多时候，它们会在市场最差、情绪最弱的时候就先走出来。")
    paragraphs.append("所以这份摘要会先整理筛选框架，再把视频里重点讲到的五个方向展开说明，尽量还原讲者真正想表达的市场结构，而不是只摘几句字幕拼在一起。")
    return paragraphs


def build_method_paragraphs(segments: list[SubtitleLike]) -> list[str]:
    rules = screener_rules(segments)
    paragraphs: list[str] = []
    if rules:
        paragraphs.append("讲者的筛选方法很直接，核心是先在弱市里找出“价格行为不对劲”的股票，也就是指数在跌，但它们还能逆势上涨、低开高走，或者迅速收复失地。")
        paragraphs.append("视频里反复用到的条件包括：" + "；".join(rule.rstrip("。") for rule in rules) + "。")
    paragraphs.append("筛选出来之后，下一步不是立刻追价，而是把这些股票按题材归类，看它们是否集中指向同一条需求线。如果很多强势股都落在同一个方向，那个方向就值得重点跟踪。")
    paragraphs.append("这也是整条视频真正的训练目标：先用筛选器看到价格强弱，再用基本面、订单、指引和产业需求去解释强弱，逐步找出市场正在提前布局什么。")
    return paragraphs


def format_examples(examples: list[str], limit: int = 4) -> str:
    unique: list[str] = []
    for example in examples:
        if example not in unique:
            unique.append(example)
    return "、".join(unique[:limit])


def detailed_theme_paragraphs(section: ThemeSection) -> list[str]:
    examples = format_examples(section.examples)
    if section.title == "替代能源":
        return [
            "讲者把替代能源放在第一位，核心逻辑不是短线消息刺激，而是地缘政治与能源安全重新变成市场主线。石油和天然气一旦受冲突影响，价格就容易大幅波动，因此市场会重新重视太阳能等替代能源。",
            "这一段真正想提醒的是：当市场开始担心传统能源供应不稳定时，资金会提前去找“替代方案”与“能源分散化”受益者，而不是等新闻完全明朗后才行动。",
            "视频里特别强调，这个板块在大盘偏弱时还能低开高走，说明资金不是单纯做防守，而是在提前布局下一阶段可能扩散的强势主题。相关例子包括 " + examples + "。" if examples else "视频里特别强调，这个板块在大盘偏弱时还能低开高走，说明资金不是单纯做防守，而是在提前布局下一阶段可能扩散的强势主题。",
        ]
    if section.title == "供電 / 核電":
        return [
            "第二个方向是供电和核电。讲者的意思很明确：AI Data Center 继续扩张后，受益的已经不只是服务器、网络设备和零部件，连“能不能尽快稳定供电”本身都变成投资主题。",
            "这和旧思路的区别在于，市场以前更爱看芯片、交换机、光模块；而这次视频强调的是，如果电力瓶颈会卡住数据中心扩张，那供电能力本身就会被重新定价。",
            "他特别提到，很多数据中心建在偏远地区，传统电网接入慢、成本高，所以能快速提供电力、具备模块化发电能力，或者直接与核电供给相关的公司，会更容易获得资金关注。相关例子包括 " + examples + "。" if examples else "他特别提到，很多数据中心建在偏远地区，传统电网接入慢、成本高，所以能快速提供电力、具备模块化发电能力，或者直接与核电供给相关的公司，会更容易获得资金关注。",
        ]
    if section.title == "太空":
        return [
            "第三个主题是太空。这里讲者想表达的重点不是“财报好不好看”，而是市场到底在交易短期结果，还是在交易未来一两年的收入扩张和订单预期。",
            "视频里举的例子很典型：财报当下甚至可以不漂亮，但如果管理层把未来收入目标拉得足够高，市场会把它理解成行业需求正在加速释放，于是股价先反应未来。",
            "视频中的例子显示，哪怕公司当期财报和盈利数字不漂亮，只要管理层给出的远期收入指引足够强、成长空间足够大，股价依然可能在弱市里走出低开高走甚至快速反转的走势。",
        ]
    if section.title == "低延遲 / AI Agent 基建":
        return [
            "第四个方向是低延迟与 AI Agent 基建。讲者把它理解为一条基础设施逻辑：生成式 AI 往 Agent 发展之后，对响应速度、边缘网络、流量调度和实时传输的要求都在提高。",
            "他也借这个例子说明，不能只把这些公司理解成传统网络安全或 CDN 公司。只要业务转型后刚好卡在 Agent 流量和实时交互这一层，市场就可能重新给更高估值。",
            "因此真正受益的，不一定只是最表面的 AI 应用公司，也可能是网络加速、边缘计算、Agentic Internet 基础设施层，甚至和稳定币流量增长相关的底层网络平台。视频里提到的代表包括 " + examples + "。" if examples else "因此真正受益的，不一定只是最表面的 AI 应用公司，也可能是网络加速、边缘计算、Agentic Internet 基础设施层，甚至和稳定币流量增长相关的底层网络平台。",
        ]
    if section.title == "光通訊 / 光子 / AI Data Center":
        return [
            "第五个主题可以概括成“光通信、硅光子，以及重新转强的 AI Data Center 链”。讲者的原话里先讲“要相信光”，后面又补充部分 AI Data Center 概念重新回归，本质上都是算力基础设施重新得到资金认可。",
            "这段的重点不只是某一只股票突然暴涨，而是多个和“光”有关的环节在同一阶段一起转强，包括光模块、光传输、硅光子以及部分大市值的数据中心公司，这种同步更像主题回流。",
            "这部分一方面看的是光模块、光学传输、硅光子等环节重新变强；另一方面看的是部分大型 AI Data Center 相关公司也重新出现逆势上涨，说明市场可能在回到更底层、更硬件化的主线。视频里提到的例子包括 " + examples + "。" if examples else "这部分一方面看的是光模块、光学传输、硅光子等环节重新变强；另一方面看的是部分大型 AI Data Center 相关公司也重新出现逆势上涨，说明市场可能在回到更底层、更硬件化的主线。",
        ]
    return [summarize_text(section.text, 240)]


def closing_lines() -> list[str]:
    return [
        "结尾提醒不是去追所有相关概念股，而是先找出价格行为明显强过大盘的股票，再回头核对背后的业务、需求变化和催化剂。",
        "真正的重点是先看到“谁在弱市里不愿意跌”，再研究“市场为什么愿意买它”。",
    ]


def build_summary_blocks(
    segments: list[SubtitleLike],
    theme_sections: list[ThemeSection],
    llm_summary: dict | None = None,
) -> list[list[str]]:
    blocks: list[list[str]] = []
    wrap_width = 44

    if llm_summary:
        return _build_llm_blocks(llm_summary, wrap_width)

    # --- Template fallback ---
    overview_block = ["核心结论", ""]
    for paragraph in build_intro_paragraphs(segments):
        overview_block.extend(wrap_cjk_text(paragraph, wrap_width))
        overview_block.append("")
    blocks.append(overview_block[:-1] if overview_block[-1] == "" else overview_block)

    method_block = ["筛选框架", ""]
    for paragraph in build_method_paragraphs(segments):
        method_block.extend(wrap_cjk_text(paragraph, wrap_width))
        method_block.append("")
    blocks.append(method_block[:-1] if method_block[-1] == "" else method_block)

    if theme_sections:
        theme_intro = ["五个主题", ""]
        theme_intro.extend(wrap_cjk_text("以下五个方向，是讲者把筛选结果归纳之后认为最值得跟踪的主题。重点不在于今天立刻买入，而在于这些方向已经在弱市里显露出相对强度。", wrap_width))
        blocks.append(theme_intro)
        for index, section in enumerate(theme_sections, start=1):
            theme_block = [f"{index}. {simplify_summary_text(section.title)}", ""]
            for paragraph in detailed_theme_paragraphs(section):
                theme_block.extend(wrap_cjk_text(paragraph, wrap_width))
                theme_block.append("")
            blocks.append(theme_block[:-1] if theme_block[-1] == "" else theme_block)
    else:
        blocks.append(["核心主题", "", *wrap_cjk_text("未能从字幕中可靠提取主题段落，因此这次只保留方法论层面的摘要。", wrap_width)])

    closing_block = ["最后的用法", ""]
    for paragraph in closing_lines():
        closing_block.extend(wrap_cjk_text(paragraph, wrap_width))
        closing_block.append("")
    blocks.append(closing_block[:-1] if closing_block[-1] == "" else closing_block)
    return blocks


def _coerce_list(val: object) -> list:
    """Return val as a list. Handles LLM returning a JSON-encoded string instead of an array."""
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return [s] if s else []
    return []


LABELED_PARA_RE = re.compile(r"^\s*\*?\*?(.{2,20}?)\*?\*?\s*[：:]\s*(.+)$", re.DOTALL)


def _split_labeled_para(item: object) -> tuple[str | None, str]:
    """Return (label, content) if item carries a label/content shape; otherwise (None, text).

    For dict items, returns the label whenever present, even if content is empty —
    callers may render the label alone when substance lives in nested sub_points.
    """
    if isinstance(item, dict):
        label = (item.get("label") or "").strip() or None
        content = (item.get("content") or "").strip()
        return label, content
    text = str(item).strip()
    if not text:
        return None, ""
    match = LABELED_PARA_RE.match(text)
    if match:
        candidate_label = match.group(1).strip().strip("*")
        candidate_content = match.group(2).strip()
        if candidate_label and candidate_content and len(candidate_label) <= 18:
            return candidate_label, candidate_content
    return None, text


CJK_NUMERALS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


def _section_title_line(index: int, raw_title: str) -> str:
    numeral = CJK_NUMERALS[index] if index < len(CJK_NUMERALS) else str(index + 1)
    clean = simplify_summary_text(raw_title.strip())[:30] or "主题"
    return f"{numeral}、{clean}"


def _build_llm_blocks(llm_summary: dict, wrap_width: int) -> list[list[str]]:
    """Build page blocks from LLM-generated summary dict.

    New schema:
      {one_line_takeaway, sections: [{title, points: [{label?, content, sub_points?: [{label?, content}]}]}], keywords}
    """
    blocks: list[list[str]] = []
    MAX_PARA_LINES = 8           # ~352 chars per paragraph at wrap_width=44
    MAX_SUBPOINT_LINES = 5       # ~220 chars per sub-bullet body
    MAX_POINTS_PER_SECTION = 6
    MAX_SUBPOINTS = 5

    def _para_lines(text: str, cap: int = MAX_PARA_LINES) -> list[str]:
        """Wrap text and cap line count, appending … if truncated."""
        lines = wrap_cjk_text(text.strip(), wrap_width)
        if len(lines) > cap:
            lines = lines[:cap]
            lines[-1] = lines[-1][: wrap_width - 1] + "…"
        return lines

    # ── One-line takeaway ───────────────────────────────────────────
    takeaway = (llm_summary.get("one_line_takeaway") or "").strip()
    if takeaway:
        blocks.append(["一句话总结", "", *wrap_cjk_text(takeaway, wrap_width)[:2]])

    # ── Sections (the main content) ─────────────────────────────────
    # Section heading is its own block so a heavy first point (8 lines + 5
    # sub-points) still fits within the 1920px page budget. Worst-case
    # standalone point block: 56 + 8*44 + 5*(26+50+5*44) = 1888 + 18 padding.
    sections = _coerce_list(llm_summary.get("sections"))[:len(CJK_NUMERALS)]
    for s_idx, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        title = (section.get("title") or "").strip()
        points = _coerce_list(section.get("points"))[:MAX_POINTS_PER_SECTION]
        if not title and not points:
            continue
        section_heading = _section_title_line(s_idx, title)
        blocks.append([section_heading])

        for p_idx, point in enumerate(points):
            block: list[str] = []
            label, content = _split_labeled_para(point)
            sub_points = _coerce_list(point.get("sub_points") if isinstance(point, dict) else None)[:MAX_SUBPOINTS]

            header = f"{p_idx + 1}. {label}" if label else f"{p_idx + 1}."
            block.append(header)
            if content:
                block.extend(_para_lines(content))

            for sub in sub_points:
                sub_label, sub_content = _split_labeled_para(sub)
                if not sub_content:
                    continue
                block.append("")
                if sub_label:
                    block.append(f"◦ {sub_label}")
                    block.extend(_para_lines(sub_content, cap=MAX_SUBPOINT_LINES))
                else:
                    sub_lines = _para_lines(sub_content, cap=MAX_SUBPOINT_LINES)
                    if sub_lines:
                        sub_lines[0] = f"◦ {sub_lines[0]}"
                    block.extend(sub_lines)

            blocks.append(block)

    # ── Keywords (rows of 6, max 24) ────────────────────────────────
    keywords = _coerce_list(llm_summary.get("keywords"))[:24]
    if keywords:
        kw_block = ["关键词", ""]
        row_size = 6
        for i in range(0, len(keywords), row_size):
            kw_block.append("    ".join(str(k).strip() for k in keywords[i: i + row_size]))
        blocks.append(kw_block)

    if not blocks:
        blocks.append(["未能生成摘要。"])

    return blocks


def render_summary_markdown(llm_summary: dict, video_title: str | None = None) -> str:
    """Render the structured LLM summary as a markdown document.

    Layout — section headings come from the LLM (executive_title / deep_dive_title),
    so a chart-teaching video, a Q&A, and a market-analysis video each get titles
    that match their actual content. Neutral fallbacks are used if the LLM omits
    them.

    Markdown preserves whatever script the LLM produced (no traditional/simplified
    conversion) so quoted phrases stay readable.
    """
    parts: list[str] = []
    if video_title:
        parts.append(f"# {video_title.strip()}\n")

    takeaway = (llm_summary.get("one_line_takeaway") or "").strip()
    if takeaway:
        parts.append(f"> {takeaway}\n")

    exec_paragraphs = _coerce_list(llm_summary.get("executive_summary"))
    exec_title = (llm_summary.get("executive_title") or "").strip() or "重点速览"
    if exec_paragraphs:
        parts.append(f"## {exec_title}\n")
        for para in exec_paragraphs:
            label, content = _split_labeled_para(para)
            if not content and not label:
                continue
            if label and content:
                parts.append(f"**{label}**：{content}\n")
            elif label:
                parts.append(f"**{label}**\n")
            else:
                parts.append(f"{content}\n")

    outline = [str(item).strip() for item in _coerce_list(llm_summary.get("outline")) if str(item).strip()]
    sections = _coerce_list(llm_summary.get("sections"))
    deep_dive_title = (llm_summary.get("deep_dive_title") or "").strip() or "分主题展开"

    if outline or sections:
        if exec_paragraphs:
            parts.append("\n———\n")
        parts.append(f"## {deep_dive_title}\n")

    if outline:
        parts.append("**大纲：**\n")
        for item in outline:
            parts.append(f"- {item}")
        parts.append("")

    for s_idx, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            continue
        title = (section.get("title") or "").strip() or f"主題 {s_idx}"
        parts.append(f"\n### {s_idx}. {title}\n")
        for point in _coerce_list(section.get("points")):
            label, content = _split_labeled_para(point)
            sub_points = _coerce_list(point.get("sub_points") if isinstance(point, dict) else None)

            if label and content:
                parts.append(f"**{label}：** {content}\n")
            elif label:
                parts.append(f"**{label}**\n")
            elif content:
                parts.append(f"{content}\n")

            for sub in sub_points:
                sub_label, sub_content = _split_labeled_para(sub)
                if not sub_content and not sub_label:
                    continue
                if sub_label and sub_content:
                    parts.append(f"- **{sub_label}：** {sub_content}")
                elif sub_label:
                    parts.append(f"- **{sub_label}**")
                else:
                    parts.append(f"- {sub_content}")
            if sub_points:
                parts.append("")

    keywords = [str(k).strip() for k in _coerce_list(llm_summary.get("keywords")) if str(k).strip()]
    if keywords:
        parts.append("\n---\n")
        parts.append("**关键词**：" + " ｜ ".join(keywords[:24]) + "\n")

    return "\n".join(parts).rstrip() + "\n"


HEADING_TITLES = {
    "一句话总结",
    "关键词",
    # Template fallback headings (still emitted when LLM unavailable):
    "核心结论",
    "筛选框架",
    "五个主题",
    "核心主题",
    "最后的用法",
}

SECTION_PREFIX_RE = re.compile(r"^[一二三四五六七八九十]+、")


def line_style(line: str) -> str:
    simplified = simplify_summary_text(line).strip()
    if not simplified:
        return "Spacer"
    if simplified in HEADING_TITLES:
        return "Heading"
    if SECTION_PREFIX_RE.match(simplified):
        return "Heading"
    if re.match(r"^\d+个主题$", simplified):
        return "Heading"
    if re.match(r"^\d+\.(\s|$)", simplified):
        return "Subheading"
    if simplified.startswith("◆ "):
        return "Subheading"
    if simplified.startswith(("■ ", "◦ ")):
        return "Subbullet"
    return "Body"


def line_height(line: str) -> int:
    style = line_style(line)
    if style == "Spacer":
        return 26
    if style == "Heading":
        return 66
    if style == "Subheading":
        return 56
    if style == "Subbullet":
        return 50
    return 44


def block_height(block: list[str]) -> int:
    return sum(line_height(line) for line in block) + 18


def paginate_blocks(blocks: list[list[str]]) -> list[list[str]]:
    pages: list[list[str]] = []
    current_page: list[str] = []
    current_height = 0
    max_height = CONTENT_BOTTOM - CONTENT_TOP

    for block in blocks:
        height = block_height(block)
        if current_page and current_height + height > max_height:
            pages.append(current_page)
            current_page = []
            current_height = 0
        current_page.extend(block)
        current_height += height
    if current_page:
        pages.append(current_page)
    return pages or [["未能生成摘要。"]]


def font_file() -> Path:
    candidates = [
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No suitable system font was found for PDF summary rendering.")


def ass_escape(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def ffmpeg_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def write_ass_page(title: str, body_lines: list[str], ass_path: Path) -> None:
    events: list[str] = []
    y = CONTENT_TOP
    if title.strip():
        title_text = ass_escape(simplify_summary_text(title))
        events.append(f"Dialogue: 0,0:00:00.00,0:00:05.00,Title,,0,0,0,,{{\\an7\\pos({CONTENT_LEFT},118)}}{title_text}")
    for raw_line in body_lines:
        simplified = simplify_summary_text(raw_line)
        if not simplified.strip():
            y += line_height("")
            continue
        events.append(f"Dialogue: 0,0:00:00.00,0:00:05.00,{line_style(simplified)},,0,0,0,,{{\\an7\\pos({CONTENT_LEFT},{y})}}{ass_escape(simplified)}")
        y += line_height(simplified)
    ass_path.write_text(
        f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PAGE_WIDTH}
PlayResY: {PAGE_HEIGHT}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,{TITLE_FONT_NAME},74,&H002A2A2A,&H002A2A2A,&H00F8F6F1,&H00F8F6F1,1,0,0,0,100,100,0,0,1,0,0,7,{CONTENT_LEFT},82,120,1
Style: Heading,{TITLE_FONT_NAME},48,&H002A2A2A,&H002A2A2A,&H00F8F6F1,&H00F8F6F1,1,0,0,0,100,100,0,0,1,0,0,7,{CONTENT_LEFT},82,260,1
Style: Subheading,{TITLE_FONT_NAME},38,&H002A2A2A,&H002A2A2A,&H00F8F6F1,&H00F8F6F1,1,0,0,0,100,100,0,0,1,0,0,7,{CONTENT_LEFT},82,300,1
Style: Subbullet,{TITLE_FONT_NAME},33,&H00424242,&H00424242,&H00F8F6F1,&H00F8F6F1,1,0,0,0,100,100,0,0,1,0,0,7,{CONTENT_LEFT},82,320,1
Style: Body,{BODY_FONT_NAME},35,&H002A2A2A,&H002A2A2A,&H00F8F6F1,&H00F8F6F1,0,0,0,0,100,100,0,0,1,0,0,7,{CONTENT_LEFT},82,340,1
Style: Spacer,{BODY_FONT_NAME},35,&H00F8F6F1,&H00F8F6F1,&H00F8F6F1,&H00F8F6F1,0,0,0,0,100,100,0,0,1,0,0,7,{CONTENT_LEFT},82,340,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{chr(10).join(events)}
""",
        encoding="utf-8",
    )


def render_text_page(ffmpeg: str, title: str, body_lines: list[str], output_path: Path) -> None:
    ass_path = output_path.with_suffix(".ass")
    write_ass_page(title, body_lines, ass_path)
    run([ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c=0xF8F6F1:s={PAGE_WIDTH}x{PAGE_HEIGHT}:d=1", "-vf", f"subtitles='{ffmpeg_escape(str(ass_path))}'", "-frames:v", "1", "-update", "1", "-q:v", "2", str(output_path)])


def render_image_page(ffmpeg: str, font_path: Path, image_path: Path, title: str, caption_lines: list[str], output_path: Path) -> None:
    caption_file = output_path.with_suffix(".txt")
    caption_file.write_text("\n".join(line.replace("%", r"\%") for line in caption_lines), encoding="utf-8")
    filter_graph = (
        "[0:v]scale=1434:-1[img];"
        f"[1:v]drawtext=fontfile='{ffmpeg_escape(str(font_path))}':text='{title}':fontcolor=black:fontsize=44:x=110:y=110[base];"
        "[base][img]overlay=(W-w)/2:230[tmp];"
        f"[tmp]drawtext=fontfile='{ffmpeg_escape(str(font_path))}':textfile='{ffmpeg_escape(str(caption_file))}':fontcolor=black:fontsize=28:line_spacing=12:x=110:y=1780"
    )
    run([ffmpeg, "-y", "-loop", "1", "-i", str(image_path), "-f", "lavfi", "-i", f"color=c=white:s={PAGE_WIDTH}x{PAGE_HEIGHT}:d=1", "-filter_complex", filter_graph, "-frames:v", "1", "-update", "1", "-q:v", "2", str(output_path)])


def media_duration_seconds(ffprobe: str | None, path: Path) -> float | None:
    if not ffprobe:
        return None
    try:
        output = subprocess.check_output([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True).strip()
        duration = float(output)
    except (subprocess.CalledProcessError, OSError, ValueError):
        return None
    return duration if duration > 0 else None


def image_dimensions(ffprobe: str | None, path: Path) -> tuple[int, int]:
    if not ffprobe:
        raise RuntimeError("ffprobe is required to measure summary page images.")
    output = subprocess.check_output([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)], text=True).strip()
    width_text, height_text = output.split("x", 1)
    return int(width_text), int(height_text)


def build_pdf_from_images(ffprobe: str | None, image_paths: list[Path], pdf_path: Path) -> None:
    objects: list[bytes] = []

    def add_object(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    page_object_ids: list[int] = []
    for page_number, image_path in enumerate(image_paths, start=1):
        width, height = image_dimensions(ffprobe, image_path)
        image_bytes = image_path.read_bytes()
        image_id = add_object((f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(image_bytes)} >>\n").encode("ascii") + b"stream\n" + image_bytes + b"\nendstream")
        content_stream = f"q {A4_WIDTH_POINTS} 0 0 {A4_HEIGHT_POINTS} 0 0 cm /Im{page_number} Do Q".encode("ascii")
        content_id = add_object(f"<< /Length {len(content_stream)} >>\n".encode("ascii") + b"stream\n" + content_stream + b"\nendstream")
        page_id = add_object((f"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 {A4_WIDTH_POINTS} {A4_HEIGHT_POINTS}] /Resources << /XObject << /Im{page_number} {image_id} 0 R >> >> /Contents {content_id} 0 R >>").encode("ascii"))
        page_object_ids.append(page_id)

    pages_id = add_object(f"<< /Type /Pages /Count {len(page_object_ids)} /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_object_ids)}] >>".encode("ascii"))
    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii"))
    for page_id in page_object_ids:
        objects[page_id - 1] = objects[page_id - 1].replace(b"/Parent 0 0 R", f"/Parent {pages_id} 0 R".encode("ascii"), 1)

    pdf = bytearray(b"%PDF-1.4\n%\xff\xff\xff\xff\n")
    offsets = [0]
    for object_id, payload in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_id} 0 obj\n".encode("ascii"))
        pdf.extend(payload)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010} 00000 n \n".encode("ascii"))
    pdf.extend((f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n").encode("ascii"))
    pdf_path.write_bytes(pdf)


def extract_frame(ffmpeg: str, ffprobe: str | None, video_path: Path, timestamp_seconds: float, output_path: Path) -> None:
    duration = media_duration_seconds(ffprobe, video_path)
    if duration:
        timestamp_seconds = min(timestamp_seconds, max(duration - 1.0, 0.0))
    commands = [
        [ffmpeg, "-y", "-i", str(video_path), "-ss", f"{max(timestamp_seconds, 0):.3f}", "-frames:v", "1", "-f", "image2", str(output_path)],
        [ffmpeg, "-y", "-i", str(video_path), "-frames:v", "1", "-f", "image2", str(output_path)],
    ]
    for command in commands:
        run(command)
        if output_path.exists():
            return
    raise RuntimeError(f"Failed to extract frame from {video_path}")


def clock_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, rem = divmod(total_seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}"
