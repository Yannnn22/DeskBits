import difflib
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult


MAX_PATH_LENGTH = 4_096
MAX_QUERY_LENGTH = 1_000
MAX_DRAFT_CONTENT_LENGTH = 150_000


class SearchFilesArgs(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    limit: int = Field(default=10, ge=1, le=50)


class ReadFileArgs(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH)


class DraftWriteArgs(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH)
    content: str = Field(min_length=1, max_length=MAX_DRAFT_CONTENT_LENGTH)


class AllowedRootMixin:
    def __init__(self, allowed_roots: list[Path]) -> None:
        self.allowed_roots = [root.resolve() for root in allowed_roots]

    def _is_within_allowed_root(self, path: Path) -> bool:
        return any(path == root or root in path.parents for root in self.allowed_roots)


class SearchFilesTool(AllowedRootMixin, BaseTool[SearchFilesArgs]):
    name = "search_files"
    description = "Search files by filename under explicitly allowed roots."
    args_schema = SearchFilesArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    def __init__(self, allowed_roots: list[Path], max_search_files: int = 100_000) -> None:
        super().__init__(allowed_roots)
        self.max_search_files = max_search_files

    async def run(self, args: SearchFilesArgs) -> ToolResult:
        matches: list[str] = []
        lowered_query = args.query.lower()
        scanned = 0
        for root in self.allowed_roots:
            if not root.exists():
                continue
            try:
                iterator = root.rglob("*")
                for path in iterator:
                    if len(matches) >= args.limit or scanned >= self.max_search_files:
                        break
                    scanned += 1
                    try:
                        is_file = path.is_file()
                    except (PermissionError, OSError):
                        continue  # 无权限目录/损坏链接：跳过而非崩溃
                    resolved_path = path.resolve()
                    if (
                        is_file
                        and lowered_query in path.name.lower()
                        and self._is_within_allowed_root(resolved_path)
                    ):
                        matches.append(str(resolved_path))
            except (PermissionError, OSError):
                continue  # 整个子树不可遍历：跳过该 root
        return ToolResult(
            status="success",
            summary=f"找到 {len(matches)} 个文件",
            data={"matches": matches},
        )


class ReadFileTool(AllowedRootMixin, BaseTool[ReadFileArgs]):
    name = "read_file"
    description = "Read a UTF-8 text file under explicitly allowed roots."
    args_schema = ReadFileArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    def __init__(self, allowed_roots: list[Path], max_read_bytes: int = 5_000_000) -> None:
        super().__init__(allowed_roots)
        self.max_read_bytes = max_read_bytes

    async def run(self, args: ReadFileArgs) -> ToolResult:
        path = Path(args.path).expanduser().resolve()
        if not self._is_within_allowed_root(path):
            return ToolResult(
                status="error",
                summary="文件不在允许访问的目录内",
                data={"path": str(path)},
            )
        if not path.is_file():
            return ToolResult(
                status="error",
                summary="文件不存在或不是普通文件",
                data={"path": str(path)},
            )
        if path.stat().st_size > self.max_read_bytes:
            return ToolResult(
                status="error",
                summary=f"文件过大，最大支持 {self.max_read_bytes} 字节",
                data={"path": str(path), "size": path.stat().st_size},
            )
        content = path.read_text(encoding="utf-8")
        return ToolResult(
            status="success",
            summary="读取文件成功",
            data={"path": str(path), "content": content},
        )


class FileDiffArgs(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH)
    proposed_content: str = Field(min_length=0, max_length=200_000)


class FileDiffTool(AllowedRootMixin, BaseTool[FileDiffArgs]):
    name = "file_diff"
    description = "Preview a unified diff between the current file content and proposed content (no write)."
    args_schema = FileDiffArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    def __init__(self, allowed_roots: list[Path], max_read_bytes: int = 5_000_000) -> None:
        super().__init__(allowed_roots)
        self.max_read_bytes = max_read_bytes

    async def run(self, args: FileDiffArgs) -> ToolResult:
        path = Path(args.path).expanduser().resolve()
        if not self._is_within_allowed_root(path):
            return ToolResult(
                status="error",
                summary="文件不在允许访问的目录内",
                data={"path": str(path)},
            )
        if not path.is_file():
            return ToolResult(
                status="error",
                summary="文件不存在或不是普通文件",
                data={"path": str(path)},
            )
        if path.stat().st_size > self.max_read_bytes:
            return ToolResult(
                status="error",
                summary=f"文件过大，最大支持 {self.max_read_bytes} 字节",
                data={"path": str(path), "size": path.stat().st_size},
            )
        current = path.read_text(encoding="utf-8")
        diff = "".join(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                args.proposed_content.splitlines(keepends=True),
                fromfile=str(path),
                tofile=str(path),
            )
        )
        return ToolResult(
            status="success",
            summary="已生成差异预览" if diff else "内容与现有文件一致，无差异",
            data={"path": str(path), "diff": diff, "changed": bool(diff)},
        )


