from dataclasses import dataclass, field
from datetime import datetime, timezone
import itertools
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app
from app.models import Document, DocumentPermission, User
from app.routes.auth import get_current_user
from app.routes import collab, viewer


@dataclass
class FakeStore:
    users: list[User] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    permissions: list[DocumentPermission] = field(default_factory=list)


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        if not self._rows:
            raise AssertionError("Expected one row, got none")
        if len(self._rows) > 1:
            raise AssertionError(f"Expected one row, got {len(self._rows)}")
        return self._rows[0]

    def scalars(self):
        return FakeScalarResult(self._rows)

    def all(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def add(self, instance):
        self._apply_defaults(instance)
        collection = self._collection_for(instance)
        if not any(existing.id == instance.id for existing in collection):
            collection.append(instance)

    async def delete(self, instance):
        if isinstance(instance, Document):
            self.store.documents = [
                doc for doc in self.store.documents if doc.id != instance.id
            ]
            self.store.permissions = [
                perm
                for perm in self.store.permissions
                if perm.document_id != instance.id
            ]
        elif isinstance(instance, User):
            self.store.users = [user for user in self.store.users if user.id != instance.id]
        elif isinstance(instance, DocumentPermission):
            self.store.permissions = [
                perm for perm in self.store.permissions if perm.id != instance.id
            ]

    async def execute(self, statement):
        descriptions = statement.column_descriptions
        entities = [description.get("entity") for description in descriptions]
        expressions = [description.get("expr") for description in descriptions]

        if entities == [User]:
            return FakeResult(self._filter(self.store.users, statement))

        if entities == [Document]:
            rows = self._filter(self.store.documents, statement)
            return FakeResult(self._sort_documents(rows, statement))

        if entities == [DocumentPermission]:
            return FakeResult(self._filter(self.store.permissions, statement))

        if entities == [Document, DocumentPermission] and expressions[1].key == "role":
            rows = []
            for doc in self.store.documents:
                for permission in self.store.permissions:
                    if permission.document_id == doc.id:
                        if self._matches(statement, doc, permission):
                            rows.append((doc, permission.role))
            return FakeResult(self._sort_document_rows(rows, statement))

        raise NotImplementedError(f"Unsupported fake query: {statement}")

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def close(self):
        return None

    def _collection_for(self, instance):
        if isinstance(instance, User):
            return self.store.users
        if isinstance(instance, Document):
            return self.store.documents
        if isinstance(instance, DocumentPermission):
            return self.store.permissions
        raise TypeError(f"Unsupported model: {type(instance)!r}")

    def _apply_defaults(self, instance):
        now = datetime.now(timezone.utc)
        if getattr(instance, "id", None) is None:
            instance.id = uuid.uuid4()
        if isinstance(instance, User) and instance.created_at is None:
            instance.created_at = now
        if isinstance(instance, Document):
            if instance.created_at is None:
                instance.created_at = now
            if instance.updated_at is None:
                instance.updated_at = now
        if isinstance(instance, DocumentPermission) and instance.granted_at is None:
            instance.granted_at = now

    def _filter(self, rows, statement):
        return [row for row in rows if self._matches(statement, row)]

    def _matches(self, statement, *models):
        for criterion in statement._where_criteria:
            table_name = criterion.left.table.name
            attr_name = criterion.left.key
            expected = getattr(criterion.right, "value", None)
            model = self._model_for_table(table_name, models)
            if model is None:
                return False
            actual = getattr(model, attr_name)
            if str(actual) != str(expected):
                return False
        return True

    def _model_for_table(self, table_name, models):
        for model in models:
            if model.__tablename__ == table_name:
                return model
        return None

    def _sort_documents(self, rows, statement):
        if statement._order_by_clauses:
            return sorted(rows, key=lambda row: row.updated_at, reverse=True)
        return rows

    def _sort_document_rows(self, rows, statement):
        if statement._order_by_clauses:
            return sorted(rows, key=lambda row: row[0].updated_at, reverse=True)
        return rows


class FakeSessionFactory:
    def __init__(self, store):
        self.store = store

    def __call__(self):
        return FakeSession(self.store)


@pytest.fixture
def session_factory():
    return FakeSessionFactory(FakeStore())


@pytest.fixture
def auth_state():
    return {"user": None}


@pytest.fixture
def set_current_user(auth_state):
    def _set(user):
        auth_state["user"] = user

    return _set


@pytest_asyncio.fixture
async def client(session_factory, auth_state, monkeypatch):
    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_current_user():
        user = auth_state["user"]
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_current_user
    monkeypatch.setattr(viewer, "async_session_factory", session_factory)
    monkeypatch.setattr(collab, "async_session_factory", session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def make_user(session_factory):
    counter = itertools.count(1)

    async def _make_user(
        *,
        google_id=None,
        email=None,
        display_name="Test User",
        avatar_url=None,
    ):
        index = next(counter)
        user = User(
            google_id=google_id or f"google-{index}",
            email=email or f"user{index}@example.com",
            display_name=display_name,
            avatar_url=avatar_url,
        )
        async with session_factory() as session:
            session.add(user)
            await session.commit()
        return user

    return _make_user


@pytest.fixture
def make_document(session_factory):
    counter = itertools.count(1)

    async def _make_document(
        *,
        creator,
        title=None,
        edit_key=None,
        view_key=None,
        add_creator_permission=True,
    ):
        index = next(counter)
        doc = Document(
            title=title or f"Document {index}",
            edit_key=edit_key or f"edit-key-{index}",
            view_key=view_key or f"view-key-{index}",
            creator_id=creator.id,
        )
        async with session_factory() as session:
            session.add(doc)
            await session.flush()
            if add_creator_permission:
                session.add(
                    DocumentPermission(
                        document_id=doc.id,
                        user_id=creator.id,
                        role="creator",
                        moniker=f"creator{index}",
                        cursor_color="#FF6B6B",
                    )
                )
            await session.commit()
        return doc

    return _make_document


@pytest.fixture
def make_permission(session_factory):
    async def _make_permission(
        *,
        document,
        user,
        role="editor",
        moniker="editor",
        cursor_color="#4ECDC4",
    ):
        permission = DocumentPermission(
            document_id=document.id,
            user_id=user.id,
            role=role,
            moniker=moniker,
            cursor_color=cursor_color,
        )
        async with session_factory() as session:
            session.add(permission)
            await session.commit()
        return permission

    return _make_permission
