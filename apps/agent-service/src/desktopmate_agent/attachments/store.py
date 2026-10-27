"""会话附件存储：图片 + 可注入上下文的文档。

图片文件落盘到 data/uploads/，元数据写入同目录 index.json（单用户桌面应用，
集中 JSON 索引足够简单可靠）。附件 id 即文件名（不含扩展名）。
文档附件（md/txt/pdf/docx）保存后可通过 content_text() 读取文本，
供注入对话上下文（方案 B：基于上传文件直接对话）。
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from desktopmate_agent.api.errors import ApiError

ALLOWED_IMAGE_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# 可注入上下文的文档附件类型：mime → 扩展名
ALLOWED_DOCUMENT_TYPES: dict[str, str] = {
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/plain": ".txt",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024  # 20MB
# 注入对话上下文的单文件文本上限（超出截断，避免 prompt 爆炸）
MAX_DOCUMENT_CONTEXT_CHARS = 60_000


@dataclass
class AttachmentRecord:
    id: str
    filename: str
    mime: str
    size: int
    created_at: str


class AttachmentStore:
    def __init__(self, uploads_dir: Path) -> None:
        self.uploads_dir = uploads_dir
        self.index_path = uploads_dir / "index.json"
        self._lock = threading.Lock()
        self._index: dict[str, dict[str, object]] = {}
        self._load()

    def _load(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        if self.index_path.exists():
            try:
                raw = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._index = {k: v for k, v in raw.items() if isinstance(v, dict)}
            except (json.JSONDecodeError, OSError):
                self._index = {}

    def _save_index(self) -> None:
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.index_path)

    def save(self, *, filename: str, mime: str, content: bytes) -> AttachmentRecord:
        ext = ALLOWED_IMAGE_TYPES.get(mime) or ALLOWED_DOCUMENT_TYPES.get(mime)
        is_image = mime in ALLOWED_IMAGE_TYPES
        if ext is None:
            raise ApiError(
                code="unsupported_attachment_type",
                message="不支持的附件类型：仅支持图片（PNG/JPEG/WebP/GIF）和文档（md/txt/pdf/docx）",
            )
        if not content:
            raise ApiError(code="empty_attachment", message="附件内容为空")
        limit = MAX_IMAGE_BYTES if is_image else MAX_DOCUMENT_BYTES
        kind = "图片" if is_image else "文档"
        if len(content) > limit:
            raise ApiError(
                code="attachment_too_large",
                message=f"{kind}不能超过 {limit // 1024 // 1024}MB",
            )
        attachment_id = uuid.uuid4().hex
        with self._lock:
            target = self.uploads_dir / f"{attachment_id}{ext}"
            target.write_bytes(content)
            record = AttachmentRecord(
                id=attachment_id,
                filename=filename or f"attachment{ext}",
                mime=mime,
                size=len(content),
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            self._index[attachment_id] = {
                "filename": record.filename,
                "mime": record.mime,
                "size": record.size,
                "created_at": record.created_at,
                "ext": ext,
            }
            self._save_index()
        return record

    def content_text(self, attachment_id: str, *, limit: int = MAX_DOCUMENT_CONTEXT_CHARS) -> str:
        """读取文档附件的纯文本（供注入对话上下文）。图片/不支持类型返回空串。"""
        record = self.get(attachment_id)
        path = self.path_for(attachment_id)
        if record is None or path is None:
            return ""
        mime = (record.mime or "").lower()
        if mime in ALLOWED_IMAGE_TYPES:
            return ""
        try:
            if mime == "application/pdf":
                return self._extract_pdf_text(path)[:limit]
            if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                return self._extract_docx_text(path)[:limit]
            # md / txt：直接按 UTF-8 读，失败降级 latin-1
            try:
                return path.read_text(encoding="utf-8")[:limit]
            except UnicodeDecodeError:
                return path.read_text(encoding="latin-1")[:limit]
        except OSError:
            return ""

    @staticmethod
    def _extract_pdf_text(path: Path) -> str:
        """极简 PDF 文本抽取：pypdf 可用则用，否则退化为空（调用方容忍）。"""
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]
        except ImportError:
            return ""
        try:
            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return ""

    @staticmethod
    def _extract_docx_text(path: Path) -> str:
        """极简 docx 文本抽取：python-docx 可用则用，否则退化为空。"""
        try:
            import docx  # type: ignore[import-not-found]
        except ImportError:
            return ""
        try:
            document = docx.Document(str(path))
            return "\n".join(paragraph.text for paragraph in document.paragraphs)
        except Exception:
            return ""

    def get(self, attachment_id: str) -> AttachmentRecord | None:
        meta = self._index.get(attachment_id)
        if meta is None:
            # 其他实例/进程可能已写入新附件：重载磁盘索引再查一次
            self._load()
            meta = self._index.get(attachment_id)
            if meta is None:
                return None
        ext = str(meta.get("ext", ""))
        path = self.uploads_dir / f"{attachment_id}{ext}"
        if not path.exists():
            return None
        return AttachmentRecord(
            id=attachment_id,
            filename=str(meta.get("filename", f"image{ext}")),
            mime=str(meta.get("mime", "application/octet-stream")),
            size=int(meta.get("size", 0)),
            created_at=str(meta.get("created_at", "")),
        )

    def path_for(self, attachment_id: str) -> Path | None:
        meta = self._index.get(attachment_id)
        if meta is None:
            # 兜底：重载磁盘索引（见 get）
            self._load()
            meta = self._index.get(attachment_id)
            if meta is None:
                return None
        path = self.uploads_dir / f"{attachment_id}{meta.get('ext', '')}"
        return path if path.exists() else None

    def delete(self, attachment_id: str) -> bool:
        with self._lock:
            if attachment_id not in self._index:
                return False
            path = self.path_for(attachment_id)
            if path is not None:
                path.unlink(missing_ok=True)
            del self._index[attachment_id]
            self._save_index()
            return True

    def list_ids(self) -> list[str]:
        """索引中的全部附件 id（供孤儿附件清理使用）。"""
        with self._lock:
            return list(self._index.keys())
