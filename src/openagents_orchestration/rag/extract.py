"""提取 — 版式探测 + 可插拔 TextExtractor(弹性 ingestion)。

照《工业级的 RAG 优化选型》「garbage in garbage out」,解析是质量瓶颈,须独立可替换。
PlainTextExtractor 立即可用(stdlib);PdfTextExtractor 用 pypdf(可选,import 失败降级),
并提供文本层探测(无文本层 = 扫描件,需 OCR,此处仅占位不实现)。新增提取后端 =
加一个 TextExtractor 实现,不动 pipeline。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Protocol, runtime_checkable
from xml.etree import ElementTree as ET

# (page_no, text);纯文本 page_no=None
PageText = tuple[int | None, str]

_TEXT_LAYER_MIN_CHARS = 20
_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_XLSX_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@runtime_checkable
class TextExtractor(Protocol):
    """从文件提取文本。返回 (页码, 文本) 列表,纯文本页码为 None。"""

    def extract(self, path: str | Path) -> list[PageText]: ...


class PlainTextExtractor:
    """.txt / .md / 纯文本:整文件作为一页。"""

    def extract(self, path: str | Path) -> list[PageText]:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return [(None, text)]


class DocxTextExtractor:
    """DOCX 文字版直提(stdlib zip + XML)。"""

    def extract(self, path: str | Path) -> list[PageText]:
        path = Path(path)
        with zipfile.ZipFile(path) as zf:
            try:
                xml = zf.read("word/document.xml")
            except KeyError as exc:
                raise ValueError(f"invalid docx archive: missing word/document.xml: {path}") from exc
        root = ET.fromstring(xml)
        paras: list[str] = []
        for para in root.findall(".//w:p", _DOCX_NS):
            text = "".join(node.text or "" for node in para.findall(".//w:t", _DOCX_NS))
            if text.strip():
                paras.append(text)
        return [(None, "\n".join(paras))]


class XlsxTextExtractor:
    """XLSX 文字版直提(stdlib zip + XML)。"""

    def extract(self, path: str | Path) -> list[PageText]:
        path = Path(path)
        with zipfile.ZipFile(path) as zf:
            shared = self._shared_strings(zf)
            sheets = sorted(
                name
                for name in zf.namelist()
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
            pages: list[PageText] = []
            for idx, name in enumerate(sheets, start=1):
                try:
                    root = ET.fromstring(zf.read(name))
                except KeyError:
                    continue
                rows: list[str] = []
                for row in root.findall(".//x:row", _XLSX_NS):
                    cells: list[str] = []
                    for cell in row.findall("x:c", _XLSX_NS):
                        cells.append(_cell_text(cell, shared))
                    row_text = " | ".join(part for part in cells if part)
                    if row_text:
                        rows.append(row_text)
                page_text = "\n".join(rows).strip()
                if page_text:
                    pages.append((idx, page_text))
            return pages

    def _shared_strings(self, zf: zipfile.ZipFile) -> list[str]:
        try:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        except KeyError:
            return []
        return ["".join(node.text or "" for node in si.findall(".//x:t", _XLSX_NS)) for si in root.findall(".//x:si", _XLSX_NS)]


class PdfTextExtractor:
    """PDF 文字版直提(pypdf)。pypdf 未装 → 构造即降级报错(X-03 不吞)。"""

    def __init__(self) -> None:
        try:
            import pypdf  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "PdfTextExtractor requires pypdf: pip install pypdf"
                "(或先用 PlainTextExtractor / 系统 pdftotext)"
            ) from exc
        self._pypdf = pypdf

    def extract(self, path: str | Path) -> list[PageText]:
        reader = self._pypdf.PdfReader(str(path))
        try:
            pages: list[PageText] = []
            for i, page in enumerate(reader.pages):
                text = (page.extract_text() or "").strip()
                pages.append((i + 1, text))
            return pages
        finally:
            reader.close()

    def has_text_layer(self, path: str | Path) -> bool:
        """版式探测:是否有可提取文本层(无 → 扫描件,需 OCR)。"""
        return any(len(t) > _TEXT_LAYER_MIN_CHARS for _, t in self.extract(path))


def extract(path: str | Path, extractor: TextExtractor | None = None) -> list[PageText]:
    """按扩展名选默认后端:.docx/.xlsx/.pdf → 对应 extractor,其余 → PlainTextExtractor。"""
    path = Path(path)
    if extractor is not None:
        return extractor.extract(path)
    if path.suffix.lower() == ".docx":
        return DocxTextExtractor().extract(path)
    if path.suffix.lower() == ".xlsx":
        return XlsxTextExtractor().extract(path)
    if path.suffix.lower() == ".pdf":
        return PdfTextExtractor().extract(path)
    return PlainTextExtractor().extract(path)


def extract_text(text: str) -> list[PageText]:
    """直接接收文本(不走文件),包成单页。"""
    return [(None, text)]


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    t = cell.attrib.get("t")
    if t == "s":
        value = cell.find("x:v", _XLSX_NS)
        if value is None or value.text is None:
            return ""
        idx = int(value.text)
        return shared[idx] if 0 <= idx < len(shared) else ""
    if t == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", _XLSX_NS))
    value = cell.find("x:v", _XLSX_NS)
    return value.text.strip() if value is not None and value.text else ""
