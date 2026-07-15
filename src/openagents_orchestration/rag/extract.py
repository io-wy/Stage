"""提取 — 版式探测 + 可插拔 TextExtractor(弹性 ingestion)。

照《工业级的 RAG 优化选型》「garbage in garbage out」,解析是质量瓶颈,须独立可替换。
PlainTextExtractor 立即可用(stdlib);PdfTextExtractor 用 pypdf(可选,import 失败降级),
并提供文本层探测(无文本层 = 扫描件,需 OCR,此处仅占位不实现)。新增提取后端 =
加一个 TextExtractor 实现,不动 pipeline。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

# (page_no, text);纯文本 page_no=None
PageText = tuple[int | None, str]

_TEXT_LAYER_MIN_CHARS = 20


@runtime_checkable
class TextExtractor(Protocol):
    """从文件提取文本。返回 (页码, 文本) 列表,纯文本页码为 None。"""

    def extract(self, path: str | Path) -> list[PageText]: ...


class PlainTextExtractor:
    """.txt / .md / 纯文本:整文件作为一页。"""

    def extract(self, path: str | Path) -> list[PageText]:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return [(None, text)]


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
    """按扩展名选默认后端:.pdf → PdfTextExtractor,其余 → PlainTextExtractor。"""
    path = Path(path)
    if extractor is not None:
        return extractor.extract(path)
    if path.suffix.lower() == ".pdf":
        return PdfTextExtractor().extract(path)
    return PlainTextExtractor().extract(path)


def extract_text(text: str) -> list[PageText]:
    """直接接收文本(不走文件),包成单页。"""
    return [(None, text)]
