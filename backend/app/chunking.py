"""Dynamic AST-aware chunking.

Primary strategy: tree-sitter grammars turn source files into declaration-level
chunks (functions, methods, classes, interfaces, structs, traits) with exact
byte and line spans, qualified symbol names, and doc comments. Relationship
extraction runs over the same source so ``CALLS`` / ``IMPORTS`` /
``EXTENDS`` / ``IMPLEMENTS`` edges can be written to the graph store during
ingestion.

Fallback strategy (tree-sitter not installed): Python files are parsed with
the standard library ``ast`` module, and brace languages are handled by a
deterministic line scanner that respects brace balance. This keeps the system
fully functional offline.
"""

from __future__ import annotations

import ast as py_ast
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# --- optional tree-sitter import ----------------------------------------------------
try:  # pragma: no cover - exercised implicitly depending on environment
    from tree_sitter import Language, Parser

    import tree_sitter_go
    import tree_sitter_java
    import tree_sitter_javascript
    import tree_sitter_python
    import tree_sitter_rust
    import tree_sitter_typescript

    TREE_SITTER_AVAILABLE = True
    _GRAMMAR_FACTORIES: dict[str, Any] = {
        "python": tree_sitter_python.language,
        "typescript": tree_sitter_typescript.language_typescript,
        "tsx": tree_sitter_typescript.language_tsx,
        "javascript": tree_sitter_javascript.language,
        "go": tree_sitter_go.language,
        "java": tree_sitter_java.language,
        "rust": tree_sitter_rust.language,
    }
except ImportError:  # pragma: no cover
    TREE_SITTER_AVAILABLE = False
    _GRAMMAR_FACTORIES = {}


EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
}

SUPPORTED_LANGUAGES = tuple(EXTENSION_TO_LANGUAGE.values())

# Node types that open a declaration chunk, mapped to chunk kinds.
_DECLARATION_NODES: dict[str, dict[str, str]] = {
    "python": {"function_definition": "function", "class_definition": "class"},
    "typescript": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
        "method_definition": "method",
    },
    "tsx": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
        "method_definition": "method",
    },
    "javascript": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "go": {"function_declaration": "function", "method_declaration": "method", "type_spec": "type"},
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "record_declaration": "class",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "impl_item": "impl",
        "type_item": "type",
    },
}

# Containers whose children receive qualified ParentName.child names.
_CONTAINER_NODES = {
    "class_definition",
    "class_declaration",
    "abstract_class_declaration",
    "interface_declaration",
    "impl_item",
    "trait_item",
    "enum_item",
    "struct_item",
}

_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(")

_CALL_KEYWORDS = {
    "if", "else", "for", "while", "switch", "case", "catch", "do", "try", "return",
    "yield", "await", "new", "delete", "typeof", "match", "with", "raise", "except",
    "and", "or", "not", "in", "of", "is", "lambda", "async", "print", "super", "self",
    "make", "defer", "go", "var", "let", "const", "def", "func", "fn", "function",
    "class", "struct", "impl", "enum", "trait", "interface", "type", "sizeof",
}

@dataclass
class Chunk:
    """A semantic code unit produced by a chunker."""

    id: str
    tenant_id: str
    repository_id: str
    commit_sha: str
    file_path: str
    language: str
    kind: str
    symbol: str
    name: str
    content: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    calls: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)
    implements: list[str] = field(default_factory=list)
    parent: str | None = None
    doc: str | None = None
    part_index: int = 0
    part_count: int = 1


@dataclass
class Relationship:
    """A directed relationship discovered while parsing a file."""

    source: str
    relation: str  # CALLS | IMPORTS | EXTENDS | IMPLEMENTS
    target: str


class LanguageNotSupported(Exception):
    """Raised when no chunker exists for a language."""


def extract_calls(text: str, owner: str) -> list[str]:
    """Extract call names from ``text`` with a language-neutral heuristic.

    Returns dotted call names (``this.gateway.capture`` becomes
    ``gateway.capture``), deduplicated while preserving order.
    """
    calls: list[str] = []
    for match in _CALL_RE.finditer(text):
        raw = match.group(1)
        parts = raw.split(".")
        if parts[0] in _CALL_KEYWORDS:
            continue
        if parts[0] in ("this", "self") and len(parts) > 1:
            parts = parts[1:]
        target = ".".join(part for part in parts if part)
        if not target or target == owner:
            continue
        if target.rsplit(".", 1)[-1] == owner.rsplit(".", 1)[-1]:
            continue  # recursion or method invoking its own name
        if len(target.rsplit(".", 1)[-1]) < 2:
            continue
        calls.append(target)
    return list(dict.fromkeys(calls))


