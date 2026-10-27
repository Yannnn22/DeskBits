"""文件浏览 API（只读）：目录列表 + 文件读取 + 多格式预览，受 allowed_roots 限制。"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends

from desktopmate_agent.api.errors import ApiError
from desktopmate_agent.api.routes import require_auth

_PREVIEW_CHAR_LIMIT = 100_000

# 文本类扩展名：直接按 UTF-8 读取
_TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".csv", ".log",
    ".py", ".js", ".ts", ".vue", ".css", ".html", ".xml", ".sh", ".toml",
    ".ini", ".conf", ".sql", ".rst",
}


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(page for page in pages if page.strip())


def _extract_docx(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    return "\n".join(p.text for p in document.paragraphs if p.text.strip())


def _extract_pptx(path: Path) -> str:
    from pptx import Presentation

    presentation = Presentation(str(path))
    chunks: list[str] = []
    for index, slide in enumerate(presentation.slides, start=1):
        texts: list[str] = []
        for shape in slide.shapes:
            frame = getattr(shape, "text_frame", None)
            text = (frame.text if frame is not None else "").strip()
            if text:
                texts.append(text)
        if texts:
            chunks.append(f"【第 {index} 页】\n" + "\n".join(texts))
    return "\n\n".join(chunks)


def _preview_text(path: Path) -> tuple[str, str]:
    """返回 (kind, content)。kind: text/pdf/docx/pptx。"""
    extension = path.suffix.lower()
    if extension in _TEXT_EXTENSIONS:
        try:
            return "text", path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return "binary", ""
    if extension == ".pdf":
        return "pdf", _extract_pdf(path)
    if extension == ".docx":
        return "docx", _extract_docx(path)
    if extension == ".pptx":
        return "pptx", _extract_pptx(path)
    return "unsupported", ""


def _resolve(raw: str, allowed_roots: list[Path]) -> Path:
    path = Path(raw or "").expanduser().resolve()
    if not any(
        path == root.resolve() or root.resolve() in path.parents
        for root in allowed_roots
    ):
        raise ApiError(status_code=403, code="path_not_allowed", message="路径不在允许访问的目录内")
    return path


def create_files_router(allowed_roots: list[Path]) -> APIRouter:
    router = APIRouter(prefix="/api/files", tags=["files"], dependencies=[Depends(require_auth)])
    roots = [Path(root).expanduser().resolve() for root in allowed_roots]

    @router.get("/list")
    def list_files(path: str = "") -> dict:
        target = _resolve(path, roots)
        if not target.is_dir():
            raise ApiError(status_code=404, code="dir_not_found", message="目录不存在")
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                stat = child.stat()
            except OSError:
                continue
            entries.append(
                {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": 0 if child.is_dir() else stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                }
            )
        parent = str(target.parent) if target != Path(target.anchor) else None
        return {"path": str(target), "parent": parent, "entries": entries}

    @router.get("/read")
    def read_file(path: str, max_bytes: int = 1_000_000) -> dict:
        target = _resolve(path, roots)
        if not target.is_file():
            raise ApiError(status_code=404, code="file_not_found", message="文件不存在")
        if target.stat().st_size > max_bytes:
            raise ApiError(status_code=413, code="file_too_large", message=f"文件超过 {max_bytes} 字节，请用对话读取")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ApiError(status_code=415, code="not_text", message="非 UTF-8 文本文件") from None
        stat = target.stat()
        return {
            "path": str(target),
            "content": content,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        }

    @router.get("/preview")
    def preview_file(path: str) -> dict:
        target = _resolve(path, roots)
        if not target.is_file():
            raise ApiError(status_code=404, code="file_not_found", message="文件不存在")
        extension = target.suffix.lower()
        kind, content = _preview_text(target)
        stat = target.stat()
        if kind == "binary":
            raise ApiError(
                status_code=415,
                code="binary_file",
                message="该文件不是 UTF-8 文本，无法直接预览",
            )
        if kind == "unsupported":
            raise ApiError(
                status_code=415,
                code="unsupported_format",
                message=f"暂不支持预览 .{extension[1:] if extension else '未知'} 格式；"
                "支持 md/txt/代码/PDF/DOCX/PPTX，旧版 doc/ppt 请另存为 docx/pptx",
            )
        truncated = len(content) > _PREVIEW_CHAR_LIMIT
        if truncated:
            content = content[:_PREVIEW_CHAR_LIMIT]
        return {
            "path": str(target),
            "name": target.name,
            "kind": kind,
            "content": content,
            "size": stat.st_size,
            "truncated": truncated,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        }

    return router
