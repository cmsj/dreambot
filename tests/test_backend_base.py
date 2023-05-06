# pylint: skip-file
import pytest
import dreambot.backend.base
from unittest.mock import MagicMock


def test_backend_base_queue_name():
    backend = dreambot.backend.base.DreambotBackendBase("test", {"nats_queue_name": "foo"}, MagicMock())
    assert backend.queue_name() == "foo"


@pytest.mark.asyncio
async def test_backend_base_shutdown():
    backend = dreambot.backend.base.DreambotBackendBase("test", {"nats_queue_name": "foo"}, MagicMock())
    with pytest.raises(NotImplementedError):
        await backend.shutdown()
