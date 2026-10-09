from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.tool_management import local_management

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class CreateTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID


class TaskDecision(CreateTask):
    decision: Literal["approve", "reject", "cancel"]
    expected_version: int = Field(ge=1, strict=True)


def identity(request):
    local_management(request)
    return request.headers.get("X-Demo-Role", "consultant"), "demo-consultant"


@router.post("")
async def create_task(payload: CreateTask, request: Request):
    role, actor = identity(request)
    return await request.app.state.task_service.submit(role, actor, str(payload.idempotency_key))


@router.get("")
async def list_tasks(request: Request):
    role, actor = identity(request)
    return await request.app.state.task_client.list(role, actor)


@router.get("/{task_id}")
async def get_task(task_id: UUID, request: Request):
    role, actor = identity(request)
    return await request.app.state.task_client.get(str(task_id), role, actor)


@router.get("/{task_id}/audit")
async def get_audit(task_id: UUID, request: Request):
    role, actor = identity(request)
    return await request.app.state.task_client.get(str(task_id), role, actor, audit=True)


@router.post("/{task_id}/decision")
async def decide(task_id: UUID, payload: TaskDecision, request: Request):
    role, actor = identity(request)
    return await request.app.state.task_client.decide(
        str(task_id), role, actor, payload.model_dump(mode="json")
    )
