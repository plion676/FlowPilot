"""Only current-request, exact corpus excerpts may become displayed citations."""

import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import AppError
from app.rag.corpus import Corpus


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str = Field(min_length=1, max_length=16000)
    sources: list[str] = Field(max_length=20)


class EvidenceLedger:
    def __init__(self, corpus=None):
        self.corpus = corpus or Corpus()
        self.items = {}

    def observe(self, endpoint_id: str, result: dict) -> dict:
        enriched = []
        for match in result["matches"]:
            chunk = self.corpus.chunks.get(match["chunk_id"])
            if not chunk or any(
                match[key] != getattr(chunk, key) for key in ["document_id", "title", "excerpt"]
            ):
                raise AppError("INVALID_SOP_EVIDENCE", "SOP 证据与源文档不一致。", 503)
            source_id = f"{endpoint_id}:{chunk.chunk_id}"
            self.items[source_id] = {
                **match,
                "endpoint_id": endpoint_id,
                "source_id": source_id,
                "version": chunk.version,
                "source_path": chunk.source_path,
            }
            enriched.append({**match, "source_id": source_id})
        return {"matches": enriched}

    def validate(self, raw: str) -> tuple[str, list[dict]]:
        try:
            text = raw.strip()
            if text.startswith("```json\n") and text.endswith("```"):
                text = text[8:-3].strip()
            answer = GroundedAnswer.model_validate(json.loads(text))
            if len(answer.sources) != len(set(answer.sources)) or not answer.sources:
                raise ValueError("引用重复或为空")
            if any(source not in self.items for source in answer.sources):
                raise ValueError("引用不属于当前请求")
            markers = re.findall(r"\[\[([^\]]+)\]\]", answer.answer)
            if set(markers) != set(answer.sources):
                raise ValueError("回答引用与来源集合不一致")
            message = answer.answer
            for number, source in enumerate(answer.sources, 1):
                message = message.replace(f"[[{source}]]", f"[{number}]")
            return message, [self.items[source] for source in answer.sources]
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            raise AppError("UNGROUNDED_ANSWER", "回答未通过当前 SOP 证据校验。", 422) from error

    def response_instructions(self) -> str:
        if not self.items:
            return ""
        return (
            "当前已经取得 SOP 证据。最终消息必须是合法 JSON，仅包含 answer 和 sources，"
            "不能在 JSON 前后添加解释。answer 中每项 SOP 建议使用 [[完整source_id]] 标记，"
            "sources 选择下面的真实 ID 并与标记完全一致。不要使用 [1] 或占位符 source_id；"
            "编号由服务器生成。本轮可用 source_id："
            + json.dumps(list(self.items), ensure_ascii=False)
        )