def extract_imports(source_text: str, language: str) -> list[str]:
    """Extract module/import paths from a file's source text."""
    imports: list[str] = []
    if language == "python":
        for match in re.finditer(r"^\s*from\s+([\w.]+)\s+import", source_text, re.MULTILINE):
            imports.append(match.group(1))
        for match in re.finditer(r"^\s*import\s+([\w.]+)", source_text, re.MULTILINE):
            imports.append(match.group(1))
    elif language in ("typescript", "tsx", "javascript"):
        for match in re.finditer(r"""(?:import|export)\s[^;]*?from\s+['"]([^'"]+)['"]""", source_text):
            imports.append(match.group(1))
        for match in re.finditer(r"""import\s+['"]([^'"]+)['"]""", source_text):
            imports.append(match.group(1))
        for match in re.finditer(r"""require\(\s*['"]([^'"]+)['"]\s*\)""", source_text):
            imports.append(match.group(1))
    elif language == "go":
        for match in re.finditer(r'"([\w./-]+)"', source_text[source_text.find("import") : source_text.find("import") + 4000]):
            imports.append(match.group(1))
    elif language == "java":
        for match in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.]+);", source_text, re.MULTILINE):
            imports.append(match.group(1))
    elif language == "rust":
        for match in re.finditer(r"^\s*use\s+([\w:]+)", source_text, re.MULTILINE):
            imports.append(match.group(1))
    return list(dict.fromkeys(imports))


def extract_doc_comment(source_text: str, start_line: int) -> str | None:
    """Best-effort doc comment extraction from the lines above a declaration."""
    lines = source_text.splitlines()
    collected: list[str] = []
    index = start_line - 2  # first line above the declaration
    while index >= 0 and len(collected) < 3:
        stripped = lines[index].strip()
        if not stripped:
            break
        if stripped.startswith(("//", "///", "#", "*", "/*", "**")):
            collected.append(stripped.lstrip("/*# ").rstrip("*/").strip())
            index -= 1
            continue
        break
    doc = " ".join(reversed(collected)).strip()
    return doc[:280] or None


def detect_language(file_name: str) -> str | None:
    """Map a file name to a supported language identifier."""
    lowered = file_name.lower()
    for extension, language in EXTENSION_TO_LANGUAGE.items():
        if lowered.endswith(extension):
            return language
    return None


def chunk_id(
    tenant_id: str,
    repository_id: str,
    commit_sha: str,
    file_path: str,
    start_byte: int,
    end_byte: int,
) -> str:
    """Stable, idempotent identifier for a chunk within a revision."""
    digest = hashlib.blake2b(
        f"{tenant_id}|{repository_id}|{commit_sha}|{file_path}|{start_byte}:{end_byte}".encode(),
        digest_size=16,
    ).hexdigest()
    return digest


def is_binary(data: bytes) -> bool:
    """Heuristic binary-file detection."""
    if b"\x00" in data[:8192]:
        return True
    return False

class BaseChunker:
    """Shared post-processing used by every chunker implementation."""

    def finalize(
        self,
        declarations: list[dict[str, Any]],
        *,
        tenant_id: str,
        repository_id: str,
        commit_sha: str,
        file_path: str,
        language: str,
        imports: list[str],
        max_chars: int,
        source_text: str,
    ) -> list[Chunk]:
        """Turn raw declaration dicts into split, id-assigned ``Chunk`` objects."""
        final: list[Chunk] = []
        for item in declarations:
            content = item["content"]
            if len(content.strip()) < 20 and item["kind"] != "module":
                continue
            parts = self.split_content(content, max_chars)
            for index, piece in enumerate(parts, start=1):
                start = item["start_byte"]
                end = item["end_byte"]
                if len(parts) > 1:
                    span = item["end_byte"] - item["start_byte"]
                    start = item["start_byte"] + int(span * (index - 1) / len(parts))
                    end = item["start_byte"] + int(span * index / len(parts))
                final.append(
                    Chunk(
                        id=chunk_id(tenant_id, repository_id, commit_sha, file_path, start, end),
                        tenant_id=tenant_id,
                        repository_id=repository_id,
                        commit_sha=commit_sha,
                        file_path=file_path,
                        language=language,
                        kind=item["kind"],
                        symbol=item["symbol"],
                        name=item["name"],
                        content=piece,
                        start_byte=start,
                        end_byte=end,
                        start_line=source_text.count("\n", 0, start) + 1,
                        end_line=source_text.count("\n", 0, end) + 1,
                        calls=item.get("calls", []),
                        imports=list(imports),
                        extends=item.get("extends", []),
                        implements=item.get("implements", []),
                        parent=item.get("parent"),
                        doc=item.get("doc"),
                        part_index=index if len(parts) > 1 else 0,
                        part_count=len(parts),
                    )
                )
        return final

    @staticmethod
    def split_content(content: str, max_chars: int) -> list[str]:
        """Split oversized content into overlap-free pieces under ``max_chars``."""
        if len(content) <= max_chars:
            return [content]
        pieces: list[str] = []
        current: list[str] = []
        size = 0
        for line in content.splitlines(keepends=True):
            if size + len(line) > max_chars and current:
                pieces.append("".join(current))
                current, size = [], 0
            current.append(line)
            size += len(line)
        if current:
            pieces.append("".join(current))
        return pieces

