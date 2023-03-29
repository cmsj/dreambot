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


@pytest.fixture
def mock_send_cmd(mocker):
    yield mocker.patch("dreambot_frontend_irc.DreambotFrontendIRC.send_cmd")


@pytest.fixture
def mock_send_line(mocker):
    yield mocker.patch("dreambot_frontend_irc.DreambotFrontendIRC.send_line")


@pytest.fixture
def mock_irc_privmsg(mocker):
    yield mocker.patch("dreambot_frontend_irc.DreambotFrontendIRC.irc_privmsg")


@pytest.fixture
def mock_builtins_open(mocker):
    yield mocker.patch("builtins.open", mocker.mock_open())

# Tests

def test_queue_name():
    irc = dreambot_frontend_irc.DreambotFrontendIRC(
        {"host": "abc123"}, {"output_dir": "/tmp"}, None)
    assert irc.queue_name() == "irc.abc123"


def test_parse_line():
    irc = dreambot_frontend_irc.DreambotFrontendIRC(
        {"host": "abc123"}, {"output_dir": "/tmp"}, None)
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

    result = irc.parse_line(
        ":irc.example.com 001 nick :Welcome to the Internet Relay Network nick")
    assert result.prefix.nick == "irc.example.com"
    assert result.prefix.ident == None
    assert result.prefix.host == None

    result = irc.parse_line(
        ":SomeUser`^!some@1.2.3.4 PRIVMSG #channel :Some message")
    assert result.prefix.nick == "SomeUser`^"
    assert result.prefix.ident == "some"
    assert result.prefix.host == "1.2.3.4"
    assert result.command == "PRIVMSG"
    assert result.params == ["#channel", "Some message"]

    result = irc.parse_line(
        ":OtherUser@2.3.4.5 PRIVMSG #channel :Other message")
    assert result.prefix.nick == "OtherUser"
    assert result.prefix.ident == None
    assert result.prefix.host == "2.3.4.5"
    assert result.command == "PRIVMSG"
    assert result.params == ["#channel", "Other message"]

    with pytest.raises(ValueError):
        result = irc.parse_line(":::::::::")

    with pytest.raises(ValueError):
        result = irc.parse_line("")

    with pytest.raises(AttributeError):
        result = irc.parse_line(None)


def test_irc_join(mock_send_cmd):
    irc = dreambot_frontend_irc.DreambotFrontendIRC(
        {"host": "abc123"}, {"output_dir": "/tmp"}, None)
    irc.irc_join(["#channel1", "#channel2"])
    irc.send_cmd.assert_has_calls(
        [call('JOIN', '#channel1'), call('JOIN', '#channel2')])


def test_irc_renick(mock_send_line):
    irc = dreambot_frontend_irc.DreambotFrontendIRC(
        {"host": "abc123", "nickname": "abc"}, {"output_dir": "/tmp"}, None)

    irc.irc_renick()
    assert irc.server["nickname"] == "abc_"
    assert irc.send_line.call_count == 1

    # Repeat renick to check we are incrementing the nick correctly.
    irc.irc_renick()
    assert irc.server["nickname"] == "abc__"
    assert irc.send_line.call_count == 2


def test_irc_privmsg(mock_send_cmd):
    async def cb_publish(trigger, packet):
        pass

    irc = dreambot_frontend_irc.DreambotFrontendIRC(
        {"host": "abc123", "nickname": "abc"}, {"output_dir": "/tmp", "triggers": []}, None)
    irc.cb_publish = cb_publish

    message = irc.parse_line(
        ":SomeUser`^!some@1.2.3.4 PRIVMSG #channel :Some message")
    asyncio.run(irc.irc_privmsg(message))
    assert irc.send_cmd.call_count == 0

    irc.options["triggers"].append("!test")
    message = irc.parse_line(":OtherUser^!other@2.3.4.5 PRIVMSG #place :!test")
    asyncio.run(irc.irc_privmsg(message))
    assert irc.send_cmd.call_count == 1


