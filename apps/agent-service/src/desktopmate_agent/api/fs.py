"""文件系统 API：项目目录浏览与上下文生成（2.1 workspace 增强）。

所有操作受 allowed_roots 限制（与文件工具同一安全边界）：
- 只能浏览/读取 allowed_roots 内的路径
- 路径规范化后必须位于某个 root 之下，否则 403

端点：
- GET /api/fs/roots          → 可访问根目录列表
- GET /api/fs/tree?path=     → 指定目录的浅层内容（文件+子目录，单层）
- GET /api/fs/context?path=  → 项目上下文摘要（结构树 + README + 语言统计），
                               供前端作为 workspace_context 注入对话
- GET /api/fs/read?path=     → 文件内容（文本，受 roots 限制）
- POST /api/fs/write         → 编辑保存文件（受 roots 限制；用户主动操作，
                               等同本地编辑，token 鉴权即足够）
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from desktopmate_agent.api.routes import require_auth

# 浏览深度上限：防止巨目录响应爆炸
MAX_TREE_ENTRIES = 500
MAX_CONTEXT_DEPTH = 4
MAX_README_BYTES = 8_000
MAX_READ_BYTES = 200_000  # 单文件读取上限 200KB（防止超大文件撑爆响应）
MAX_WRITE_BYTES = 500_000  # 单文件写入上限 500KB
IGNORED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", ".nuxt", ".idea", ".vscode", "target", ".pytest_cache", ".mypy_cache",
}


class FileWrite(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    content: str = Field(max_length=MAX_WRITE_BYTES)


def _resolve_within_roots(path_str: str, allowed_roots: list[Path]) -> Path:
    """把用户提供的路径规范化，校验必须在 allowed_roots 内；否则抛 403。"""
    path = Path(path_str).expanduser().resolve()
    for root in allowed_roots:
        if path == root or root in path.parents:
            return path
    raise HTTPException(
        status_code=403,
        detail=dict(code="path_outside_roots", message="路径不在可访问范围内"),
    )


def create_fs_router(allowed_roots: list[Path]) -> APIRouter:
    roots = [root.resolve() for root in allowed_roots]
    router = APIRouter()

    @router.get("/api/fs/roots")
    def list_roots(_: None = Depends(require_auth)) -> dict[str, object]:
        items = []
        for root in roots:
            items.append(
                {
                    "path": str(root),
                    "name": root.name or str(root),
                    "exists": root.exists(),
                }
            )
        return {"roots": items}

    @router.get("/api/fs/tree")
    def list_tree(
        path: str = Query(default="", max_length=1000),
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        target = _resolve_within_roots(path or str(roots[0]), roots)
        if not target.is_dir():
            raise HTTPException(
                status_code=404,
                detail=dict(code="not_a_directory", message="路径不是目录"),
            )
        files: list[dict[str, object]] = []
        dirs: list[dict[str, object]] = []
        try:
            entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail=dict(code="read_failed", message=f"读取目录失败: {exc}"),
            ) from exc
        for entry in entries:
            if entry.name in IGNORED_DIRS:
                continue
            try:
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": str(entry)})
                else:
                    size = entry.stat().st_size if entry.is_file() else 0
                    files.append({"name": entry.name, "path": str(entry), "size": size})
            except OSError:
                continue
            if len(files) + len(dirs) >= MAX_TREE_ENTRIES:
                break
        return {"path": str(target), "dirs": dirs, "files": files}

    @router.get("/api/fs/context")
    def project_context(
        path: str = Query(default="", max_length=1000),
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        """生成项目上下文摘要：结构树 + README 前 N 字符 + 文件语言统计。

        前端拿到后组装成 workspace_context（title/kind/content）传给 chat。
        """
        target = _resolve_within_roots(path or str(roots[0]), roots)
        if not target.is_dir():
            raise HTTPException(
                status_code=404,
                detail=dict(code="not_a_directory", message="路径不是目录"),
            )

        tree_lines: list[str] = []
        extensions: dict[str, int] = {}
        total_files = 0

        def walk(current: Path, depth: int) -> None:
            nonlocal total_files
            if depth > MAX_CONTEXT_DEPTH:
                return
            try:
                entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                return
            for entry in entries:
                if entry.name in IGNORED_DIRS:
                    continue
                indent = "  " * depth
                try:
                    if entry.is_dir():
                        tree_lines.append(f"{indent}{entry.name}/")
                        walk(entry, depth + 1)
                    else:
                        total_files += 1
                        ext = entry.suffix.lower() or "(no-ext)"
                        extensions[ext] = extensions.get(ext, 0) + 1
                        tree_lines.append(f"{indent}{entry.name}")
                except OSError:
                    continue
                if len(tree_lines) >= MAX_TREE_ENTRIES:
                    return

        walk(target, 0)

        # README 提取（常见命名）
        readme_text = ""
        for name in ("README.md", "readme.md", "README", "Readme.md"):
            candidate = target / name
            if candidate.is_file():
                try:
                    readme_text = candidate.read_text(
                        encoding="utf-8", errors="replace"
                    )[:MAX_README_BYTES]
                except OSError:
                    readme_text = ""
                break

        top_exts = sorted(extensions.items(), key=lambda item: item[1], reverse=True)[:8]
        return {
            "path": str(target),
            "name": target.name or str(target),
            "total_files": total_files,
            "tree": "\n".join(tree_lines[:MAX_TREE_ENTRIES]),
            "readme": readme_text,
            "extensions": [{"ext": ext, "count": count} for ext, count in top_exts],
        }

    @router.get("/api/fs/read")
    def read_file(
        path: str = Query(default="", max_length=2000),
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        target = _resolve_within_roots(path, roots)
        if not target.is_file():
            raise HTTPException(
                status_code=404,
                detail=dict(code="not_a_file", message="路径不是文件"),
            )
        try:
            size = target.stat().st_size
            if size > MAX_READ_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=dict(
                        code="file_too_large",
                        message=f"文件过大（{size} 字节，上限 {MAX_READ_BYTES}）",
                    ),
                )
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail=dict(code="read_failed", message=f"读取文件失败: {exc}"),
            ) from exc
        return {"path": str(target), "name": target.name, "size": size, "content": content}

    @router.post("/api/fs/write")
    def write_file(
        payload: FileWrite,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        target = _resolve_within_roots(payload.path, roots)
        if target.is_dir():
            raise HTTPException(
                status_code=400,
                detail=dict(code="is_directory", message="目标路径是目录"),
            )
        try:
            # 创建父目录（项目内新增文件场景）
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload.content, encoding="utf-8")
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail=dict(code="write_failed", message=f"写入失败: {exc}"),
            ) from exc
        return {"path": str(target), "size": len(payload.content.encode("utf-8"))}

    return router
