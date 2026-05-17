"""doc-generation-pipeline entrypoint.

AST-based API doc generator. Extracts docstrings, signatures, classes.
Renders markdown. Pure Python.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any


def _extract_docstring(node: ast.AST) -> str:
    """Return the docstring of a node, or empty string."""
    doc = ast.get_docstring(node)
    return doc or ""


def _format_signature(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Format a function signature from AST."""
    args_parts: list[str] = []
    for arg in func.args.args:
        if arg.arg in ("self", "cls"):
            continue
        hint = ast.unparse(arg.annotation) if arg.annotation else "Any"
        args_parts.append(f"{arg.arg}: {hint}")
    for arg in func.args.kwonlyargs:
        hint = ast.unparse(arg.annotation) if arg.annotation else "Any"
        args_parts.append(f"{arg.arg}: {hint}")
    if func.args.vararg:
        args_parts.append(f"*{func.args.vararg.arg}")
    if func.args.kwarg:
        args_parts.append(f"**{func.args.kwarg.arg}")

    ret = ""
    if func.returns:
        ret = f" -> {ast.unparse(func.returns)}"

    return f"({', '.join(args_parts)}){ret}"


def _render_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Render markdown for a single function."""
    lines: list[str] = []
    sig = _format_signature(func)
    lines.append(f"### `{func.name}{sig}`")
    lines.append("")
    doc = _extract_docstring(func)
    if doc:
        lines.append(doc)
        lines.append("")
    else:
        lines.append("*(no docstring)*")
        lines.append("")
    return "\n".join(lines)


def _render_class(cls: ast.ClassDef) -> str:
    """Render markdown for a class and its public methods."""
    lines: list[str] = []
    bases = ", ".join(ast.unparse(b) for b in cls.bases) if cls.bases else ""
    header = f"class {cls.name}"
    if bases:
        header += f"({bases})"
    lines.append(f"### `{header}`")
    lines.append("")
    doc = _extract_docstring(cls)
    if doc:
        lines.append(doc)
        lines.append("")

    methods = [
        node for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]
    if methods:
        lines.append("**Methods:**")
        lines.append("")
        for method in methods:
            sig = _format_signature(method)
            lines.append(f"- `{method.name}{sig}`")
            mdoc = _extract_docstring(method)
            if mdoc:
                first_line = mdoc.splitlines()[0]
                lines.append(f"  — {first_line}")
        lines.append("")

    # TypedDict / dataclass fields
    fields: list[str] = []
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            hint = ast.unparse(node.annotation) if node.annotation else "Any"
            fields.append(f"- `{node.target.id}: {hint}`")
    if fields:
        lines.append("**Fields:**")
        lines.append("")
        lines.extend(fields)
        lines.append("")

    return "\n".join(lines)


def _render_module(tree: ast.AST, module_name: str, file_path: str) -> str:
    """Render full markdown document."""
    lines: list[str] = []
    lines.append(f"# API Documentation: `{module_name}`")
    lines.append("")
    lines.append(f"*Source: `{file_path}`*")
    lines.append("")

    mod_doc = _extract_docstring(tree)
    if mod_doc:
        lines.append("## Overview")
        lines.append("")
        lines.append(mod_doc)
        lines.append("")

    functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    if functions:
        lines.append("## Functions")
        lines.append("")
        for func in functions:
            lines.append(_render_function(func))

    if classes:
        lines.append("## Classes")
        lines.append("")
        for cls in classes:
            lines.append(_render_class(cls))

    if not functions and not classes:
        lines.append("*No public functions or classes found.*")

    return "\n".join(lines)


async def run_openagent_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute the doc generation pipeline."""
    target_path = payload.get("target_path")
    output_path = payload.get("output_path")

    if not target_path:
        raise ValueError("doc-generation-pipeline: payload must include 'target_path'")
    if not output_path:
        raise ValueError("doc-generation-pipeline: payload must include 'output_path'")

    target = Path(target_path)
    if not target.exists():
        raise FileNotFoundError(f"target_path does not exist: {target_path}")

    source = target.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"Failed to parse {target_path}: {exc}") from exc

    module_name = target.stem
    doc = _render_module(tree, module_name, str(target))

    output_p = Path(output_path)
    output_p.parent.mkdir(parents=True, exist_ok=True)
    output_p.write_text(doc, encoding="utf-8")

    functions = sum(
        1 for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    )
    classes = sum(1 for node in tree.body if isinstance(node, ast.ClassDef))

    return {
        "doc_path": str(output_p),
        "functions_documented": functions,
        "classes_documented": classes,
        "module_docstring": _extract_docstring(tree)[:200],
    }
