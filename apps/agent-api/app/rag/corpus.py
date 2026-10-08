from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import yaml
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[4]
SOP_DIRECTORY = ROOT / "data" / "sop"


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: str = Field(pattern=r"^SOP-[A-Z]+-[0-9]{3}$")
    title: str = Field(min_length=1, max_length=120)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+$")


@dataclass(frozen=True)
class Chunk:
    document_id: str
    title: str
    version: str
    chunk_id: str
    excerpt: str
    source_path: str
    content_hash: str

    def payload(self) -> dict:
        return asdict(self)


class Corpus:
    def __init__(self, directory: Path = SOP_DIRECTORY):
        self.chunks: dict[str, Chunk] = {}
        documents = set()
        for path in sorted(directory.glob("*.md")):
            if path.is_symlink() or path.stat().st_size > 65536:
                raise ValueError("SOP 文件无效或过大")
            source = path.read_text(encoding="utf-8")
            if not source.startswith("---\n"):
                raise ValueError("SOP 缺少元数据")
            _, raw_metadata, body = source.split("---\n", 2)
            metadata = Metadata.model_validate(yaml.safe_load(raw_metadata))
            if metadata.document_id in documents:
                raise ValueError("SOP 文档编号重复")
            documents.add(metadata.document_id)
            # Sections retain exact original text; long sections are bounded slices.
            for section in re.split(r"(?=^## )", body, flags=re.MULTILINE):
                section = section.strip()
                for offset in range(0, len(section), 700):
                    excerpt = section[offset : offset + 700]
                    digest = hashlib.sha256(excerpt.encode()).hexdigest()
                    chunk_id = str(
                        uuid5(NAMESPACE_URL, f"{metadata.document_id}:{metadata.version}:{digest}")
                    )
                    self.chunks[chunk_id] = Chunk(
                        metadata.document_id,
                        metadata.title,
                        metadata.version,
                        chunk_id,
                        excerpt,
                        f"data/sop/{path.name}",
                        digest,
                    )
        if not self.chunks or len(self.chunks) > 200:
            raise ValueError("SOP 语料为空或超出 MVP 范围")
        self.digest = hashlib.sha256(
            repr([chunk.payload() for chunk in self.chunks.values()]).encode()
        ).hexdigest()
