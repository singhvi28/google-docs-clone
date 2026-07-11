import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ─── Auth ─────────────────────────────────────────────
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    avatar_url: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Documents ────────────────────────────────────────
class DocumentCreate(BaseModel):
    title: str = Field(default="Untitled Document", max_length=255)


class DocumentResponse(BaseModel):
    id: uuid.UUID
    title: str
    edit_key: str
    view_key: str
    creator_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentListItem(BaseModel):
    id: uuid.UUID
    title: str
    edit_key: str
    view_key: str
    role: str  # 'creator', 'editor', 'viewer'
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─── Collaboration ────────────────────────────────────
class EditorInfo(BaseModel):
    user_id: uuid.UUID
    moniker: str
    cursor_color: str
    role: str


class ApprovalRequest(BaseModel):
    user_id: uuid.UUID
    moniker: str
    document_id: uuid.UUID


class ApprovalDecision(BaseModel):
    user_id: uuid.UUID
    approved: bool
