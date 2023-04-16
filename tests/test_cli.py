import pytest
import dreambot.shared.cli
from unittest.mock import patch, AsyncMock, MagicMock


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
