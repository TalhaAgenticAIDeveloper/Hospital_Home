"""
Tests for GroqQueueService — Centralized Queue & Retry Mechanism.
"""

import asyncio
from unittest.mock import AsyncMock, patch
import pytest

from app.core.exceptions import ValidationError
from app.services.groq_queue_service import (
    GroqPriority,
    GroqQueueService,
    GroqRateLimitError,
)


@pytest.fixture
async def queue_service():
    """Create a fresh isolated GroqQueueService instance for testing."""
    service = GroqQueueService(
        max_size=50,
        inter_request_delay=0.01,
        default_enqueue_retries=3,
        default_exec_retries=3,
    )
    await service.start_worker()
    yield service
    await service.stop_worker()


@pytest.mark.asyncio
async def test_queue_sequential_execution(queue_service):
    """Verify that queued jobs execute in orderly FIFO sequence."""
    execution_order = []

    async def make_job(idx: int):
        async def _job():
            execution_order.append(f"start_{idx}")
            await asyncio.sleep(0.02)
            execution_order.append(f"end_{idx}")
            return f"result_{idx}"
        return _job

    # Submit 3 jobs in sequence
    t1 = asyncio.create_task(queue_service.submit(await make_job(1), priority=GroqPriority.NORMAL, caller="Test1"))
    t2 = asyncio.create_task(queue_service.submit(await make_job(2), priority=GroqPriority.NORMAL, caller="Test2"))
    t3 = asyncio.create_task(queue_service.submit(await make_job(3), priority=GroqPriority.NORMAL, caller="Test3"))

    r1, r2, r3 = await asyncio.gather(t1, t2, t3)

    assert r1 == "result_1"
    assert r2 == "result_2"
    assert r3 == "result_3"
    # Ensure sequential non-overlapping execution
    assert execution_order == ["start_1", "end_1", "start_2", "end_2", "start_3", "end_3"]


@pytest.mark.asyncio
async def test_queue_priority_ordering(queue_service):
    """Verify that HIGH priority items jump ahead of NORMAL priority items in queue."""
    order = []

    # Pause worker temporarily by submitting a slow blocker
    async def blocker():
        await asyncio.sleep(0.1)
        return "blocker_done"

    async def normal_job():
        order.append("normal")
        return "normal"

    async def high_job():
        order.append("high")
        return "high"

    blocker_task = asyncio.create_task(queue_service.submit(blocker, priority=GroqPriority.LOW, caller="Blocker"))

    # While blocker is running, submit NORMAL then HIGH
    await asyncio.sleep(0.01)
    normal_task = asyncio.create_task(queue_service.submit(normal_job, priority=GroqPriority.NORMAL, caller="NormalJob"))
    await asyncio.sleep(0.01)
    high_task = asyncio.create_task(queue_service.submit(high_job, priority=GroqPriority.HIGH, caller="HighJob"))

    await asyncio.gather(blocker_task, normal_task, high_task)

    # HIGH should have executed before NORMAL
    assert order == ["high", "normal"]


@pytest.mark.asyncio
async def test_queue_execution_retry_mechanism(queue_service):
    """Verify that jobs retry up to 3 times on GroqRateLimitError (429) or transient errors, then succeed."""
    attempts = 0

    async def failing_then_succeeding_job():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise GroqRateLimitError("Simulated 429 Too Many Requests", retry_after=0.02)
        return "finally_success"

    result = await queue_service.submit(
        failing_then_succeeding_job,
        priority=GroqPriority.HIGH,
        max_retries=3,
        caller="TestRetry",
    )

    assert result == "finally_success"
    assert attempts == 3
    assert queue_service.total_retries == 2


@pytest.mark.asyncio
async def test_queue_execution_fails_after_retries_exhausted(queue_service):
    """Verify that if all 3 retries fail, the exception is propagated to the caller."""
    attempts = 0

    async def always_failing_job():
        nonlocal attempts
        attempts += 1
        raise GroqRateLimitError("Permanent 429", retry_after=0.01)

    with pytest.raises(GroqRateLimitError):
        await queue_service.submit(
            always_failing_job,
            priority=GroqPriority.NORMAL,
            max_retries=3,
            caller="TestFail",
        )

    # Initial attempt + 3 retries = 4 attempts total
    assert attempts == 4
    assert queue_service.total_failed >= 1


@pytest.mark.asyncio
async def test_queue_enqueue_retry_on_failure():
    """Verify 3-attempt enqueue retry mechanism when queue is temporarily full."""
    service = GroqQueueService(
        max_size=1,  # queue capacity 1
        inter_request_delay=0.01,
        default_enqueue_retries=3,
    )
    await service.start_worker()

    try:
        # Fill queue and pause worker
        async def slow_job():
            await asyncio.sleep(0.15)
            return "ok"

        # Item 1 starts running
        t1 = asyncio.create_task(service.submit(slow_job, caller="Job1"))
        await asyncio.sleep(0.01)
        # Item 2 fills the 1-capacity queue
        t2 = asyncio.create_task(service.submit(slow_job, caller="Job2"))
        await asyncio.sleep(0.01)

        # Item 3 will experience contention and retry enqueuing
        t3 = asyncio.create_task(service.submit(slow_job, enqueue_retries=3, caller="Job3"))

        r1, r2, r3 = await asyncio.gather(t1, t2, t3)
        assert r1 == "ok"
        assert r2 == "ok"
        assert r3 == "ok"
    finally:
        await service.stop_worker()
