import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.citations import EvidenceLedger
from app.core.errors import AppError
from app.main import create_app
from app.rag.corpus import Corpus
from app.rag.service import RagService


class FakeEmbedding:
    dimension = 3
    model_name = "test-vector-only"

    def embed(self, texts, *, query=False):
        return [[1.0, 0.0, 0.0] for _ in texts]


class FakeQdrant:
    def __init__(self):
        self.points = {}
        self.exists = False
        self.empty = False
        self.tamper = False

    def request(self, request):
        if request.method == "GET":
            return (
                httpx.Response(200, json={"result": {"points_count": len(self.points)}})
                if self.exists
                else httpx.Response(404)
            )
        body = json.loads(request.content)
        if request.method == "PUT" and "points" not in body:
            self.exists = True
        elif request.method == "PUT":
            self.points.update({point["id"]: point for point in body["points"]})
        else:
            points = [{**point, "score": 0.8} for point in self.points.values()][: body["limit"]]
            if self.empty:
                points = []
            if self.tamper and points:
                points[0] = {**points[0], "payload": {}}
            return httpx.Response(200, json={"result": {"points": points}})
        return httpx.Response(200, json={"result": True})


def test_corpus_is_stable_exact_and_versioned():
    first, second = Corpus(), Corpus()
    assert first.digest == second.digest
    assert first.chunks == second.chunks
    assert len({chunk.document_id for chunk in first.chunks.values()}) == 3
    for chunk in first.chunks.values():
        from app.rag.corpus import ROOT

        assert chunk.excerpt in (ROOT / chunk.source_path).read_text()
        assert chunk.version == "1.0"


@pytest.mark.asyncio
async def test_index_is_idempotent_empty_and_tampered_results_fail():
    backend = FakeQdrant()
    service = RagService(embedding=FakeEmbedding(), transport=httpx.MockTransport(backend.request))
    with pytest.raises(AppError) as rejected:
        await service.search("续费风险")
    assert rejected.value.code == "SOP_INDEX_NOT_READY"
    first = await service.index()
    assert await service.index() == first
    assert len(backend.points) == len(service.corpus.chunks)
    assert len((await service.search("续费风险", 2))["matches"]) == 2
    backend.empty = True
    assert await service.search("无关问题") == {"matches": []}
    backend.empty, backend.tamper = False, True
    with pytest.raises(AppError) as rejected:
        await service.search("续费风险")
    assert rejected.value.code == "INVALID_SOP_EVIDENCE"


@pytest.mark.asyncio
async def test_qdrant_failure_does_not_claim_empty_results():
    service = RagService(
        embedding=FakeEmbedding(),
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    with pytest.raises(AppError) as rejected:
        await service.search("续费风险")
    assert rejected.value.code == "DEPENDENCY_UNAVAILABLE"


def match_and_source():
    chunk = next(chunk for chunk in Corpus().chunks.values() if "临近续费" in chunk.excerpt)
    match = {key: getattr(chunk, key) for key in ["document_id", "title", "chunk_id", "excerpt"]}
    match["score"] = 0.8
    return match, f"business:{chunk.chunk_id}"


def test_citations_are_exact_and_current_request_only():
    ledger = EvidenceLedger()
    match, source = match_and_source()
    ledger.observe("business", {"matches": [match]})
    message, citations = ledger.validate(
        json.dumps({"answer": f"优先核实工单。[[{source}]]", "sources": [source]})
    )
    assert message == "优先核实工单。[1]"
    assert citations[0]["excerpt"] == match["excerpt"]
    assert citations[0]["version"] == "1.0"
    assert source in ledger.response_instructions()
    assert EvidenceLedger().response_instructions() == ""
    with pytest.raises(AppError):
        EvidenceLedger().validate(json.dumps({"answer": f"引用[[{source}]]", "sources": [source]}))


@pytest.mark.parametrize(
    "value",
    [
        {"answer": "伪造[[fake]]", "sources": ["fake"]},
        {"answer": "没有对应标记", "sources": []},
        {"answer": "伪造片段", "sources": [], "excerpt": "任意内容"},
    ],
)
def test_bad_citations_cannot_be_displayed(value):
    with pytest.raises(AppError):
        EvidenceLedger().validate(json.dumps(value))


def test_modified_excerpt_is_rejected():
    match, _ = match_and_source()
    with pytest.raises(AppError):
        EvidenceLedger().observe("business", {"matches": [{**match, "excerpt": "伪造"}]})


def test_internal_rag_requires_service_identity_and_valid_parameters(settings):
    token = "test-internal-token-at-least-16"
    app = create_app(replace(settings, internal_service_token=token))

    class Search:
        async def search(self, query, top_k):
            return {"matches": []}

    app.state.rag_service = Search()
    client = TestClient(app)
    payload = {"query": "续费风险", "top_k": 2}
    assert client.post("/internal/knowledge/search", json=payload).status_code == 401
    headers = {"X-Internal-Service-Token": token}
    assert client.post("/internal/knowledge/search", json=payload, headers=headers).json() == {
        "matches": []
    }
    assert (
        client.post(
            "/internal/knowledge/search", json={"query": "x", "sql": "x"}, headers=headers
        ).status_code
        == 422
    )
