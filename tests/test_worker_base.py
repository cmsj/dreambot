# pylint: skip-file
import pytest
from dreambot.shared.worker import DreambotWorkerBase, DreambotWorkerEndType
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_worker_base_shutdown():
    backend = DreambotWorkerBase(
        name="test_name",
        end=DreambotWorkerEndType.BACKEND,
        options={"nats_queue_name": "foo"},
        callback_send_workload=MagicMock(),
    )
    with pytest.raises(NotImplementedError):
        await backend.shutdown()