class TreeSitterChunker(BaseChunker):
    """AST chunker built on tree-sitter grammars."""

    languages = SUPPORTED_LANGUAGES

    def __init__(self) -> None:
        self._parsers: dict[str, Parser] = {}

    def _parser(self, language: str) -> Parser:
        if language not in self._parsers:
            factory = _GRAMMAR_FACTORIES.get(language)
            if factory is None:
                raise LanguageNotSupported(language)
            lang = Language(factory())
            try:
                parser = Parser(lang)
            except TypeError:  # pragma: no cover - older binding API
                parser = Parser()
                parser.language = lang
            self._parsers[language] = parser
        return self._parsers[language]

    @staticmethod
    def _field_text(node: Any, source: bytes, *fields: str) -> str | None:
        for name in fields:
            child = node.child_by_field_name(name)
            if child is not None:
                return source[child.start_byte : child.end_byte].decode("utf-8", "replace").strip()
        return None

    @staticmethod
    def _text(node: Any, source: bytes) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def chunk(
        self,
        *,
        source: bytes,
        file_path: str,
        language: str,
        tenant_id: str,
        repository_id: str,
        commit_sha: str,
        max_chars: int,
    ) -> list[Chunk]:
        parser = self._parser(language)
        tree = parser.parse(source)
        source_text = source.decode("utf-8", "replace")
        imports = extract_imports(source_text, language)
        declarations: list[dict[str, Any]] = []
        covered: list[tuple[int, int]] = []
        self._walk(tree.root_node, source, language, "", declarations, covered)
        declarations.extend(self._module_chunks(tree.root_node, source, covered))
        return self.finalize(
            declarations,
            tenant_id=tenant_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            file_path=file_path,
            language=language,
            imports=imports,
            max_chars=max_chars,
            source_text=source_text,
        )

    def _walk(
        self,
        node: Any,
        source: bytes,
        language: str,
        prefix: str,
        out: list[dict[str, Any]],
        covered: list[tuple[int, int]],
    ) -> None:
        decls = _DECLARATION_NODES.get(language, {})
        for child in node.children:
            if child.type in decls:
                self._emit_declaration(child, source, language, prefix, out, covered, decls)
            elif child.type in ("lexical_declaration", "variable_declaration"):
                self._emit_variable(child, source, prefix, out, covered)
            elif child.type in ("export_statement", "expression_statement", "decorated_definition"):
                inner = [c for c in child.children if c.type in decls]
                if inner:
                    self._emit_declaration(inner[0], source, language, prefix, out, covered, decls)
                    covered.append((child.start_byte, child.end_byte))
                else:
                    self._walk(child, source, language, prefix, out, covered)
            else:
                self._walk(child, source, language, prefix, out, covered)

    def _emit_declaration(
        self,
        node: Any,
        source: bytes,
        language: str,
        prefix: str,
        out: list[dict[str, Any]],
        covered: list[tuple[int, int]],
        decls: dict[str, str],
    ) -> None:
        kind = decls[node.type]
        container_prefix = prefix
        name: str | None
        if node.type == "impl_item":
            type_text = self._field_text(node, source, "type") or ""
            trait_text = self._field_text(node, source, "trait")
            name = f"impl {trait_text} for {type_text}" if trait_text and trait_text != "Self" else f"impl {type_text}"
            container_prefix = type_text or prefix
        elif node.type == "type_spec":
            name = self._field_text(node, source, "name")
            body = self._text(node, source)
            if "struct {" in body:
                kind = "struct"
            elif "interface {" in body:
                kind = "interface"
        else:
            name = self._field_text(node, source, "name")

        if not name:
            self._walk(node, source, language, prefix, out, covered)
            return

        name = name.strip().strip('"')
        qualified = f"{container_prefix}.{name}" if container_prefix else name
        content = self._text(node, source)
        covered.append((node.start_byte, node.end_byte))

        extends: list[str] = []
        implements: list[str] = []
        header = content.split("{", 1)[0] if "{" in content else content
        if language == "python":
            bases = re.search(r"class\s+\w+\s*\(([^)]*)\)", header)
            if bases:
                extends = [b.strip().split("(")[0] for b in bases.group(1).split(",") if b.strip() and b.strip() != "object"]
        elif kind in ("class", "interface"):
            ext = re.search(r"\bextends\s+([\w.<>, ]+?)(?=\s+implements|\s*\{|\s*$)", header)
            if ext:
                extends = [b.strip() for b in ext.group(1).split(",") if b.strip()]
            imp = re.search(r"\bimplements\s+([\w.<>, ]+?)(?=\s*\{|\s*$)", header)
            if imp:
                implements = [i.strip() for i in imp.group(1).split(",") if i.strip()]
        elif kind == "impl":
            trait = re.search(r"impl(?:<[^>]*>)?\s+(\w+)\s+for\s+([\w:<>]+)", header)
            if trait:
                extends = [trait.group(1)]
                implements = [trait.group(2)]

        out.append(
            {
                "kind": kind,
                "name": name,
                "symbol": qualified,
                "content": content,
                "start_byte": node.start_byte,
                "end_byte": node.end_byte,
                "calls": extract_calls(content, qualified),
                "extends": extends,
                "implements": implements,
                "parent": prefix or None,
                "doc": extract_doc_comment(source.decode("utf-8", "replace"), node.start_point[0] + 1),
            }
        )
        if node.type in _CONTAINER_NODES or kind in ("class", "interface", "impl", "struct", "enum", "trait"):
            self._walk(node, source, language, qualified, out, covered)

    def _emit_variable(
        self,
        node: Any,
        source: bytes,
        prefix: str,
        out: list[dict[str, Any]],
        covered: list[tuple[int, int]],
    ) -> None:
        """Handle ``const foo = () => {}`` / ``var x = function() {}`` patterns."""
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            name = self._field_text(declarator, source, "name")
            value = declarator.child_by_field_name("value")
            if not name or value is None:
                continue
            if value.type not in ("arrow_function", "function", "function_expression", "generator_function"):
                continue
            qualified = f"{prefix}.{name}" if prefix else name
            content = self._text(node, source)
            covered.append((node.start_byte, node.end_byte))
            out.append(
                {
                    "kind": "function",
                    "name": name,
                    "symbol": qualified,
                    "content": content,
                    "start_byte": node.start_byte,
                    "end_byte": node.end_byte,
                    "calls": extract_calls(content, qualified),
                    "parent": prefix or None,
                    "doc": extract_doc_comment(source.decode("utf-8", "replace"), node.start_point[0] + 1),
                }
            )
            self._walk(value, source, "typescript", qualified, out, covered)

    def _module_chunks(self, root: Any, source: bytes, covered: list[tuple[int, int]]) -> list[dict[str, Any]]:
        """Emit a module-level chunk for top-level code outside declarations."""
        leftovers: list[tuple[int, int]] = []
        skip = {
            "import_statement", "import_from_statement", "import_declaration",
            "use_declaration", "comment", "line_comment", "block_comment",
            "package_clause", "hash_bang_line", "decorated_definition",
        }
        for child in root.children:
            if child.type in skip or not child.is_named:
                continue
            if any(child.start_byte >= start and child.end_byte <= end for start, end in covered):
                continue
            text = self._text(child, source)
            if len(text.strip()) >= 40:
                leftovers.append((child.start_byte, child.end_byte))
        if not leftovers:
            return []
        content = "\n".join(source[start:end].decode("utf-8", "replace") for start, end in leftovers)
        if len(content.strip()) < 40:
            return []
        return [
            {
                "kind": "module",
                "name": "<module>",
                "symbol": "<module>",
                "content": content,
                "start_byte": min(start for start, _ in leftovers),
                "end_byte": max(end for _, end in leftovers),
                "calls": [],
            }
        ]

