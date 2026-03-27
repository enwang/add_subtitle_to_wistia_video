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
    """Summarize a single transcript chunk with Claude Haiku (map phase)."""
    prompt = (
        f"以下是一段粤语/普通话财经视频的第 {chunk_index}/{total_chunks} 段字幕"
        f"（时间 {_fmt_time(start)} - {_fmt_time(end)}）。\n\n"
        "请用简体中文总结这段内容，包括：\n"
        "1. 主要讨论的话题、主题或股票方向\n"
        "2. 提到的所有股票代码和公司名称（尽量完整）\n"
        "3. 具体的数据指标（涨跌幅、价格区间、筛选条件等）\n"
        "4. 讲者的核心观点和分析逻辑\n\n"
        f"字幕内容：\n{text}\n\n"
        "请输出结构化段落总结（用完整中文句子，不要用bullet points）。"
    )
    import anthropic as _anthropic
    response = _anthropic.Anthropic().messages.create(
        model="claude-haiku-4-5-20251001",  # cheapest model
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    return response.content[0].text  # type: ignore[union-attr]


def _reduce_summary(client: object, chunk_summaries: list[str], all_tickers: list[str], total_duration_seconds: float = 0) -> dict:
    """Synthesize chunk summaries into a structured final summary (reduce phase).
    Uses tool use to guarantee schema-valid JSON output."""
    summaries_text = "\n\n".join(f"【第{i + 1}段总结】\n{s}" for i, s in enumerate(chunk_summaries))
    tickers_str = "、".join(all_tickers[:20]) if all_tickers else "（未检测到）"
    if total_duration_seconds >= 3600:
        duration_str = f"约{total_duration_seconds / 3600:.1f}小时"
    elif total_duration_seconds >= 60:
        duration_str = f"约{int(total_duration_seconds / 60)}分钟"
    else:
        duration_str = f"{len(chunk_summaries)}段"

    prompt = (
        f"以下是一个{duration_str}粤语财经视频的分段总结，请整合成完整详细的视频摘要，调用 write_summary 工具输出。\n\n"
        f"视频中提到的股票代码：{tickers_str}\n\n"
        f"分段总结：\n{summaries_text}\n\n"
        "输出要求（所有文字用简体中文，内容必须具体，基于实际视频内容，不要泛泛而谈）：\n"
        "- intro_paragraphs：3段，总结视频核心目标、主要发现和实用价值，每段3句以上\n"
        "- method_paragraphs：4-5段，详细说明筛选标准，必须包含视频中的具体数字和条件，每段3句以上\n"
        "- themes：列出所有重要主题（通常3-6个），每个主题写4段详细说明，examples填代表股票代码（仅大写英文字母）\n"
        "- stock_analyses：对视频中讨论的每支重要股票，写一段分析（包括股票代码、公司名、讲者的观点和逻辑）\n"
        "- key_data_points：列出视频中提到的所有具体数据，如涨跌幅、百分比、筛选条件数值等（每条15字左右）\n"
        "- market_insights：列出10-15条视频中最有价值的市场洞察，每条约20字\n"
        "- keywords：15-25个关键词，包括股票代码、板块名称、核心概念（每项1-4个字）\n"
        "- one_line_takeaway：一句话（20字以内）总结视频最核心的信息\n"
        "- closing_paragraphs：2-3段，讲者的最终建议和注意事项"
    )

    _SUMMARY_TOOL = {
        "name": "write_summary",
        "description": "Output the comprehensive structured video summary",
        "input_schema": {
            "type": "object",
            "properties": {
                "intro_paragraphs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3 detailed paragraphs summarising key conclusions",
                },
                "method_paragraphs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "4-5 paragraphs on screening methodology with specific numbers",
                },
                "themes": {
                    "type": "array",
                    "description": "All major themes discussed in the video (3-6 themes)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Short theme title in Chinese"},
                            "paragraphs": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "4 detailed paragraphs about this theme",
                            },
                            "examples": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Stock ticker symbols (uppercase only) for this theme",
                            },
                        },
                        "required": ["title", "paragraphs", "examples"],
                    },
                },
                "stock_analyses": {
                    "type": "array",
                    "description": "One analysis paragraph per major stock discussed",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string", "description": "Stock ticker symbol"},
                            "analysis": {"type": "string", "description": "1-2 sentence analysis of why the speaker highlighted this stock"},
                        },
                        "required": ["ticker", "analysis"],
                    },
                },
                "key_data_points": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "10-20 specific data points mentioned: percentages, conditions, price levels (each ~15 chars)",
                },
                "market_insights": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "10-15 key market insights from the video (each ~20 chars)",
                },
                        "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "15-25 keywords: stock tickers, sector names, key concepts mentioned (each 1-4 words)",
                },
                "one_line_takeaway": {
                    "type": "string",
                    "description": "Single sentence (≤20 words in Chinese) capturing the most important message of the video",
                },
                "closing_paragraphs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-3 closing paragraphs with final advice",
                },
            },
            "required": ["intro_paragraphs", "method_paragraphs", "themes", "stock_analyses", "key_data_points", "market_insights", "keywords", "one_line_takeaway", "closing_paragraphs"],
        },
    }

    import anthropic as _anthropic
    response = _anthropic.Anthropic().messages.create(
        model="claude-haiku-4-5-20251001",  # cheapest model
        max_tokens=6000,
        tools=[_SUMMARY_TOOL],
        tool_choice={"type": "tool", "name": "write_summary"},  # force tool call → guaranteed valid JSON
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "write_summary":  # type: ignore[union-attr]
            return block.input  # type: ignore[union-attr]
    raise ValueError("No write_summary tool_use block found in response")


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


def _build_llm_blocks(llm_summary: dict, wrap_width: int) -> list[list[str]]:
    """Build page blocks from LLM-generated summary dict."""
    blocks: list[list[str]] = []

    def _block(heading: str, paragraphs: list[str]) -> list[str]:
        block = [heading, ""]
        for para in paragraphs:
            if para.strip():
                block.extend(wrap_cjk_text(para, wrap_width))
                block.append("")
        return block[:-1] if block and block[-1] == "" else block

    def _bullet_block(heading: str, items: list[str], prefix: str = "• ") -> list[str]:
        block = [heading, ""]
        for item in items:
            if item.strip():
                block.extend(wrap_cjk_text(f"{prefix}{item.strip()}", wrap_width))
                block.append("")
        return block[:-1] if block and block[-1] == "" else block

    # One-line takeaway as a standalone prominent block
    takeaway = (llm_summary.get("one_line_takeaway") or "").strip()
    if takeaway:
        blocks.append(["一句话总结", "", *wrap_cjk_text(takeaway, wrap_width)])

    overview_paras = llm_summary.get("intro_paragraphs") or []
    if overview_paras:
        blocks.append(_block("核心结论", overview_paras))

    method_paras = llm_summary.get("method_paragraphs") or []
    if method_paras:
        blocks.append(_block("筛选框架", method_paras))

    # Key data points — rendered as compact bullets
    data_points = llm_summary.get("key_data_points") or []
    if data_points:
        blocks.append(_bullet_block("关键数据", data_points))

    themes = llm_summary.get("themes") or []
    if themes:
        theme_count = len(themes)
        theme_label = "五个主题" if theme_count == 5 else f"{theme_count}个主题"
        intro_text = f"以下{theme_label}，是讲者把筛选结果归纳后认为最值得跟踪的方向。"
        theme_intro = [theme_label, ""]
        theme_intro.extend(wrap_cjk_text(intro_text, wrap_width))
        blocks.append(theme_intro)
        for index, theme in enumerate(themes, start=1):
            if isinstance(theme, str):
                blocks.append(_block(f"{index}. 主题", [theme]))
                continue
            title = simplify_summary_text(theme.get("title") or "主题")
            paras = list(theme.get("paragraphs") or [])
            examples = theme.get("examples") or []
            if examples:
                ticker_str = "、".join(str(e) for e in examples[:5])
                if paras:
                    paras[-1] = paras[-1].rstrip("。") + f"。相关股票：{ticker_str}。"
                else:
                    paras.append(f"相关股票：{ticker_str}。")
            blocks.append(_block(f"{index}. {title}", paras))

    # Per-stock analysis
    stock_analyses = llm_summary.get("stock_analyses") or []
    if stock_analyses:
        stock_block = ["重点股票分析", ""]
        for item in stock_analyses:
            if isinstance(item, str):
                stock_block.extend(wrap_cjk_text(item, wrap_width))
                stock_block.append("")
                continue
            ticker = (item.get("ticker") or "").strip()
            analysis = (item.get("analysis") or "").strip()
            if ticker and analysis:
                combined = f"[{ticker}] {analysis}"
                stock_block.extend(wrap_cjk_text(combined, wrap_width))
                stock_block.append("")
        if len(stock_block) > 2:
            blocks.append(stock_block[:-1] if stock_block[-1] == "" else stock_block)

    # Market insights — bullet list
    insights = llm_summary.get("market_insights") or []
    if insights:
        blocks.append(_bullet_block("市场洞察", insights))

    # Keywords — comma-separated compact list
    keywords = llm_summary.get("keywords") or []
    if keywords:
        kw_text = "  ".join(keywords)
        blocks.append(["关键词", "", *wrap_cjk_text(kw_text, wrap_width)])

    closing_paras = llm_summary.get("closing_paragraphs") or []
    if closing_paras:
        blocks.append(_block("最后的用法", closing_paras))
    elif not blocks:
        blocks.append(["未能生成摘要。"])

    return blocks


def line_style(line: str) -> str:
    simplified = simplify_summary_text(line).strip()
    if not simplified:
        return "Spacer"
    if simplified in {"核心结论", "筛选框架", "五个主题", "核心主题", "最后的用法", "关键数据", "重点股票分析", "市场洞察", "一句话总结", "关键词"}:
        return "Heading"
    if re.match(r"^\d+个主题$", simplified):
        return "Heading"
    if re.match(r"^\d+\.\s", simplified):
        return "Subheading"
    return "Body"


def line_height(line: str) -> int:
    style = line_style(line)
    if style == "Spacer":
        return 26
    if style == "Heading":
        return 66
    if style == "Subheading":
        return 56
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
