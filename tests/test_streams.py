import asyncio

from whispercatch_sentinel.streams import StreamBus


def test_publish_delivers_to_subscriber() -> None:
    async def scenario() -> tuple[int, dict]:
        bus = StreamBus()
        async with bus.subscribe("ch") as queue:
            delivered = await bus.publish("ch", {"v": 1})
            payload = await asyncio.wait_for(queue.get(), timeout=1.0)
            return delivered, payload

    delivered, payload = asyncio.run(scenario())
    assert delivered == 1
    assert payload == {"v": 1}


def test_publish_without_subscribers_returns_zero() -> None:
    async def scenario() -> int:
        bus = StreamBus()
        return await bus.publish("none", {"v": 2})

    assert asyncio.run(scenario()) == 0


def test_subscriber_released_after_context_exit() -> None:
    async def scenario() -> int:
        bus = StreamBus()
        async with bus.subscribe("ch"):
            pass
        return bus.subscriber_count("ch")

    assert asyncio.run(scenario()) == 0


def test_full_queue_drops_payload() -> None:
    async def scenario() -> int:
        bus = StreamBus(max_queue=1)
        async with bus.subscribe("ch") as queue:
            await bus.publish("ch", "a")  # fills the single slot
            dropped_delivered = await bus.publish("ch", "b")
            # First message present; second dropped.
            first = await queue.get()
            assert first == "a"
            assert queue.empty()
            return dropped_delivered

    # The second publish "fails" to deliver because the queue is full,
    # so delivered count should be 0 for the second message.
    assert asyncio.run(scenario()) == 0
