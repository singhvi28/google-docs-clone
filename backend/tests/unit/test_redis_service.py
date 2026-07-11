import pytest

from app.services import redis_service


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}
        self.hashes = {}
        self.expirations = {}
        self.published = []
        self.closed = False

    async def set(self, key, value):
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)
        self.hashes.pop(key, None)
        self.lists.pop(key, None)

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start : end + 1]

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    async def decr(self, key):
        self.values[key] = int(self.values.get(key, 0)) - 1
        return self.values[key]

    async def publish(self, channel, data):
        self.published.append((channel, data))

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field.encode()] = value.encode()

    async def expire(self, key, seconds):
        self.expirations[key] = seconds

    async def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field.encode(), None)

    async def hgetall(self, key):
        return self.hashes.get(key, {})

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_service, "_redis_pool", fake)
    return fake


@pytest.mark.asyncio
async def test_append_and_merge_crdt_updates(fake_redis):
    from pycrdt import Doc, Text

    d1 = Doc()
    d1.get("content", type=Text).insert(0, "Hello")
    u1 = d1.get_update()

    d2 = Doc()
    d2.apply_update(u1)
    state_vec = d2.get_state()
    d2.get("content", type=Text).insert(5, " World")
    incremental = d2.get_update(state_vec)

    await redis_service.append_crdt_update("edit-1", u1)
    await redis_service.append_crdt_update("edit-1", incremental)

    updates = await redis_service.get_crdt_updates("edit-1")
    assert len(updates) == 2

    merged = await redis_service.get_merged_crdt_state("edit-1")
    assert merged is not None

    out = Doc()
    out.apply_update(merged)
    assert str(out.get("content", type=Text)) == "Hello World"


@pytest.mark.asyncio
async def test_cache_get_and_delete_crdt_state(fake_redis):
    await redis_service.cache_crdt_state("edit-1", b"state")

    assert await redis_service.get_cached_crdt_state("edit-1") == b"state"

    await redis_service.delete_crdt_state("edit-1")

    assert await redis_service.get_cached_crdt_state("edit-1") is None


@pytest.mark.asyncio
async def test_seed_crdt_state_if_empty(fake_redis):
    await redis_service.seed_crdt_state_if_empty("edit-1", b"baseline")
    await redis_service.seed_crdt_state_if_empty("edit-1", b"ignored")

    assert await redis_service.get_crdt_updates("edit-1") == [b"baseline"]


@pytest.mark.asyncio
async def test_editor_count_increments_decrements_and_cleans_up(fake_redis):
    assert await redis_service.get_active_editor_count("edit-1") == 0
    assert await redis_service.increment_editor_count("edit-1") == 1
    assert await redis_service.increment_editor_count("edit-1") == 2
    assert await redis_service.decrement_editor_count("edit-1") == 1
    assert await redis_service.decrement_editor_count("edit-1") == 0
    assert await redis_service.get_active_editor_count("edit-1") == 0


@pytest.mark.asyncio
async def test_pending_editors_are_stored_decoded_and_removed(fake_redis):
    await redis_service.add_pending_editor("edit-1", "user-1", "swiftphoenix")

    assert fake_redis.expirations["doc:pending:edit-1"] == 600
    assert await redis_service.get_pending_editors("edit-1") == {
        "user-1": "swiftphoenix"
    }

    await redis_service.remove_pending_editor("edit-1", "user-1")

    assert await redis_service.get_pending_editors("edit-1") == {}


@pytest.mark.asyncio
async def test_publish_update_and_close_redis(fake_redis):
    await redis_service.publish_update("edit-1", b"update")

    assert fake_redis.published == [("channel:edit-1", b"update")]

    await redis_service.close_redis()

    assert fake_redis.closed is True
    assert redis_service._redis_pool is None
