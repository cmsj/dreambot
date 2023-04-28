import pytest
import asyncio
import nats
import signal
import time
import dreambot.shared.nats
from unittest.mock import call, patch, AsyncMock, MagicMock
from nats.js.errors import BadRequestError


class TestWorker:
    def queue_name(self):
        return "testqueue"

    def callback_receive_workload(self, message):
        pass


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


@pytest.fixture
def mock_nats_next_msg(create_mock_coro):
    mock, _ = create_mock_coro(to_patch="nats.aio.subscription.Subscription.next_msg")
    return mock


# Tests


@pytest.mark.asyncio
async def test_boot_connect_failed(mocker):
    nm = dreambot.shared.nats.NatsManager(nats_uri="nats://test:1234", name="test_boot_connect_failed")

    mock_nats_connect = mocker.patch("nats.connect", return_value=AsyncMock(), side_effect=nats.errors.NoServersError)

    await nm.boot([])
    assert mock_nats_connect.call_count == 1


@pytest.mark.asyncio
async def test_nats_shutdown(mocker, mock_sleep):
    all_tasks = [MagicMock(), MagicMock(), MagicMock()]
    mock_all_tasks = mocker.patch("asyncio.all_tasks", return_value=all_tasks)

    nm = dreambot.shared.nats.NatsManager(nats_uri="nats://test:1234", name="test_nats_shutdown")
    nm.nats_tasks = [MagicMock(), MagicMock()]
    nm.nc = AsyncMock()

    await nm.shutdown()
    assert mock_sleep.call_count == 1
    for task in nm.nats_tasks:
        assert task.cancel.call_count == 1
    assert nm.nc.close.call_count == 1


@pytest.mark.asyncio
async def test_nats_publish(mocker):
    nm = dreambot.shared.nats.NatsManager(nats_uri="nats://test:1234", name="test_nats_publish")
    nm.nc = AsyncMock()
    nm.js = AsyncMock()

    json_txt = '{"test": "test"}'
    json_bytes = json_txt.encode()

    await nm.publish("test", json_bytes)
    assert nm.js.publish.call_count == 1
    assert nm.js.publish.has_calls([call("test", json_bytes)])


# FIXME: No idea why this one is broken
# @pytest.mark.asyncio
# async def test_main_shutdown(mocker, mock_nats_next_msg):
#     nm = dreambot.shared.nats.NatsManager(nats_uri="nats://test:1234")
#     nm.shutdown = AsyncMock()
#     objects = [nm]
#     loop = AsyncMock()

#     await dreambot.shared.nats.shutdown(loop, None, objects=objects)
#     assert nm.shutdown.call_count == 1
#     assert loop.stop.call_count == 1

#     nm.shutdown.reset_mock()
#     loop.stop.reset_mock()

#     await dreambot.shared.nats.shutdown(loop, signal.SIGINT, objects=objects)
#     assert nm.shutdown.call_count == 1
#     assert loop.stop.call_count == 1


# FIXME: This test should work, but is currently broken because of our reply-image censoring
# @pytest.mark.asyncio
# async def test_nats_subscribe(mocker, mock_sleep):
#     nm = dreambot.shared.nats.NatsManager(nats_uri="nats://test:1234")
#     nm.nc = AsyncMock()
#     nm.js = AsyncMock()
#     callback_count = 5

#     def next_sub_side_effect():
#         return AsyncMock()

#     def callback(queue_name, msg):
#         nonlocal callback_count
#         callback_count -= 1
#         if callback_count <= 0:
#             nm.shutting_down = True
#         return True

#     cb = MagicMock()
#     cb.callback = callback

#     sub_obj = AsyncMock()
#     sub_obj.next_msg = AsyncMock()
#     sub_obj.next_msg.side_effect = next_sub_side_effect  # FIXME: I don't understand why this is necessary
#     nm.js.subscribe = sub_obj

#     tw = TestWorker()
#     tw.callback_receive_workload = callback
#     await nm.subscribe(tw)
#     assert nm.shutting_down == True
#     assert sub_obj.call_count == 1
#     assert callback_count == 0


@pytest.mark.asyncio
async def test_nats_subscribe_badrequest(mocker, mock_sleep):
    nm = dreambot.shared.nats.NatsManager(nats_uri="nats://test:1234", name="test_nats_subscribe_badrequest")
    nm.js = MagicMock()
    nm.logger.warning = MagicMock()
    loop_count = 5

    def add_stream_side_effect(**kwargs):
        # Inhibit the retry loop from running forever, then raise the exception we want to ensure is caught
        nonlocal loop_count
        loop_count -= 1
        if loop_count <= 0:
            nonlocal nm
            nm.shutting_down = True
        raise BadRequestError

    nm.js.add_stream = AsyncMock(side_effect=add_stream_side_effect)

    await nm.subscribe(TestWorker())
    assert loop_count == 0
    assert nm.logger.warning.call_count == 5
    nm.logger.warning.assert_has_calls(
        [
            call(
                "NATS consumer 'testqueue' already exists, likely a previous instance of us hasn't timed out yet. Sleeping..."
            )
        ]
    )


@pytest.mark.asyncio
async def test_nats_subscribe_other_exception(mocker, mock_sleep):
    nm = dreambot.shared.nats.NatsManager(nats_uri="nats://test:1234", name="test_nats_subscribe_other_exception")
    nm.js = MagicMock()
    nm.logger.error = MagicMock()
    loop_count = 5

    def add_stream_side_effect(**kwargs):
        # Inhibit the retry loop from running forever, then raise the exception we want to ensure is caught
        nonlocal loop_count
        loop_count -= 1
        if loop_count <= 0:
            nonlocal nm
            nm.shutting_down = True
        raise ValueError("Some other exception")

    nm.js.add_stream = AsyncMock(side_effect=add_stream_side_effect)

    await nm.subscribe(MagicMock())
    assert loop_count == 0
    assert nm.logger.error.call_count == 5
    nm.logger.error.assert_has_calls([call("nats_subscribe exception: Some other exception")])
