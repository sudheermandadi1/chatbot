
from uuid import UUID
from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: UUID | None = None
    dataset_id: UUID | None = None


class ChatResponse(BaseModel):
    conversation_id: UUID
    reply: str
    pending_mutations: dict | None = None
