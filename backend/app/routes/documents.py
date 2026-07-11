import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Document, DocumentPermission, User
from app.routes.auth import get_current_user
from app.schemas import DocumentCreate, DocumentListItem, DocumentResponse
from app.utils import generate_key, generate_moniker, generate_cursor_color

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/", response_model=DocumentResponse, status_code=201)
async def create_document(
    body: DocumentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc = Document(
        title=body.title, edit_key=generate_key(),
        view_key=generate_key(), creator_id=current_user.id,
    )
    db.add(doc)
    await db.flush()
    perm = DocumentPermission(
        document_id=doc.id, user_id=current_user.id,
        role="creator", moniker=generate_moniker(),
        cursor_color=generate_cursor_color(),
    )
    db.add(perm)
    await db.flush()
    return doc


@router.get("/")
async def list_documents(
    tab: str = "created",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if tab == "created":
        result = await db.execute(
            select(Document).where(Document.creator_id == current_user.id)
            .order_by(Document.updated_at.desc())
        )
        docs = result.scalars().all()
        return [DocumentListItem(
            id=d.id, title=d.title, edit_key=d.edit_key,
            view_key=d.view_key, role="creator",
            created_at=d.created_at, updated_at=d.updated_at,
        ) for d in docs]
    elif tab == "edited":
        result = await db.execute(
            select(Document, DocumentPermission.role)
            .join(DocumentPermission, DocumentPermission.document_id == Document.id)
            .where(DocumentPermission.user_id == current_user.id,
                   DocumentPermission.role == "editor")
            .order_by(Document.updated_at.desc())
        )
        return [DocumentListItem(
            id=d.id, title=d.title, edit_key=d.edit_key,
            view_key=d.view_key, role=role,
            created_at=d.created_at, updated_at=d.updated_at,
        ) for d, role in result.all()]
    else:
        result = await db.execute(
            select(Document, DocumentPermission.role)
            .join(DocumentPermission, DocumentPermission.document_id == Document.id)
            .where(DocumentPermission.user_id == current_user.id)
            .order_by(Document.updated_at.desc())
        )
        return [DocumentListItem(
            id=d.id, title=d.title, edit_key=d.edit_key,
            view_key=d.view_key, role=role,
            created_at=d.created_at, updated_at=d.updated_at,
        ) for d, role in result.all()]


@router.get("/by-edit-key/{edit_key}", response_model=DocumentResponse)
async def get_by_edit_key(edit_key: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.edit_key == edit_key))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/by-view-key/{view_key}", response_model=DocumentResponse)
async def get_by_view_key(view_key: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.view_key == view_key))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: UUID, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    perm = await db.execute(
        select(DocumentPermission).where(
            DocumentPermission.document_id == doc_id,
            DocumentPermission.user_id == current_user.id))
    if not perm.scalar_one_or_none() and doc.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return doc


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: UUID, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only creator can delete")
    await db.delete(doc)


@router.patch("/{doc_id}/title")
async def update_title(
    doc_id: UUID, body: DocumentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only creator can rename")
    doc.title = body.title
    return {"title": doc.title}
