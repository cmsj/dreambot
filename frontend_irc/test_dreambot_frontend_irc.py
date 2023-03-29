import asyncio
import pytest
import dreambot_frontend_irc
from unittest.mock import call

# Various support functions and fixtures
@pytest.fixture
def create_mock_coro(mocker, monkeypatch):
    def _create_mock_patch_coro(to_patch=None):
        mock = mocker.Mock()

        async def _coro(*args, **kwargs):
            return mock(*args, **kwargs)

        if to_patch:  # <-- may not need/want to patch anything
            monkeypatch.setattr(to_patch, _coro)
        return mock, _coro

    return _create_mock_patch_coro

# @pytest.fixture
# def mock_sleep(create_mock_coro):
#     # won't need the returned coroutine here
#     mock, _ = create_mock_coro(to_patch="mayhem.asyncio.sleep")
#     return mock

# Tests
def test_queue_name():
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123"}, {"output_dir": "/tmp"}, None)
    assert irc.queue_name() == "irc.abc123"

def test_parse_line():
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123"}, {"output_dir": "/tmp"}, None)
    result = irc.parse_line("PRIVMSG #channel :hello world")
    assert result.prefix == None
    assert result.command == "PRIVMSG"
    assert result.params == ["#channel", "hello world"]

    result = irc.parse_line(":nick!user@host PRIVMSG #channel :hello world")
    assert result.prefix.nick == "nick"
    assert result.prefix.ident == "user"
    assert result.prefix.host == "host"
    assert result.command == "PRIVMSG"
    assert result.params == ["#channel", "hello world"]

    result = irc.parse_line(":irc.example.com 001 nick :Welcome to the Internet Relay Network nick")
    assert result.prefix.nick == "irc.example.com"
    assert result.prefix.ident == None
    assert result.prefix.host == None

    result = irc.parse_line(":SomeUser`^!some@1.2.3.4 PRIVMSG #channel :Some message")
    assert result.prefix.nick == "SomeUser`^"
    assert result.prefix.ident == "some"
    assert result.prefix.host == "1.2.3.4"
    assert result.command == "PRIVMSG"
    assert result.params == ["#channel", "Some message"]

    with pytest.raises(ValueError):
        result = irc.parse_line(":::::::::")

    with pytest.raises(ValueError):
        result = irc.parse_line("")

def test_irc_join(mocker):
    mocker.patch("dreambot_frontend_irc.DreambotFrontendIRC.send_cmd")
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123"}, {"output_dir": "/tmp"}, None)
    irc.irc_join(["#channel1", "#channel2"])
    irc.send_cmd.assert_has_calls([call('JOIN', '#channel1'), call('JOIN', '#channel2')])

def test_irc_renick(mocker):
    mocker.patch("dreambot_frontend_irc.DreambotFrontendIRC.send_line")
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc"}, {"output_dir": "/tmp"}, None)
    irc.irc_renick()
    assert irc.server["nickname"] == "abc_"
    assert irc.send_line.call_count == 1

    irc.irc_renick()
    assert irc.server["nickname"] == "abc__"
    assert irc.send_line.call_count == 2

def test_irc_privmsg(mocker):
    async def cb_publish(trigger, packet):
        pass

    mocker.patch("dreambot_frontend_irc.DreambotFrontendIRC.send_cmd")
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc"}, {"output_dir": "/tmp", "triggers": []}, None)
    irc.cb_publish = cb_publish

    message = irc.parse_line(":SomeUser`^!some@1.2.3.4 PRIVMSG #channel :Some message")
    asyncio.run(irc.irc_privmsg(message))
    assert irc.send_cmd.call_count == 0

    irc.options["triggers"].append("!test")
    message = irc.parse_line(":OtherUser^!other@2.3.4.5 PRIVMSG #place :!test")
    asyncio.run(irc.irc_privmsg(message))
    assert irc.send_cmd.call_count == 1