class PythonAstChunker(BaseChunker):
    """Python chunker using the standard library ``ast`` module.

    Used when tree-sitter is unavailable; the tree-sitter grammar is preferred
    when present because it matches the multi-language pipeline.
    """

    languages = ("python",)

    def chunk(
        self,
        *,
        source: bytes,
        file_path: str,
        language: str,
        tenant_id: str,
        repository_id: str,
        commit_sha: str,
        max_chars: int,
    ) -> list[Chunk]:
        source_text = source.decode("utf-8", "replace")
        imports = extract_imports(source_text, language)
        tree = py_ast.parse(source_text)
        declarations: list[dict[str, Any]] = []
        self._walk(tree.body, source_text, "", declarations)
        return self.finalize(
            declarations,
            tenant_id=tenant_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            file_path=file_path,
            language=language,
            imports=imports,
            max_chars=max_chars,
            source_text=source_text,
        )

    def _walk(self, body: list, source_text: str, prefix: str, out: list[dict[str, Any]]) -> None:
        for node in body:
            if isinstance(node, (py_ast.FunctionDef, py_ast.AsyncFunctionDef, py_ast.ClassDef)):
                kind = "class" if isinstance(node, py_ast.ClassDef) else "function"
                qualified = f"{prefix}.{node.name}" if prefix else node.name
                content = py_ast.get_source_segment(source_text, node) or ""
                extends: list[str] = []
                if isinstance(node, py_ast.ClassDef):
                    for base in node.bases:
                        name = base.id if isinstance(base, py_ast.Name) else ast_name(base)
                        if name and name != "object":
                            extends.append(name)
                lines_before = len("".join(source_text.splitlines(keepends=True)[: node.lineno - 1]))
                end_line = node.end_lineno or node.lineno
                lines_through_end = len("".join(source_text.splitlines(keepends=True)[:end_line]))
                out.append(
                    {
                        "kind": kind,
                        "name": node.name,
                        "symbol": qualified,
                        "content": content,
                        "start_byte": lines_before,
                        "end_byte": lines_through_end,
                        "calls": extract_calls(content, qualified),
                        "extends": extends,
                        "parent": prefix or None,
                    }
                )
                if isinstance(node, py_ast.ClassDef):
                    self._walk(node.body, source_text, qualified, out)
            elif isinstance(node, (py_ast.If, py_ast.Try, py_ast.With)):
                self._walk(getattr(node, "body", []), source_text, prefix, out)
                for handler in getattr(node, "handlers", []):
                    self._walk(handler.body, source_text, prefix, out)
                self._walk(getattr(node, "orelse", []), source_text, prefix, out)
                self._walk(getattr(node, "finalbody", []), source_text, prefix, out)


