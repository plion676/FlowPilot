from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import math
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.core.errors import AppError
from app.rag.corpus import ROOT, Corpus

EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"


class LocalEmbedding:
    dimension = 512
    model_name = EMBEDDING_MODEL

    def __init__(self, cache: Path = ROOT / ".cache" / "fastembed", *, allow_download=False):
        self.cache = cache
        self.allow_download = allow_download
        self._model = None
        self._lock = threading.Lock()

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        # Load only when indexing/searching, never on health/config requests.
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=str(self.cache),
                    threads=2,
                    local_files_only=not self.allow_download,
                )
            values = self._model.query_embed(texts) if query else self._model.passage_embed(texts)
            return [value.tolist() for value in values]


class RagService:
    def __init__(self, corpus=None, embedding=None, url=None, transport=None):
        self.corpus = corpus or Corpus()
        self.embedding = embedding or LocalEmbedding()
        self.url = url or os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
        parsed = urlsplit(self.url)
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
            valid = (address.is_loopback or address.is_private) and not (
                address.is_link_local or address.is_unspecified or address.is_multicast
            )
            _ = parsed.port
        except ValueError:
            valid = False
        if (
            not valid
            or parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("QDRANT_URL 必须是运维配置的本地/私有 IP 基址")
        self.transport = transport
        fingerprint = hashlib.sha256(
            f"{self.corpus.digest}:{self.embedding.model_name}:chunk-v1".encode()
        ).hexdigest()[:20]
        self.collection = f"opspilot_sop_{fingerprint}"

    async def _request(self, method: str, path: str, payload=None, *, missing=False):
        try:
            async with httpx.AsyncClient(
                timeout=8,
                trust_env=False,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.request(method, self.url.rstrip("/") + path, json=payload)
                if response.status_code == 404:
                    if missing:
                        return None
                    raise AppError(
                        "SOP_INDEX_NOT_READY", "SOP 尚未建立索引，请先运行 index-sop。", 503
                    )
                response.raise_for_status()
                if len(response.content) > 1048576:
                    raise ValueError("Qdrant 响应过大")
                return response.json()
        except AppError:
            raise
        except Exception as error:
            raise AppError(
                "DEPENDENCY_UNAVAILABLE", "SOP 向量检索服务暂不可用。", 503, True
            ) from error

    async def _embed(self, texts, *, query=False):
        try:
            async with asyncio.timeout(
                600 if getattr(self.embedding, "allow_download", False) else 10
            ):
                return await asyncio.to_thread(self.embedding.embed, texts, query=query)
        except Exception as error:
            raise AppError(
                "EMBEDDING_UNAVAILABLE", "本地 Embedding 不可用，请检查模型缓存。", 503
            ) from error

    async def index(self) -> dict:
        chunks = list(self.corpus.chunks.values())
        vectors = await self._embed([chunk.excerpt for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("向量数量不一致")
        path = f"/collections/{self.collection}"
        if await self._request("GET", path, missing=True) is None:
            await self._request(
                "PUT", path, {"vectors": {"size": self.embedding.dimension, "distance": "Cosine"}}
            )
        await self._request(
            "PUT",
            path + "/points?wait=true",
            {
                "points": [
                    {"id": chunk.chunk_id, "vector": vector, "payload": chunk.payload()}
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ]
            },
        )
        return {
            "collection": self.collection,
            "chunks": len(chunks),
            "model": self.embedding.model_name,
            "corpus_hash": self.corpus.digest,
        }

    async def search(self, query: str, top_k: int = 5) -> dict:
        # Confirm the complete current corpus is indexed before claiming empty search.
        collection = await self._request("GET", f"/collections/{self.collection}")
        if collection["result"].get("points_count", 0) != len(self.corpus.chunks):
            raise AppError("SOP_INDEX_NOT_READY", "当前 SOP 索引尚未完成。", 503)
        vectors = await self._embed([query], query=True)
        result = await self._request(
            "POST",
            f"/collections/{self.collection}/points/query",
            {
                "query": vectors[0],
                "limit": top_k,
                "with_payload": True,
                "score_threshold": 0.55,
            },
        )
        hits = []
        for point in result["result"]["points"]:
            chunk = self.corpus.chunks.get(str(point["id"]))
            if not chunk or point.get("payload") != chunk.payload():
                raise AppError("INVALID_SOP_EVIDENCE", "索引内容与当前 SOP 源文件不一致。", 503)
            score = float(point["score"])
            if not math.isfinite(score):
                raise AppError("INVALID_SOP_EVIDENCE", "检索分数无效。", 503)
            hits.append(
                {
                    "document_id": chunk.document_id,
                    "title": chunk.title,
                    "chunk_id": chunk.chunk_id,
                    "excerpt": chunk.excerpt,
                    "score": min(1.0, max(0.0, score)),
                }
            )
        return {"matches": hits}
