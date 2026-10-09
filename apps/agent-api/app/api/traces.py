from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.tool_management import local_management

router = APIRouter(prefix="/api/traces", tags=["traces"], dependencies=[Depends(local_management)])


def identity(request):
    return {"role": request.headers.get("X-Demo-Role", "consultant"), "actor_id": "demo-consultant"}


@router.get("")
async def list_traces(
    request: Request,
    search: str = Query(default="", max_length=80),
    offset: int = Query(default=0, ge=0, le=10000),
):
    return await request.app.state.trace_client.request(
        "GET", params={**identity(request), "search": search, "offset": offset}
    )


@router.get("/{trace_id}")
async def get_trace(
    trace_id: UUID, request: Request, after_sequence: int = Query(default=0, ge=0, le=256)
):
    return await request.app.state.trace_client.request(
        "GET", f"/{trace_id}", params={**identity(request), "after_sequence": after_sequence}
    )