def test_handle_response_image(mock_builtins_open, mock_send_cmd):
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc"}, {
                                                    "output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"}, None)

    irc.handle_response({"image": "UE5HIHRlc3QK", "prompt": "test prompt",
                        "server": "test.server.com", "channel": "#testchannel", "user": "testuser"})

    assert irc.send_cmd.call_count == 1
    assert open.call_count == 1
    open.assert_has_calls([call('/tmp/test_prompt.png', 'wb')])
    handle = open()
    handle.write.assert_has_calls([call(b'PNG test\n')])
    irc.send_cmd.assert_has_calls(
        [call('PRIVMSG', '#testchannel', 'testuser: I dreamed this: http://testuri//test_prompt.png')])


def test_handle_response_error(mock_send_cmd):
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc"}, {
                                                    "output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"}, None)

    irc.handle_response({"error": "test error", "server": "test.server.com",
                        "channel": "#testchannel", "user": "testuser"})

    assert irc.send_cmd.call_count == 1
    irc.send_cmd.assert_has_calls(
        [call('PRIVMSG', '#testchannel', 'testuser: Dream sequence collapsed: test error')])


def test_handle_response_usage(mock_send_cmd):
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc"}, {
                                                    "output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"}, None)

    irc.handle_response({"usage": "test usage", "server": "test.server.com",
                        "channel": "#testchannel", "user": "testuser"})

    assert irc.send_cmd.call_count == 1
    irc.send_cmd.assert_has_calls(
        [call('PRIVMSG', '#testchannel', 'testuser: test usage')])


def test_handle_response_unknown(mock_send_cmd):
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc"}, {
                                                    "output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"}, None)

    irc.handle_response({"server": "test.server.com",
                        "channel": "#testchannel", "user": "testuser"})

    assert irc.send_cmd.call_count == 1
    irc.send_cmd.assert_has_calls(
        [call('PRIVMSG', '#testchannel', 'testuser: Dream sequence collapsed, unknown reason.')])


def test_handle_line_ping(mock_send_cmd):
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc"}, {
                                                    "output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"}, None)

    asyncio.run(irc.handle_line(b"PING :abc123"))

    assert irc.send_cmd.call_count == 1
    irc.send_cmd.assert_has_calls([call('PONG', 'abc123')])


def test_handle_line_001(mock_send_cmd):
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc", "channels": [
                                                    "#test1", "#test2"]}, {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"}, None)

    asyncio.run(irc.handle_line(b"001"))

    assert irc.send_cmd.call_count == 2
    irc.send_cmd.assert_has_calls(
        [call('JOIN', '#test1'), call('JOIN', '#test2')])


def test_handle_line_443(mock_send_cmd, mock_send_line):
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc", "channels": [
                                                    "#test1", "#test2"]}, {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"}, None)

    asyncio.run(irc.handle_line(b"443"))

    assert irc.send_cmd.call_count == 0
    assert irc.send_line.call_count == 1
    irc.send_line.assert_has_calls([call('NICK abc_')])
    assert irc.server["nickname"] == "abc_"


def test_handle_line_privmsg(mock_irc_privmsg):
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc", "channels": [
                                                    "#test1", "#test2"]}, {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"}, None)

    asyncio.run(irc.handle_line(b"PRIVMSG #testchannel :!test"))

    assert irc.irc_privmsg.call_count == 1
    irc.irc_privmsg.assert_has_calls([call(irc.Message(
        prefix=None, command='PRIVMSG', params=['#testchannel', '!test']))])


def test_handle_line_unknown(mock_irc_privmsg, mock_send_cmd, mock_send_line):
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc", "channels": [
                                                    "#test1", "#test2"]}, {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"}, None)

    asyncio.run(irc.handle_line(b"401"))

    assert irc.irc_privmsg.call_count == 0
    assert irc.send_cmd.call_count == 0
    assert irc.send_line.call_count == 0

def test_handle_line_nonunicode(mock_irc_privmsg, mock_send_cmd, mock_send_line):
    irc = dreambot_frontend_irc.DreambotFrontendIRC({"host": "abc123", "nickname": "abc", "channels": [
                                                    "#test1", "#test2"]}, {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"}, None)

    asyncio.run(irc.handle_line(b'\x9c'))

    assert irc.irc_privmsg.call_count == 0
    assert irc.send_cmd.call_count == 0
    assert irc.send_line.call_count == 0
