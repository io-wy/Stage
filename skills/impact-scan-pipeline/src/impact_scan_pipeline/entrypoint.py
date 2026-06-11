"""Impact scan pipeline entrypoint.

Scans a Python project for references to changed symbols/files,
producing a structured impact report to prevent incomplete changes.
"""

from __future__ import annotations

import ast
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class _Reference:
    file: str
    line: int
    context: str
    ref_type: str  # import | call | inheritance | string_ref


@dataclass
class _ImpactResult:
    symbol: str
    direct_refs: list[_Reference] = field(default_factory=list)
    indirect_refs: list[_Reference] = field(default_factory=list)


_CODE_EXTENSIONS = {".py"}


def _iter_code_files(root: Path, max_files: int = 500) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if len(found) >= max_files:
            break
        if path.is_file() and path.suffix in _CODE_EXTENSIONS:
            # Skip common non-project dirs
            parts = set(path.parts)
            if parts & {"__pycache__", ".git", ".venv", "venv", ".eval_work", ".artifacts"}:
                continue
            found.append(path)
    return found


def _grep_refs(project_root: Path, symbol: str) -> list[_Reference]:
    """Fast grep-based reference search."""
    refs: list[_Reference] = []
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py", symbol, str(project_root)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return refs

    for line in result.stdout.splitlines():
        # Format: file:line:content
        if ":" not in line:
            continue
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        file_path, line_no_str, content = parts
        try:
            line_no = int(line_no_str)
        except ValueError:
            continue

        # Skip comments
        stripped = content.strip()
        if stripped.startswith("#"):
            continue

        # Classify reference type
        ref_type = "string_ref"
        if "import" in stripped or "from " in stripped:
            ref_type = "import"
        elif "def " in stripped or "class " in stripped:
            # Definition site, skip
            if symbol in stripped.split("(")[0]:
                continue
        elif "(" in content and symbol in content.split("(")[0]:
            ref_type = "call"
        elif "class " in content and symbol in content:
            ref_type = "inheritance"

        refs.append(_Reference(
            file=file_path,
            line=line_no,
            context=content.strip()[:100],
            ref_type=ref_type,
        ))
    return refs


def _ast_imports_and_calls(path: Path) -> dict[str, list[_Reference]]:
    """AST-based precise scan for a single file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except Exception:
        return {}

    lines = text.splitlines()
    result: dict[str, list[_Reference]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                if "." in alias.name:
                    name = alias.name.split(".")[-1]
                _add_ref(result, name, path, node, lines, "import")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                name = alias.asname or alias.name
                _add_ref(result, name, path, node, lines, f"import_from({module})")
        elif isinstance(node, ast.Call):
            func_name = _get_call_name(node.func)
            if func_name:
                _add_ref(result, func_name, path, node, lines, "call")
        elif isinstance(node, ast.Attribute):
            # obj.method — record method name
            if isinstance(node.value, ast.Name):
                full = f"{node.value.attr}.{node.attr}" if hasattr(node.value, "attr") else f"{node.value.id}.{node.attr}"
                _add_ref(result, node.attr, path, node, lines, "attribute_access")

    return result


def _add_ref(
    result: dict[str, list[_Reference]],
    name: str,
    path: Path,
    node: ast.AST,
    lines: list[str],
    ref_type: str,
) -> None:
    try:
        line_no = node.lineno or 1
        context = lines[line_no - 1].strip()[:100] if line_no <= len(lines) else ""
    except Exception:
        line_no = 1
        context = ""
    result.setdefault(name, []).append(_Reference(
        file=str(path),
        line=line_no,
        context=context,
        ref_type=ref_type,
    ))


def _get_call_name(node: ast.AST) -> str | None:
    """Extract the callable name from an ast.Call node."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        parts = []
        current: ast.AST = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _render_report(results: list[_ImpactResult], target_path: str, symbols: list[str]) -> str:
    lines = [
        "# Change Impact Report",
        "",
        f"- **Target:** `{target_path}`",
        f"- **Symbols scanned:** {', '.join(symbols) if symbols else '(file-level)'}",
        "",
    ]

    for r in results:
        lines.append(f"## Symbol: `{r.symbol}`")
        lines.append("")

        if r.direct_refs:
            lines.append("### Direct references (must sync modify)")
            lines.append("")
            lines.append("| File | Line | Type | Context |")
            lines.append("|------|------|------|---------|")
            for ref in r.direct_refs:
                ctx = ref.context.replace("|", r"\|")
                lines.append(f"| `{ref.file}` | {ref.line} | {ref.ref_type} | {ctx} |")
            lines.append("")
        else:
            lines.append("*No direct references found.*")
            lines.append("")

        if r.indirect_refs:
            lines.append(f"### Indirect references ({len(r.indirect_refs)} potential impacts)")
            lines.append("")
            lines.append("| File | Line | Type | Context |")
            lines.append("|------|------|------|---------|")
            for ref in r.indirect_refs[:20]:  # cap indirect refs
                ctx = ref.context.replace("|", r"\|")
                lines.append(f"| `{ref.file}` | {ref.line} | {ref.ref_type} | {ctx} |")
            if len(r.indirect_refs) > 20:
                lines.append(f"| ... | ... | ... | *{len(r.indirect_refs) - 20} more* |")
            lines.append("")

    return "\n".join(lines)


async def run_openagent_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute the impact scan pipeline.

    Returns: report_path, symbols_scanned, total_refs, direct_refs, indirect_refs.
    """
    target_path = payload.get("target_path")
    project_root = payload.get("project_root", ".")
    symbols = payload.get("symbols", [])
    output_path = payload.get("output_path")
    max_files = int(payload.get("max_files", 500))

    if not target_path:
        raise ValueError("impact-scan-pipeline: payload must include 'target_path'")
    if not output_path:
        raise ValueError("impact-scan-pipeline: payload must include 'output_path'")

    target = Path(target_path)
    root = Path(project_root).resolve()

    # If symbols not provided, try to extract from target file
    if not symbols and target.is_file() and target.suffix == ".py":
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(node.name)
                elif isinstance(node, ast.ClassDef):
                    symbols.append(node.name)
        except Exception:
            pass

    results: list[_ImpactResult] = []

    for symbol in symbols:
        refs = _grep_refs(root, symbol)
        # Exclude definitions in the target file itself
        direct = [r for r in refs if r.ref_type in ("call", "import", "import_from")]
        indirect = [r for r in refs if r.ref_type not in ("call", "import", "import_from")]
        results.append(_ImpactResult(
            symbol=symbol,
            direct_refs=direct,
            indirect_refs=indirect,
        ))

    if not symbols:
        # File-level scan: find imports of this file
        module_name = target.stem
        refs = _grep_refs(root, module_name)
        results.append(_ImpactResult(
            symbol=f"module:{module_name}",
            direct_refs=[r for r in refs if r.ref_type == "import"],
            indirect_refs=[r for r in refs if r.ref_type != "import"],
        ))

    report_text = _render_report(results, str(target), symbols)
    output_p = Path(output_path)
    output_p.parent.mkdir(parents=True, exist_ok=True)
    output_p.write_text(report_text, encoding="utf-8")

    total_refs = sum(len(r.direct_refs) + len(r.indirect_refs) for r in results)
    direct_count = sum(len(r.direct_refs) for r in results)

    return {
        "report_path": str(output_p),
        "symbols_scanned": len(results),
        "total_refs": total_refs,
        "direct_refs": direct_count,
        "indirect_refs": total_refs - direct_count,
    }
