import asyncio
import unittest
from unittest.mock import AsyncMock

from ai.core.events import (
    EventType,
    InMemoryEventEmitter,
    RunContext,
)


class Phase1RegressionTests(unittest.TestCase):
    """Regression tests for Phase 1 refactoring."""

    def setUp(self):
        self.emitter = InMemoryEventEmitter()
        self.thread_id = "test-thread-123"
        self.run_id = "test-run-456"

    def test_streaming_stable_message_ids(self):
        """Test that message IDs are stable across deltas for a single message."""

        async def run_test():
            run_ctx = RunContext(
                emitter=self.emitter,
                thread_id=self.thread_id,
                run_id=self.run_id,
                agent_name="test-agent",
            )

            # Capture events
            events = []

            async def capture(event):  # noqa: RUF029
                events.append(event)

            # Mock emitter.emit to capture events
            self.emitter.emit = AsyncMock(side_effect=capture)

            # Simulate streaming a message
            msg_id = await run_ctx.emit_text_start()
            await run_ctx.emit_text_delta("Hello", message_id=msg_id)
            await run_ctx.emit_text_delta(" World", message_id=msg_id)
            await run_ctx.emit_text_end(message_id=msg_id)

            # Filter text events
            text_events = [
                e
                for e in events
                if e.event_type
                in [
                    EventType.TEXT_MESSAGE_START,
                    EventType.TEXT_MESSAGE_CONTENT,
                    EventType.TEXT_MESSAGE_END,
                ]
            ]

            # Verify all have the same messageId
            self.assertGreaterEqual(len(text_events), 3)
            first_id = text_events[0].data["messageId"]
            for event in text_events:
                self.assertEqual(event.data["messageId"], first_id)

        asyncio.run(run_test())

    def test_streaming_no_crosstalk(self):
        """Test that events from different threads don't mix."""

        async def run_test():
            emitter = InMemoryEventEmitter()

            # Create two contexts with different threads
            ctx1 = RunContext(emitter, thread_id="thread-1", run_id="run-1")
            ctx2 = RunContext(emitter, thread_id="thread-2", run_id="run-2")

            events = []

            async def capture(event):  # noqa: RUF029
                events.append(event)

            emitter.emit = AsyncMock(side_effect=capture)

            # Emit events from both
            await ctx1.emit_thinking("Thread 1 thinking")
            await ctx2.emit_thinking("Thread 2 thinking")

            # Verify events have correct thread_ids
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0].thread_id, "thread-1")
            self.assertEqual(events[1].thread_id, "thread-2")

        asyncio.run(run_test())

    def test_fail_loud_behavior(self):
        """Test that failures yield structured errors, not generic greetings."""

        async def run_test():
            events = []

            async def capture(event):  # noqa: RUF029
                events.append(event)

            emitter = InMemoryEventEmitter()
            emitter.emit = AsyncMock(side_effect=capture)

            try:
                async with RunContext(
                    emitter=emitter,
                    thread_id=self.thread_id,
                    run_id=self.run_id,
                    agent_name="test-agent",
                ):
                    raise Exception("Forced failure")
            except Exception:
                pass

            error_events = [
                e for e in events if e.event_type in (EventType.ERROR, EventType.RUN_ERROR)
            ]
            self.assertGreater(len(error_events), 0)
            error_msg = error_events[0].data.get("error") or error_events[0].data.get("message")
            self.assertIn("Forced failure", error_msg)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
