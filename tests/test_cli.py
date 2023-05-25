# pylint: skip-file
import pytest
import signal
import dreambot.shared.cli
from unittest.mock import call, patch, AsyncMock, MagicMock


def test_cli_parseargs():
    cli = dreambot.shared.cli.DreambotCLI("test_cli_parseargs")
    with patch("sys.argv", ["main", "-c", "somefile.json"]):
        cli.parse_args()
    assert cli.args is not None


def test_boot_missing_nats_uri():
    m = MagicMock(side_effect=[{"foo": "bar"}])
    p1 = patch("builtins.open", MagicMock())
    p2 = patch("json.load", m)

    cli = dreambot.shared.cli.DreambotCLI("test_boot_missing_nats_uri")
    with patch("sys.argv", ["main", "-c", "somefile.json"]):
        with p1 as p_open:
            with p2 as p_json_load:
                with pytest.raises(ValueError):
                    cli.boot()


def test_boot():
    m = MagicMock(side_effect=[{"nats_uri": "bar"}])
    p1 = patch("builtins.open", MagicMock())
    p2 = patch("json.load", m)
    patch("dreambot.shared.nats.NatsManager", MagicMock())

    cli = dreambot.shared.cli.DreambotCLI("test_boot")
    with patch("sys.argv", ["main", "-c", "somefile.json"]):
        with p1 as p_open:
            with p2 as p_json_load:
                cli.boot()
    assert cli.options == {"nats_uri": "bar"}
    assert cli.nats is not None


def test_run(mocker):
    loop = MagicMock()
    mock_get_event_loop = mocker.patch("asyncio.get_event_loop", MagicMock(return_value=loop))
    cli = dreambot.shared.cli.DreambotCLI("test_run")
    cli.nats = AsyncMock()
    cli.workers = [AsyncMock(), AsyncMock()]
    cli.run()

    assert mock_get_event_loop.call_count == 1
    assert loop.add_signal_handler.call_count == 3
    assert loop.create_task.call_count == 3
    assert loop.run_forever.call_count == 1
    assert loop.close.call_count == 1
    assert cli.nats.boot.call_count == 1
    for worker in cli.workers:
        assert worker.boot.call_count == 1


@pytest.mark.asyncio
async def test_shutdown(mocker):
    loop = MagicMock()
    mock_get_event_loop = mocker.patch("asyncio.get_event_loop", MagicMock(return_value=loop))
    cli = dreambot.shared.cli.DreambotCLI("test_run")
    cli.nats = AsyncMock()
    cli.workers = [AsyncMock(), AsyncMock()]
    await cli.shutdown(signal.Signals.SIGINT.value)

    assert cli.nats.shutdown.call_count == 1
    for worker in cli.workers:
        assert worker.shutdown.call_count == 1


@pytest.mark.asyncio
async def test_callback_send_workload(mocker):
    cli = dreambot.shared.cli.DreambotCLI("test_callback_send_workload")
    cli.nats = AsyncMock()

    data = {"to": "testqueue", "foo": "bar"}
    await cli.callback_send_workload(data)

    assert cli.nats.publish.call_count == 1
    assert cli.nats.publish.has_calls([call(data)])

    data = {"reply-image": "to be removed"}
    await cli.callback_send_workload(data)

    assert cli.nats.publish.call_count == 2
    assert cli.nats.publish.has_calls([call({"reply-image": "** IMAGE **"})])
