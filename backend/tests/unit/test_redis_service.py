import asyncio
import json

import pytest

from app.services import redis_service
from app.services import awareness_batcher


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}
        self.hashes = {}
        self.sets = {}
        self.expirations = {}
        self.published = []
        self.closed = False

    async def set(self, key, value, nx=False, ex=None):
        if nx and (
            key in self.values
            or key in self.lists
            or key in self.hashes
            or key in self.sets
        ):
            return None
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def get(self, key):
        return self.values.get(key)

    async def exists(self, key):
        present = (
            key in self.values
            or key in self.lists
            or key in self.hashes
            or key in self.sets
        )
        return 1 if present else 0

    async def delete(self, key):
        self.values.pop(key, None)
        self.hashes.pop(key, None)
        self.lists.pop(key, None)
        self.sets.pop(key, None)
        self.expirations.pop(key, None)

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

    def _as_bytes(self, member):
        if isinstance(member, bytes):
            return member
        return str(member).encode()

    async def sadd(self, key, *members):
        s = self.sets.setdefault(key, set())
        before = len(s)
        for m in members:
            s.add(self._as_bytes(m))
        return len(s) - before

    async def srem(self, key, *members):
        s = self.sets.get(key, set())
        removed = 0
        for m in members:
            mb = self._as_bytes(m)
            if mb in s:
                s.discard(mb)
                removed += 1
        if key in self.sets and not self.sets[key]:
            del self.sets[key]
        return removed

    async def scard(self, key):
        return len(self.sets.get(key, set()))

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def sismember(self, key, member):
        return self._as_bytes(member) in self.sets.get(key, set())

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
    assert "edit-1" in await redis_service.get_dirty_documents()


def test_merge_crdt_updates_skips_corrupt_frames_without_crashing():
    """
    Regression: reflector/stress markers used to land in the Redis update log as
    non-Yjs bytes. The old merge loop called apply_update unconditionally and
    pycrdt raised PanicException (EndOfBuffer), crashing the event loop on join/flush.

    This would FAIL on the earlier unguarded merge_crdt_updates implementation.
    """
    from pycrdt import Doc, Text

    baseline = Doc()
    baseline.get("content", type=Text).insert(0, "Hello")
    u1 = bytes(baseline.get_update())

    follow = Doc()
    follow.apply_update(u1)
    state_vec = follow.get_state()
    follow.get("content", type=Text).insert(5, " World")
    u2 = bytes(follow.get_update(state_vec))

    corrupt = b"STRESS_MARKER:deadbeef"  # same shape as reflector latency markers
    truncated = b"\x00\x01\xff"  # truncated binary frame

    # Must not raise — old code panicked here on the corrupt entry
    merged = redis_service.merge_crdt_updates([u1, corrupt, truncated, u2])

    assert merged is not None
    out = Doc()
    out.apply_update(merged)
    assert str(out.get("content", type=Text)) == "Hello World"


def test_merge_crdt_updates_returns_none_when_all_frames_are_corrupt():
    assert redis_service.merge_crdt_updates(
        [b"STRESS_MARKER:x", b"\x00\xff", b"not-yjs"]
    ) is None


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
async def test_editor_sessions_register_heartbeat_and_unregister(fake_redis):
    assert await redis_service.get_active_editor_count("edit-1") == 0
    assert await redis_service.register_editor_session("edit-1", "s1") == 1
    assert await redis_service.register_editor_session("edit-1", "s2") == 2
    assert fake_redis.expirations["doc:session:edit-1:s1"] == redis_service.settings.SESSION_TTL_SECONDS

    assert await redis_service.heartbeat_editor_session("edit-1", "s1") is True
    assert await redis_service.unregister_editor_session("edit-1", "s1") == 1
    assert await redis_service.unregister_editor_session("edit-1", "s2") == 0
    assert await redis_service.get_active_editor_count("edit-1") == 0


@pytest.mark.asyncio
async def test_editor_count_increments_decrements_and_cleans_up(fake_redis):
    assert await redis_service.get_active_editor_count("edit-1") == 0
    assert await redis_service.increment_editor_count("edit-1") == 1
    assert await redis_service.increment_editor_count("edit-1") == 2
    assert await redis_service.decrement_editor_count("edit-1") == 1
    assert await redis_service.decrement_editor_count("edit-1") == 0
    assert await redis_service.get_active_editor_count("edit-1") == 0


@pytest.mark.asyncio
async def test_sweep_stale_editor_sessions(fake_redis):
    await redis_service.register_editor_session("edit-1", "alive")
    await redis_service.register_editor_session("edit-1", "dead")
    # Simulate TTL expiry for one session key
    await fake_redis.delete("doc:session:edit-1:dead")

    empty = await redis_service.sweep_stale_editor_sessions()
    assert empty == []
    assert await redis_service.get_active_editor_count("edit-1") == 1

    await fake_redis.delete("doc:session:edit-1:alive")
    empty = await redis_service.sweep_stale_editor_sessions()
    assert empty == ["edit-1"]
    assert await redis_service.get_active_editor_count("edit-1") == 0


@pytest.mark.asyncio
async def test_checkpoint_compacts_log_at_threshold(fake_redis, monkeypatch):
    monkeypatch.setattr(redis_service.settings, "CRDT_CHECKPOINT_THRESHOLD", 5)

    from pycrdt import Doc, Text

    doc = Doc()
    text = doc.get("content", type=Text)
    for i in range(5):
        state_vec = doc.get_state()
        text.insert(len(str(text)), str(i))
        update = doc.get_update() if i == 0 else doc.get_update(state_vec)
        await redis_service.append_crdt_update("edit-cp", update)

    updates = await redis_service.get_crdt_updates("edit-cp")
    assert len(updates) == 1


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


@pytest.mark.asyncio
async def test_awareness_batcher_coalesces_publishes(fake_redis, monkeypatch):
    monkeypatch.setattr(awareness_batcher.settings, "AWARENESS_BATCH_WINDOW_MS", 20)
    await awareness_batcher.reset_batcher_for_tests()

    await awareness_batcher.enqueue_awareness("edit-1", "a", "c1")
    await awareness_batcher.enqueue_awareness("edit-1", "b", "c2")
    await asyncio.sleep(0.05)

    assert len(fake_redis.published) == 1
    channel, raw = fake_redis.published[0]
    assert channel == "channel:edit-1"
    payload = json.loads(raw.decode())
    assert payload["type"] == "awareness_batch"
    assert len(payload["cursors"]) == 2

    frames = awareness_batcher.expand_awareness_messages(payload, "c1")
    assert frames == [{"type": "awareness", "data": "b"}]
