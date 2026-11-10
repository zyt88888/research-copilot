"""学术 PDF 细粒度解析：版式、标题树、表格、公式、参考文献、扫描页 OCR。

输出结构统一为 dict，便于缓存为 JSON、直接写入 SQLite。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import statistics
from pathlib import Path

import fitz  # PyMuPDF

from ..settings import (CACHE_DIR, CHUNK_OVERLAP, CHUNK_SIZE, OCR_ENABLED,
                        OCR_LANG)
from ..utils import (CAPTION, MATH_HINT, REF_HEADING, bbox_union, chunk_text,
                     exact_content_hash, extract_year, is_math_line,
                     minhash_signature, norm_ws, safe_filename, snippet,
                     split_sentences, tokenize)

log = logging.getLogger(__name__)

ARXIV_ID = re.compile(r"arxiv[:\s]*([0-9]{4}\.[0-9]{4,5})(v\d+)?", re.I)
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
PAGE_NUM = re.compile(r"^\s*[-–]?\s*\d{1,4}\s*[-–]?\s*$")
# arXiv 水印 / 版权声明 / 日期行：字号往往和标题一样大，不应当成章节
WATERMARK = re.compile(
    r"(?i)(arxiv:\s*\d{4}\.\d{4,5}|\[cs\.|preprint|under review|"
    r"provided proper attribution|all rights reserved|"
    r"^\s*\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))"
)
OCR_MIN_CHARS = 40

_ocr_engine = None
_ocr_failed = False


# --------------------------------------------------------------------------
# OCR（可选依赖：paddleocr / pytesseract，任一存在即可）
# --------------------------------------------------------------------------
def _get_ocr():
    global _ocr_engine, _ocr_failed
    if _ocr_engine is not None or _ocr_failed:
        return _ocr_engine
    try:
        from paddleocr import PaddleOCR
        try:
            # PaddleOCR 3.x：参数改名（use_angle_cls → use_textline_orientation，show_log 移除）
            # enable_mkldnn=False：规避 PaddlePaddle 3.x 在部分 CPU 上
            # oneDNN 的 ConvertPirAttribute2RuntimeAttribute 崩溃
            engine = PaddleOCR(lang=OCR_LANG, use_doc_orientation_classify=False,
                               use_doc_unwarping=False, use_textline_orientation=False,
                               enable_mkldnn=False)
            _ocr_engine = ("paddle3", engine)
        except TypeError:
            # PaddleOCR 2.x 兼容写法
            engine = PaddleOCR(use_angle_cls=True, lang=OCR_LANG, show_log=False,
                               enable_mkldnn=False)
            _ocr_engine = ("paddle", engine)
        return _ocr_engine
    except Exception as exc:
        log.info("PaddleOCR 不可用，尝试 pytesseract：%s", exc)
    try:
        import pytesseract  # noqa: F401
        _ocr_engine = ("tesseract", None)
        return _ocr_engine
    except Exception:
        _ocr_failed = True
        log.warning("未安装 OCR 依赖（paddleocr / pytesseract），扫描页将标记为待识别")
        return None


def ocr_page(page: "fitz.Page") -> str | None:
    if not OCR_ENABLED:
        return None
    engine = _get_ocr()
    if not engine:
        return None
    try:
        pix = page.get_pixmap(dpi=200)
        if engine[0] == "paddle3":
            import numpy as np
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n)
            if pix.n == 4:  # 去掉 alpha 通道
                img = img[:, :, :3]
            lines = []
            for res in engine[1].predict(img) or []:
                texts = res.get("rec_texts") if hasattr(res, "get") else None
                if texts:
                    lines.extend(str(t) for t in texts)
            return "\n".join(x for x in lines if x)
        if engine[0] == "paddle":
            import numpy as np
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n)
            result = engine[1].ocr(img)
            lines = []
            for item in result or []:
                if item and isinstance(item, list):
                    for line in item:
                        if line and len(line) > 1:
                            lines.append(str(line[1]))
                elif item and isinstance(item, dict):
                    lines.append(str(item.get("rec_text", "")))
            return "\n".join(x for x in lines if x)
        from PIL import Image
        import pytesseract
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        # pytesseract 语言包命名与 PaddleOCR 不同：ch → chi_sim
        tess_lang = {"ch": "chi_sim", "zh": "chi_sim"}.get(OCR_LANG, "eng")
        return pytesseract.image_to_string(img, lang=tess_lang)
    except Exception as exc:  # OCR 引擎内部错误不能影响整篇解析
        log.warning("OCR 失败 page=%s: %s", page.number + 1, exc)
        return None


def ocr_image(img_path) -> str | None:
    """对一张图片文件（PNG/JPG）做 OCR，供图内文字识别复用。"""
    if not OCR_ENABLED:
        return None
    engine = _get_ocr()
    if not engine:
        return None
    try:
        import numpy as np
        data = np.fromfile(str(img_path), dtype=np.uint8)
        try:
            import cv2
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        except ImportError:
            from PIL import Image
            img = np.array(Image.open(str(img_path)).convert("RGB"))
        if img is None:
            return None
        if engine[0] == "paddle3":
            lines = []
            for res in engine[1].predict(img) or []:
                texts = res.get("rec_texts") if hasattr(res, "get") else None
                if texts:
                    lines.extend(str(t) for t in texts)
            return "\n".join(x for x in lines if x) or None
        if engine[0] == "paddle":
            lines = []
            for item in engine[1].ocr(img) or []:
                if item and isinstance(item, list):
                    for line in item:
                        if line and len(line) > 1:
                            lines.append(str(line[1]))
            return "\n".join(x for x in lines if x) or None
        import pytesseract
        tess_lang = {"ch": "chi_sim", "zh": "chi_sim"}.get(OCR_LANG, "eng")
        return pytesseract.image_to_string(img, lang=tess_lang) or None
    except Exception as exc:
        log.warning("图内 OCR 失败 %s: %s", img_path, exc)
        return None


# --------------------------------------------------------------------------
# 版式工具
# --------------------------------------------------------------------------
def _line_info(block: dict) -> dict:
    spans = [s for line in block.get("lines", []) for s in line.get("spans", [])]
    text = "".join(s.get("text", "") for s in spans)
    sizes = [s.get("size", 0) for s in spans if s.get("text", "").strip()]
    bold = any(s.get("flags", 0) & 16 for s in spans)
    return {
        "text": text.strip(),
        "size": max(sizes) if sizes else 0,
        "bold": bold,
        "bbox": list(block.get("bbox", [0, 0, 0, 0])),
    }


def _body_size(blocks: list[dict]) -> float:
    sizes = []
    for b in blocks:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            for s in line.get("spans", []):
                txt = s.get("text", "").strip()
                if txt:
                    sizes.extend([s.get("size", 10)] * max(1, min(len(txt) // 4, 10)))
    return statistics.median(sizes) if sizes else 10.0


def _order_blocks(blocks: list[dict], width: float) -> list[dict]:
    """双栏按栏内自上而下排序；整宽块作为分隔，避免两栏串行错乱。"""
    text_blocks = [b for b in blocks if b.get("type") == 0]
    mid = width / 2
    left = [b for b in text_blocks if b["bbox"][2] <= mid + 8]
    right = [b for b in text_blocks if b["bbox"][0] >= mid - 8]
    full = [b for b in text_blocks if b not in left and b not in right]
    two_col = len(left) >= 3 and len(right) >= 3
    if two_col:
        ordered = sorted(left, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        ordered += sorted(right, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        ordered = sorted(ordered, key=lambda b: (0 if b["bbox"][0] < mid else 1,
                                                 b["bbox"][1]))
    else:
        ordered = sorted(text_blocks, key=lambda b: (round(b["bbox"][1] / 12),
                                                     b["bbox"][0]))
    if full:
        # 整宽块（标题/摘要/跨栏图）优先按 y 插入到最接近的位置
        merged = ordered + full
        merged.sort(key=lambda b: (b["bbox"][1], 0 if b["bbox"][2] > width * .7 else 1))
        return merged
    return ordered


def _heading_level(size: float, body: float, size_rank: dict) -> int | None:
    if size < body * 1.14 or size > body * 4:
        return None
    return size_rank.get(round(size, 1), 3)


def _join_lines(lines: list[str]) -> str:
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if out.endswith("-") and len(line) > 1:
            out = out[:-1] + line
        else:
            out = f"{out} {line}".strip()
    return out


def _table_markdown(page: "fitz.Page") -> list[dict]:
    tables = []
    try:
        finder = page.find_tables()
        for tab in finder.tables:
            rows = tab.extract()
            if not rows or len(rows) < 2:
                continue
            rows = [[norm_ws(str(c)) if c is not None else "" for c in row]
                    for row in rows]
            header = rows[0]
            body = rows[1:]
            md = ["| " + " | ".join(header) + " |",
                  "| " + " | ".join(["---"] * len(header)) + " |"]
            md += ["| " + " | ".join(r) + " |" for r in body]
            text = "\n".join(md)
            if sum(len(t) for t in text.split()) < 40:
                continue
            tables.append({
                "text": text,
                "bbox": [round(v, 2) for v in tab.bbox],
                "kind": "table",
            })
    except Exception as exc:
        log.debug("table detect failed page=%s: %s", page.number + 1, exc)
    return tables


# --------------------------------------------------------------------------
# 参考文献解析
# --------------------------------------------------------------------------
def _looks_like_authors(seg: str) -> bool:
    return bool(re.search(r"(,|\band\b|&)", seg)) and len(seg.split()) <= 25 \
        and not seg.lower().startswith(("we ", "this ", "in this"))


def guess_title(raw: str) -> str:
    text = re.sub(r"^\s*[\[(]?\d+[\])]?\s*", "", raw or "")
    text = re.sub(r"https?://\S+", " ", text)
    text = norm_ws(text)
    quoted = re.search(r'[“"](.{10,220}?)[”"]', text)
    if quoted:
        return norm_ws(quoted.group(1))
    markers = ["arxiv preprint", "in proceedings", "proceedings of",
               "in advances", "journal of", "in acl", "in emnlp", "in neurips",
               "in iclr", "in icml", "corr,", "corr "]
    lower = text.lower()
    for marker in markers:
        idx = lower.find(marker)
        if idx > 15:
            head = text[:idx].strip(" .;,")
            segs = [s.strip() for s in re.split(r"\.\s+", head) if s.strip()]
            if len(segs) >= 2 and _looks_like_authors(segs[0]):
                return segs[1]
            if segs:
                return segs[-1]
    segs = [s.strip() for s in re.split(r"\.\s+", text) if s.strip()]
    for seg in segs[:3]:
        if 2 <= len(seg.split()) <= 25 and not _looks_like_authors(seg) \
                and not re.search(r"(19|20)\d{2}", seg):
            return seg
    return snippet(text, 160)


def split_references(raw_text: str) -> list[str]:
    raw_text = raw_text.replace("\r", "\n")
    raw_text = re.sub(r"\n{2,}", "\n", raw_text)
    parts = re.split(r"(?=\n?[\[(]\d{1,3}[\])]\s)", raw_text)
    entries = [norm_ws(p) for p in parts if p and len(norm_ws(p)) > 25]
    if len(entries) < 3:
        lines = [norm_ws(line) for line in raw_text.split("\n")]
        merged, buf = [], ""
        for line in lines:
            if not line:
                continue
            if re.match(r"^\d{1,3}\s", line) or re.match(r"^[A-Z][a-z]+,\s", line):
                if buf:
                    merged.append(buf)
                buf = line
            else:
                buf = f"{buf} {line}".strip()
        if buf:
            merged.append(buf)
        entries = [e for e in merged if len(e) > 25]
    out, seen = [], set()
    for e in entries:
        key = norm_ws(e)[:120].lower()
        if key and key not in seen:
            seen.add(key)
            out.append(e)
    return out[:400]


def parse_reference(raw: str) -> dict:
    arxiv = ARXIV_ID.search(raw or "")
    doi = DOI_RE.search(raw or "")
    return {
        "raw": raw,
        "title": guess_title(raw),
        "arxiv_id": f"{arxiv.group(1)}{arxiv.group(2) or ''}" if arxiv else None,
        "doi": doi.group(0).rstrip(" .;,/") if doi else None,
        "year": extract_year(raw),
    }


def _year_from_arxiv(arxiv_id: str | None) -> int | None:
    if not arxiv_id:
        return None
    m = re.match(r"([0-9]{2})([0-9]{2})", arxiv_id)
    if not m:
        return None
    yy = int(m.group(1))
    return 2000 + yy if yy < 90 else 1900 + yy


# --------------------------------------------------------------------------
# 主解析流程
# --------------------------------------------------------------------------
def _extract(path: Path, filename: str) -> dict:
    doc = fitz.open(path)
    meta = doc.metadata or {}
    pages_meta: list[dict] = []
    all_pages: list[list[dict]] = []
    page_texts: list[str] = []
    page_flags: dict[int, str] = {}
    body_sizes: list[float] = []
    ocr_first_page = ""   # 第 1 页 OCR 原文，供标题兜底

    raw_pages = []
    for page in doc:
        blocks = page.get_text("dict").get("blocks", [])
        raw_pages.append((page, blocks))
        body_sizes.append(_body_size(blocks))

    global_body = statistics.median([b for b in body_sizes if b > 0] or [10.0])

    # 标题层级：按字号聚类（只取正文 1.14 倍以上的字号）
    heading_sizes = set()
    for page, blocks in raw_pages:
        for b in blocks:
            if b.get("type") != 0:
                continue
            info = _line_info(b)
            if info["size"] >= global_body * 1.14 and info["text"]:
                heading_sizes.add(round(info["size"], 1))
    ranked = {size: min(i + 1, 3) for i, size in
              enumerate(sorted(heading_sizes, reverse=True))}

    section_stack: list[tuple[int, str]] = []
    toc: list[dict] = []
    chunks: list[dict] = []
    seq = 0
    references_started = False
    ref_parts: list[str] = []

    def section_path() -> str:
        return " > ".join(title for _, title in section_stack) or "Body"

    def add_chunk(text: str, page_no: int, bbox, kind: str, level: int = 0,
                  title: str | None = None):
        nonlocal seq
        text = norm_ws(text)
        if len(text) < 2:
            return None
        cid = f"c{seq:05d}"
        chunk = {
            "id": cid, "page": page_no, "bbox": [round(v, 2) for v in bbox],
            "section": section_path(), "level": level, "kind": kind,
            "text": text, "seq": seq,
        }
        if title:
            chunk["heading"] = title
        # 相邻重复块（同一页同一段被抽两次）直接跳过
        if chunks and chunks[-1]["kind"] == kind and chunks[-1]["text"] == text \
                and chunks[-1]["page"] == page_no:
            return None
        chunks.append(chunk)
        seq += 1
        return chunk

    for idx, (page, blocks) in enumerate(raw_pages, start=1):
        width, height = page.rect.width, page.rect.height
        pages_meta.append({"page": idx, "width": round(width, 2),
                           "height": round(height, 2)})
        raw_text = page.get_text("text")
        page_texts.append(raw_text)

        if len(raw_text.strip()) < OCR_MIN_CHARS:
            ocr_text = ocr_page(page)
            if ocr_text and len(ocr_text.strip()) >= OCR_MIN_CHARS:
                page_flags[idx] = "ocr"
                if idx == 1:
                    ocr_first_page = ocr_text
                chunk = add_chunk(ocr_text, idx, [0, 0, width, height], "ocr")
                if chunk:
                    chunk["section"] = section_path()
            else:
                page_flags[idx] = "needs_ocr"
            continue

        ordered = _order_blocks(blocks, width)
        # 表格独立提取（与文本块不冲突）
        tables = _table_markdown(page)
        table_inserted = False

        for block in ordered:
            info = _line_info(block)
            text = info["text"]
            bbox = info["bbox"]
            if not text:
                continue
            if PAGE_NUM.match(text) and info["size"] <= global_body:
                continue
            if (bbox[1] < height * 0.045 or bbox[3] > height * 0.955) \
                    and len(text) < 140 and not REF_HEADING.match(text):
                continue
            if not table_inserted and tables and bbox[1] > tables[0]["bbox"][1]:
                for t in tables:
                    add_chunk(t["text"], idx, t["bbox"], "table")
                table_inserted = True

            level = _heading_level(info["size"], global_body, ranked)
            if level and len(text) <= 180 and not text.endswith("."):
                title = norm_ws(text)
                # arXiv 水印、版权声明、日期行不是章节标题
                if WATERMARK.search(title):
                    continue
                # "3Model Architecture" -> "3 Model Architecture"
                title = re.sub(r"^(\d+\.?\d*)\s*([A-Za-z])", r"\1 \2", title)
                if REF_HEADING.match(title):
                    references_started = True
                while section_stack and section_stack[-1][0] >= level:
                    section_stack.pop()
                section_stack.append((level, title))
                toc.append({"title": title, "level": level, "page": idx,
                            "bbox": [round(v, 2) for v in bbox]})
                add_chunk(title, idx, bbox, "heading", level=level, title=title)
                continue

            if references_started:
                ref_parts.append(text)
                add_chunk(text, idx, bbox, "reference")
                continue

            if CAPTION.match(text):
                add_chunk(text, idx, bbox, "caption")
                continue
            if is_math_line(text):
                add_chunk(text, idx, bbox, "formula")
                continue

            # 作者/机构/邮箱行不属于正文，跳过
            if _is_meta_line(text):
                continue

            if len(text) <= CHUNK_SIZE:
                add_chunk(text, idx, bbox, "paragraph")
            else:
                sentences = split_sentences(text)
                parts = chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP, sentences)
                for part in parts:
                    add_chunk(part, idx, bbox, "paragraph")

            # 图像块：补充 figure 占位与图注
        for block in blocks:
            if block.get("type") != 1:
                continue
            ib = block.get("bbox", [0, 0, 0, 0])
            if (ib[2] - ib[0]) * (ib[3] - ib[1]) < 4000:
                continue
            caption = None
            for b in blocks:
                if b.get("type") != 0:
                    continue
                ib_info = _line_info(b)
                if CAPTION.match(ib_info["text"]) and \
                        ib_info["bbox"][1] - ib[3] <= 80 and ib_info["bbox"][1] >= ib[1]:
                    caption = ib_info["text"]
                    break
            add_chunk(caption or "[Figure]", idx, ib, "figure")

        if not table_inserted and tables:
            for t in tables:
                add_chunk(t["text"], idx, t["bbox"], "table")

    full_text = "\n".join(page_texts)
    first_page_text = page_texts[0] if page_texts else ""
    arxiv_match = ARXIV_ID.search(first_page_text) or ARXIV_ID.search(full_text[:4000])
    arxiv_id = f"{arxiv_match.group(1)}{arxiv_match.group(2) or ''}" if arxiv_match else None
    doi_match = DOI_RE.search(first_page_text[:4000])
    doi = doi_match.group(0).rstrip(" .;,/") if doi_match else None

    title = norm_ws(meta.get("title") or "")
    guessed = _guess_first_title(raw_pages, global_body)
    bad_title = (
        not title
        or title.lower() in {"untitled", "unknown"}
        or title.lower().startswith("arxiv:")
        or re.match(r"^\d{4}\.\d{4,5}", title)      # 纯 arXiv 编号
        or len(title) < 8
        or len(title) > 400
    )
    fallback_title = re.sub(r"\.pdf$", "", safe_filename(filename), flags=re.I)
    if bad_title:
        title = guessed or title or fallback_title
    # 如果 guessed 更像学术标题（更长 + 无 arXiv 编号），优先用它
    if guessed and len(guessed) > len(title) * 0.6 \
            and not re.match(r"(?i)^arxiv[:\s]?\d", guessed):
        title = guessed
    # 最后兜底：TOC 里第一个不是 arXiv/版权声明的条目（通常就是论文标题）
    if re.match(r"(?i)^arxiv[:\s]?\d", title) or "Provided proper attribution" in title:
        toc_candidate = ""
        for h in toc:
            htitle = norm_ws(h["title"])
            if len(htitle) >= 8 and not re.match(r"(?i)^arxiv[:\s]?\d", htitle) \
                    and "Provided proper attribution" not in htitle \
                    and not re.search(r"(?i)abstract|introduction|background|method|results|conclusion", htitle):
                toc_candidate = htitle
                break
        if toc_candidate:
            title = toc_candidate
    # 扫描件没有文字层：标题往往来自 PDF 元数据（可能是文件名）或文件名本身，不可靠。
    # 首页走了 OCR 时优先用 OCR 首行作标题，除非已有足够长的标题。
    if page_flags.get(1) == "ocr" and ocr_first_page:
        first_line = next((ln.strip() for ln in ocr_first_page.splitlines() if ln.strip()), "")
        if 6 <= len(first_line) <= 120 and (title == fallback_title or len(title) < 12):
            title = norm_ws(first_line)
    authors = _guess_authors(raw_pages, title, global_body)
    abstract = _guess_abstract(chunks)

    refs_raw = "\n".join(ref_parts)
    if not refs_raw:
        refs_raw = _tail_references(full_text)
    refs = [parse_reference(r) for r in split_references(refs_raw)]

    year = _year_from_arxiv(arxiv_id)
    if not year:
        year = extract_year(first_page_text[:1500])
    if not year and meta.get("creationDate"):
        m = re.search(r"D:(\d{4})", meta["creationDate"])
        year = int(m.group(1)) if m else None

    content_hash = exact_content_hash(full_text[:200000])
    signature = minhash_signature(full_text[:150000])

    return {
        "title": title[:400],
        "authors": authors,
        "year": year,
        "venue": _guess_venue(first_page_text),
        "abstract": snippet(abstract, 2400),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "filename": filename,
        "num_pages": len(pages_meta),
        "pages": pages_meta,
        "page_flags": page_flags,
        "toc": toc,
        "chunks": chunks,
        "refs": refs,
        "content_hash": content_hash,
        "minhash": signature,
        "full_text_chars": len(full_text),
    }


def _guess_first_title(raw_pages, body: float) -> str:
    arxiv_line = re.compile(r"(?i)^arXiv[:\s]?\d{4}\.\d{4,5}.*$")
    dateish = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b.*\d{4}", re.I)
    for page, blocks in raw_pages[:1]:
        # 收集所有候选（正文 1.3 倍字号以上的大文本块），排除 arXiv 编号/日期
        candidates = []
        for b in blocks:
            if b.get("type") != 0:
                continue
            info = _line_info(b)
            text = info["text"]
            if not text or len(text) < 8:
                continue
            if info["size"] < body * 1.3:
                continue
            if arxiv_line.match(text):
                continue
            if dateish.search(text) or re.match(r"^\d{1,2}\s+[A-Z][a-z]+\s+\d{4}$", text):
                continue
            if text.startswith("e-mail") or "@" in text[:40]:
                continue
            candidates.append(info)
        if not candidates:
            continue
        # 最长且字号最大的作为标题
        candidates.sort(key=lambda c: (len(c["text"]), c["size"]), reverse=True)
        return candidates[0]["text"]
    return ""


BAD_AUTHOR = re.compile(
    r"(?i)(provided proper attribution|hereby grants|all rights reserved|copyright|"
    r"license|permission|arxiv|doi:|@|http|www\.|university|institute|department|"
    r"laborator|labs?|inc\.|brain|google|research|toronto|corresponding author|"
    r"scholarly works|reproduce the tables|figures of the following)"
)


# 只用于过滤“非正文”块（作者行/授权声明/邮箱），不含 google、research 这类会误伤正文的词
META_LINE = re.compile(
    r"(?i)(arxiv:\s*\d{4}\.\d{4,5}|\[cs\.|provided proper attribution|hereby grants|"
    r"all rights reserved|copyright|license|scholarly works|reproduce the tables|"
    r"figures of the following|@|corresponding author)"
)


def _is_meta_line(text: str) -> bool:
    t = text.strip()
    # 脚注符号开头、会议页脚
    if re.match(r"^[∗†‡*]", t) or re.match(
            r"(?i)^(\d{1,2}(st|nd|rd|th)\s+conference|proceedings\s+of)", t):
        return True
    if len(t) > 140:
        return False
    if META_LINE.search(t):
        return True
    # 没有句末标点、词数很少的短行（如 'Jakob Uszkoreit∗Google Research'）
    if len(t.split()) <= 5 and not re.search(r"[.。!?；;:]", t):
        return True
    return False


def _looks_like_person(name: str) -> bool:
    name = re.sub(r"[∗†‡*]|\d", "", name).strip()
    tokens = [t for t in name.split() if t.strip(".")]
    if not 2 <= len(tokens) <= 4:
        return False
    caps = sum(1 for t in tokens if t[:1].isupper())
    return caps >= max(2, len(tokens) - 1)


def _guess_authors(raw_pages, title: str, body: float) -> list[str]:
    """两种版式都支持：
    1) 一行逗号分隔多位作者（ICML/ACL 常见）
    2) 每位作者独立成块、下方紧跟机构邮箱（NeurIPS/arXiv 常见，如 Transformer 论文）
    """
    if not raw_pages:
        return []
    blocks = [b for b in raw_pages[0][1] if b.get("type") == 0]
    infos = [_line_info(b) for b in blocks]

    start = 0
    for i, info in enumerate(infos):
        if info["text"].strip() == title.strip():
            start = i + 1
            break

    names: list[str] = []
    for info in infos[start:start + 30]:
        text = info["text"].strip()
        if not text:
            continue
        if re.match(r"(?i)^(abstract|摘要)", text):
            break
        if not body * 0.9 <= info["size"] <= body * 1.35:
            continue
        if BAD_AUTHOR.search(text) and "@" not in text:
            continue

        # 作者块常与机构/邮箱粘连：'Niki Parmar∗Google Researchnikip@google.com'
        cand = text.split("@")[0]
        cand = re.split(
            r"(?=(?:Google|University|Institute|Department|Microsoft|Meta|Allen|"
            r"IBM|NVIDIA|Stanford|Berkeley|Research|Brain))", cand
        )[0]
        cand = re.sub(r"[∗†‡*]", "", cand).strip()
        # 无 @ 的邮箱残留：'Illia Polosukhinillia.polosukhin'
        m = re.search(r"([A-Za-z]+)\.([a-z]{2,})", cand)
        if m:
            last = m.group(2).capitalize()
            head = cand[:m.start(1)].strip()
            if head and last.lower() in m.group(1).lower():
                cand = f"{head} {last}".strip()

        parts = [p.strip() for p in re.split(r",|\band\b|;|、", cand) if p.strip()]
        keep = [p for p in parts if _looks_like_person(p)]
        if len(parts) >= 2 and len(keep) == len(parts):
            names.extend(keep)          # 一行多名作者
            continue
        if len(parts) == 1 and _looks_like_person(cand):
            names.append(cand)
            continue
        if len(text) > 60:
            break

    seen, out = set(), []
    for n in names:
        n = norm_ws(n)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out[:20]


def _guess_abstract(chunks: list[dict]) -> str:
    abstract_chunks = [c for c in chunks if c["kind"] == "paragraph" and
                       re.search(r"abstract|摘要", c.get("section", ""), re.I)]
    if not abstract_chunks:
        abstract_chunks = [c for c in chunks if c["kind"] == "paragraph"][:2]
    return " ".join(c["text"] for c in abstract_chunks)[:3000]


def _guess_venue(text: str) -> str:
    m = re.search(r"(Proceedings of [^\n,.]{3,80}|Advances in [^\n,.]{3,80}|"
                  r"(?:NeurIPS|ICLR|ICML|ACL|EMNLP|CVPR|ICCV|AAAI)\s*20\d{2})", text or "")
    return norm_ws(m.group(0))[:160] if m else ""


def _tail_references(full_text: str) -> str:
    m = re.search(r"\n\s*(References|Bibliography|参考文献)\s*\n", full_text, re.I)
    return full_text[m.end():] if m else ""


def parse_pdf(path: Path, filename: str | None = None) -> dict:
    """带磁盘缓存：同一文件重复上传（去重确认场景）不重复解析。"""
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cache_file = CACHE_DIR / f"{digest}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text("utf-8"))
            data["filename"] = filename or data.get("filename")
            return data
        except Exception:
            pass
    data = _extract(path, filename or path.name)
    data["file_sha256"] = digest
    try:
        cache_file.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    except Exception as exc:
        log.warning("parse cache write failed: %s", exc)
    return data


def text_of_chunks(chunks: list[dict], kinds=("paragraph", "ocr", "table")) -> str:
    return "\n".join(c["text"] for c in chunks if c["kind"] in kinds)
