# pylint: skip-file
import pytest
from dreambot.shared.worker import DreambotWorkerBase
from unittest.mock import MagicMock


def test_worker_base_queue_name():
    backend = DreambotWorkerBase(
        name="test_name",
        queue_name="test_queue",
        end="backend",
        options={"nats_queue_name": "foo"},
        callback_send_workload=MagicMock(),
    )
    assert backend.queue_name() == "test_queue"


@pytest.mark.asyncio
async def test_worker_base_shutdown():
    backend = DreambotWorkerBase(
        name="test_name",
        queue_name="test_queue",
        end="frontend",
        options={"nats_queue_name": "foo"},
        callback_send_workload=MagicMock(),
    )
    with pytest.raises(NotImplementedError):
        await backend.shutdown()
