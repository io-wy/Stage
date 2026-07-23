"""文件抽取测试 — docx / xlsx 真实文本抽取。"""

from __future__ import annotations

import zipfile
from pathlib import Path

from openagents_orchestration.rag.extract import DocxTextExtractor, XlsxTextExtractor
from openagents_orchestration.rag.pipeline import build_pipeline
from openagents_orchestration.rag.types import DocMetadata

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
X_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _write_docx(path: Path, *paragraphs: str) -> None:
    body = "".join(
        f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>' for text in paragraphs
    )
    xml = f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)


def _write_xlsx(path: Path) -> None:
    shared = (
        f'<sst xmlns="{X_NS}">'
        "<si><t>部门</t></si><si><t>软件研发部</t></si>"
        "<si><t>方向</t></si><si><t>算法组</t></si>"
        "</sst>"
    )
    sheet = (
        f'<worksheet xmlns="{X_NS}"><sheetData><row r="1">'
        '<c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
        '</row><row r="2">'
        '<c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c>'
        "</row></sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)


def test_docx_extractor_reads_paragraphs(tmp_path):
    path = tmp_path / "club.docx"
    _write_docx(path, "SAST 成员守则", "不得泄露内部机密信息")

    pages = DocxTextExtractor().extract(path)

    assert pages == [(None, "SAST 成员守则\n不得泄露内部机密信息")]


def test_xlsx_extractor_reads_shared_strings(tmp_path):
    path = tmp_path / "club.xlsx"
    _write_xlsx(path)

    pages = XlsxTextExtractor().extract(path)

    assert pages == [(1, "部门 | 软件研发部\n方向 | 算法组")]


async def test_pipeline_ingests_docx_path(tmp_path):
    path = tmp_path / "rules.docx"
    _write_docx(path, "SAST 成员守则", "场地与财产应当妥善维护")

    pipe = build_pipeline()
    n = await pipe.ingest(path, DocMetadata(tags=["rules"]))
    res = await pipe.query("成员 守则", top_k=1)

    assert n >= 1
    assert res.passages[0].metadata.source == str(path)
