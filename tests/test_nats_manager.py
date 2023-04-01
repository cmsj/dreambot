import pytest
import asyncio
import nats
import signal
import dreambot.frontend.nats_manager
from unittest.mock import call, AsyncMock, MagicMock

# Helper fixtures
@pytest.fixture
def create_mock_coro(mocker, monkeypatch):
    def _create_mock_patch_coro(to_patch=None, return_value=None):
        mock = mocker.Mock()

        async def _coro(*args, **kwargs):
            return mock(*args, **kwargs)

        if to_patch:  # <-- may not need/want to patch anything
            monkeypatch.setattr(to_patch, _coro)
        return mock, _coro

    return _create_mock_patch_coro

@pytest.fixture
def mock_sleep(create_mock_coro):
    # won't need the returned coroutine here
    mock, _ = create_mock_coro(to_patch="asyncio.sleep")
    return mock

# Tests

@pytest.mark.asyncio
async def test_dreambot_nats_boot_connect_failed(mocker):
    nm = dreambot.frontend.nats_manager.FrontendNatsManager(nats_uri="nats://test:1234")

    mock_nats_connect = mocker.patch("nats.connect", return_value=AsyncMock(), side_effect=nats.errors.NoServersError)

    await nm.boot([])
    assert mock_nats_connect.call_count == 1

@pytest.mark.asyncio
async def test_dreambot_nats_shutdown(mocker, mock_sleep):
    all_tasks = [MagicMock(), MagicMock(), MagicMock()]
    mock_all_tasks = mocker.patch("asyncio.all_tasks", return_value=all_tasks)

    nm = dreambot.frontend.nats_manager.FrontendNatsManager(nats_uri="nats://test:1234")
    nm.nats_tasks = [MagicMock(), MagicMock()]
    nm.nc = AsyncMock()

    await nm.shutdown()
    assert mock_sleep.call_count == 1
    for task in nm.nats_tasks:
        assert task.cancel.call_count == 1
    assert nm.nc.close.call_count == 1
    for task in all_tasks:
        assert task.cancel.call_count == 1

@pytest.mark.asyncio
async def test_dreambot_nats_publish(mocker):
    nm = dreambot.frontend.nats_manager.FrontendNatsManager(nats_uri="nats://test:1234")
    nm.nc = AsyncMock()

    await nm.nats_publish("test", "test")
    assert nm.nc.publish.call_count == 1
    assert nm.nc.publish.has_calls([call("test", "test".encode())])

@pytest.mark.asyncio
async def test_dreambot_nats_main_shutdown(mocker):
    nm = dreambot.frontend.nats_manager.FrontendNatsManager(nats_uri="nats://test:1234")
    nm.shutdown = AsyncMock()
    objects = [nm]
    loop = AsyncMock()

    await dreambot.frontend.nats_manager.shutdown(loop, None, objects=objects)
    assert nm.shutdown.call_count == 1
    assert loop.stop.call_count == 1

    nm.shutdown.reset_mock()
    loop.stop.reset_mock()

    await dreambot.frontend.nats_manager.shutdown(loop, signal.SIGINT, objects=objects)
    assert nm.shutdown.call_count == 1
    assert loop.stop.call_count == 1

# FIXME: This seems like we would need a whole bunch more mocking of NATS, to be able to fully test Dreambot
# @pytest.mark.asyncio
# async def test_dreambot_nats_boot_connect_success(mocker, mock_sleep, mock_nats_jetstream, mock_nats_handle_nats_messages):
#     dreambot = frontend.irc.Dreambot({"nats_uri": "nats://test:1234",
#                                                "name":"nats-test",
#                                                "irc": {}
#                                                })

#     mock_nats_connect = mocker.patch("nats.connect", return_value=AsyncMock())
#     # mock_jetstream = mocker.patch("frontend.irc.Dreambot.nats.jetstream", return_value=AsyncMock())
#     # mock_handle_nats_message = mocker.patch("frontend.irc.Dreambot.handle_nats_messages", return_value=AsyncMock())

#     await dreambot.boot(max_reconnects=1)
#     assert mock_nats_connect.call_count == 1
#     assert mock_nats_jetstream.call_count == 1
#     assert mock_nats_handle_nats_messages.call_count == 1