class FilePatchArgs(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH)
    new_content: str = Field(min_length=0, max_length=200_000)
    reason: str = Field(default="", max_length=200)


class FilePatchTool(AllowedRootMixin, BaseTool[FilePatchArgs]):
    name = "file_patch"
    description = "Apply new content to a UTF-8 text file (atomic write). Requires approval - the diff is shown for confirmation."
    args_schema = FilePatchArgs
    risk_level = RiskLevel.WRITE
    requires_permission = True

    def __init__(self, allowed_roots: list[Path], max_write_bytes: int = 2_000_000) -> None:
        super().__init__(allowed_roots)
        self.max_write_bytes = max_write_bytes

    async def approval_preview(self, raw_args: dict[str, object]) -> dict[str, object] | None:
        args = self.parse_args(raw_args)
        prepared = self._prepare_change(args)
        if isinstance(prepared, ToolResult):
            return {
                "kind": "file_patch",
                "ready": False,
                "message": prepared.summary,
                "path": prepared.data.get("path", args.path),
            }
        return {
            "kind": "file_patch",
            "ready": True,
            "path": str(prepared["path"]),
            "created": prepared["created"],
            "changed": bool(prepared["diff"]),
            "diff": prepared["diff"],
        }

    async def run(self, args: FilePatchArgs) -> ToolResult:
        prepared = self._prepare_change(args)
        if isinstance(prepared, ToolResult):
            return prepared
        path = prepared["path"]
        existed = prepared["existed"]
        diff = prepared["diff"]
        # 原子写：临时文件 + rename，避免写一半
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{uuid4().hex[:8]}.tmp")
        tmp_path.write_text(args.new_content, encoding="utf-8")
        tmp_path.replace(path)
        return ToolResult(
            status="success",
            summary=f"已{'创建' if not existed else '更新'} {path.name}",
            data={"path": str(path), "diff": diff, "created": not existed},
        )

    def _prepare_change(self, args: FilePatchArgs) -> dict[str, object] | ToolResult:
        path = Path(args.path).expanduser().resolve()
        if not self._is_within_allowed_root(path):
            return ToolResult(
                status="error",
                summary="目标文件不在允许访问的目录内",
                data={"path": str(path)},
            )
        if path.exists() and not path.is_file():
            return ToolResult(
                status="error",
                summary="目标路径存在但不是普通文件",
                data={"path": str(path)},
            )
        if len(args.new_content.encode("utf-8")) > self.max_write_bytes:
            return ToolResult(
                status="error",
                summary=f"写入内容过大，最大支持 {self.max_write_bytes} 字节",
                data={"path": str(path)},
            )
        existed = path.exists()
        old = path.read_text(encoding="utf-8") if existed else ""
        diff = "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                args.new_content.splitlines(keepends=True),
                fromfile=str(path),
                tofile=str(path),
            )
        )
        return {"path": path, "existed": existed, "created": not existed, "diff": diff}


class DraftWriteTool(AllowedRootMixin, BaseTool[DraftWriteArgs]):
    name = "draft_write"
    description = "Create a draft for writing a UTF-8 text file without modifying the target file."
    args_schema = DraftWriteArgs
    risk_level = RiskLevel.WRITE
    requires_permission = True

    def __init__(self, allowed_roots: list[Path], draft_root: Path) -> None:
        super().__init__(allowed_roots)
        self.draft_root = draft_root.resolve()

    async def run(self, args: DraftWriteArgs) -> ToolResult:
        target_path = Path(args.path).expanduser().resolve()
        if not self._is_within_allowed_root(target_path):
            return ToolResult(
                status="error",
                summary="目标文件不在允许访问的目录内",
                data={"path": str(target_path)},
            )

        self.draft_root.mkdir(parents=True, exist_ok=True)
        draft_path = self.draft_root / f"{self._safe_draft_name(target_path)}-{uuid4().hex[:8]}.md"
        draft_path.write_text(
            f"target_path: {target_path}\n\n{args.content}",
            encoding="utf-8",
        )
        return ToolResult(
            status="success",
            summary="已生成写入草稿",
            data={"target_path": str(target_path), "draft_path": str(draft_path)},
        )

    def _safe_draft_name(self, target_path: Path) -> str:
        return "_".join(part for part in target_path.parts if part not in ("/", ""))