def ast_name(node: py_ast.expr) -> str | None:
    """Best-effort textual name for an ``ast`` base-class expression."""
    if isinstance(node, py_ast.Attribute):
        base = ast_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, py_ast.Name):
        return node.id
    if isinstance(node, py_ast.Subscript):
        return ast_name(node.value)
    return None

class GenericChunker(BaseChunker):
    """Brace-balance line scanner for languages without a tree-sitter grammar.

    Handles top-level declarations (functions, classes, structs, interfaces)
    and methods nested inside container blocks.
    """

    languages = ("kotlin", "scala", "c", "cpp", "csharp", "php", "swift", "dart", "other")

    _TOP_PATTERNS = [
        (re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"), "function"),
        (re.compile(r"^\s*(?:public|private|protected|internal|static).*?\bclass\s+([A-Za-z_]\w*)"), "class"),
        (re.compile(r"^\s*(?:public\s+|private\s+|internal\s+)?interface\s+([A-Za-z_]\w*)"), "interface"),
        (re.compile(r"^\s*(?:public\s+|private\s+|internal\s+)?struct\s+([A-Za-z_]\w*)"), "struct"),
        (re.compile(r"^\s*(?:public\s+|private\s+)?func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"), "function"),
        (re.compile(r"^\s*def\s+([A-Za-z_]\w*)"), "function"),
        (re.compile(r"^\s*class\s+([A-Za-z_]\w*)"), "class"),
    ]

    _METHOD_PATTERNS = [
        (re.compile(r"^\s+(?:pub(?:\(\w+\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"), "method"),
        (re.compile(r"^\s+(?:public|private|protected|internal|override|static|async|final).*?\b([A-Za-z_]\w*)\s*\("), "method"),
    ]

    def chunk(
        self,
        *,
        source: bytes,
        file_path: str,
        language: str,
        tenant_id: str,
        repository_id: str,
        commit_sha: str,
        max_chars: int,
    ) -> list[Chunk]:
        source_text = source.decode("utf-8", "replace")
        lines = source_text.splitlines(keepends=True)
        imports = extract_imports(source_text, language)
        declarations: list[dict[str, Any]] = []
        index = 0
        while index < len(lines):
            matched = False
            for pattern, kind in self._TOP_PATTERNS:
                match = pattern.match(lines[index])
                if not match:
                    continue
                name = match.group(1)
                end = self._block_end(lines, index)
                content = "".join(lines[index : end + 1])
                qualified = name
                declarations.append(
                    self._declaration(kind, name, qualified, content, source_text, lines, index, end)
                )
                self._nested_methods(lines, index, end, qualified, declarations, source_text)
                index = end + 1
                matched = True
                break
            if not matched:
                index += 1
        return self.finalize(
            declarations,
            tenant_id=tenant_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            file_path=file_path,
            language=language,
            imports=imports,
            max_chars=max_chars,
            source_text=source_text,
        )

    def _declaration(
        self,
        kind: str,
        name: str,
        qualified: str,
        content: str,
        source_text: str,
        lines: list[str],
        start: int,
        end: int,
    ) -> dict[str, Any]:
        start_byte = len("".join(lines[:start]))
        end_byte = len("".join(lines[: end + 1]))
        return {
            "kind": kind,
            "name": name,
            "symbol": qualified,
            "content": content,
            "start_byte": start_byte,
            "end_byte": end_byte,
            "calls": extract_calls(content, qualified),
            "parent": None,
            "doc": extract_doc_comment(source_text, start + 1),
        }

    def _nested_methods(
        self,
        lines: list[str],
        start: int,
        end: int,
        parent: str,
        declarations: list[dict[str, Any]],
        source_text: str,
    ) -> None:
        index = start + 1
        while index < end:
            for pattern, kind in self._METHOD_PATTERNS:
                match = pattern.match(lines[index])
                if not match:
                    continue
                name = match.group(1)
                if name in ("if", "for", "while", "switch", "return", "catch"):
                    break
                method_end = self._block_end(lines, index)
                if method_end > end:
                    break
                content = "".join(lines[index : method_end + 1])
                qualified = f"{parent}.{name}"
                declarations.append(
                    self._declaration(kind, name, qualified, content, source_text, lines, index, method_end)
                )
                index = method_end + 1
                break
            else:
                index += 1

    @staticmethod
    def _block_end(lines: list[str], start: int) -> int:
        """Find the line where a brace-balanced block closes."""
        depth = 0
        opened = False
        for position in range(start, len(lines)):
            for char in lines[position]:
                if char == "{":
                    depth += 1
                    opened = True
                elif char == "}":
                    depth -= 1
            if opened and depth <= 0:
                return position
        return min(start + 30, len(lines) - 1)

def get_chunker(language: str) -> BaseChunker:
    """Return the best available chunker for ``language``."""
    if TREE_SITTER_AVAILABLE and language in _GRAMMAR_FACTORIES:
        return TreeSitterChunker()
    if language == "python":
        return PythonAstChunker()
    return GenericChunker()


def chunk_file(
    *,
    source: bytes,
    file_path: str,
    tenant_id: str,
    repository_id: str,
    commit_sha: str,
    max_chars: int = 1600,
) -> list[Chunk]:
    """Chunk one file's bytes into semantic units.

    Files whose language cannot be detected return an empty list.
    """
    language = detect_language(file_path)
    if language is None:
        return []
    chunker = get_chunker(language)
    return chunker.chunk(
        source=source,
        file_path=file_path,
        language=language,
        tenant_id=tenant_id,
        repository_id=repository_id,
        commit_sha=commit_sha,
        max_chars=max_chars,
    )


def relationships_from_chunks(chunks: list[Chunk]) -> list[Relationship]:
    """Build graph relationships from a set of chunked symbols.

    CALLS edges use the raw extracted call name (later resolved against
    indexed symbols); EXTENDS / IMPLEMENTS use declared type names.
    """
    relationships: list[Relationship] = []
    for chunk in chunks:
        for callee in chunk.calls:
            relationships.append(Relationship(chunk.symbol, "CALLS", callee))
        for base in chunk.extends:
            relationships.append(Relationship(chunk.symbol, "EXTENDS", base))
        for iface in chunk.implements:
            relationships.append(Relationship(chunk.symbol, "IMPLEMENTS", iface))
    return relationships








