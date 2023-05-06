# pylint: skip-file
import pytest
import asyncio
import json
import logging
import dreambot.frontend.irc
from unittest.mock import call, AsyncMock, MagicMock

import sys

# Various support functions and fixtures


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
def mock_stream_reader_ateof(mocker):
    yield mocker.patch("asyncio.StreamReader.at_eof", return_value=True)


@pytest.fixture
def mock_send_cmd(mocker):
    mock = AsyncMock()
    mocker.patch("dreambot.frontend.irc.FrontendIRC.send_cmd")
    return mock


@pytest.fixture
def mock_send_line(mocker):
    mock = AsyncMock()
    mocker.patch("dreambot.frontend.irc.FrontendIRC.send_line")
    return mock


@pytest.fixture
def mock_irc_privmsg(mocker):
    yield mocker.patch("dreambot.frontend.irc.FrontendIRC.irc_received_privmsg")


@pytest.fixture
def mock_handle_line(mocker):
    yield mocker.patch("dreambot.frontend.irc.FrontendIRC.handle_line")


@pytest.fixture
def mock_asyncio_open_connection_read_eof(mocker):
    def at_eof():
        return True

    open_connection = mocker.patch("asyncio.open_connection")
    reader = AsyncMock()
    reader.at_eof.side_effect = at_eof
    writer = AsyncMock()
    writer.at_eof = at_eof
    open_connection.return_value = (reader, writer)
    yield open_connection


# FIXME: Do a fixture for the FrontendIRC instantiation so we don't have to repeat it in every test.


@pytest.fixture
def mock_nats_jetstream(mocker):
    yield mocker.patch("dreambot.frontend.irc.Dreambot.nats")


@pytest.fixture
def mock_nats_handle_nats_messages(mocker):
    yield mocker.patch("dreambot.frontend.irc.Dreambot.handle_nats_messages")


@pytest.fixture
def mock_builtins_open(mocker):
    yield mocker.patch("builtins.open", mocker.mock_open())


# FrontendIRC Tests


def test_queue_name():
    irc = dreambot.frontend.irc.FrontendIRC({"host": "abc123"}, {"output_dir": "/tmp"}, None)
    assert irc.queue_name() == "irc_abc123"


def test_parse_line():
    irc = dreambot.frontend.irc.FrontendIRC({"host": "abc123"}, {"output_dir": "/tmp"}, None)
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
    assert result.prefix.ident == ""
    assert result.prefix.host == ""

    result = irc.parse_line(":SomeUser`^!some@1.2.3.4 PRIVMSG #channel :Some message")
    assert result.prefix.nick == "SomeUser`^"
    assert result.prefix.ident == "some"
    assert result.prefix.host == "1.2.3.4"
    assert result.command == "PRIVMSG"
    assert result.params == ["#channel", "Some message"]

    result = irc.parse_line(":OtherUser@2.3.4.5 PRIVMSG #channel :Other message")
    assert result.prefix.nick == "OtherUser"
    assert result.prefix.ident == ""
    assert result.prefix.host == "2.3.4.5"
    assert result.command == "PRIVMSG"
    assert result.params == ["#channel", "Other message"]

    with pytest.raises(ValueError):
        result = irc.parse_line(":::::::::")

    with pytest.raises(ValueError):
        result = irc.parse_line("")

    with pytest.raises(AttributeError):
        result = irc.parse_line(None)


@pytest.mark.asyncio
async def test_irc_join(mock_send_cmd):
    irc = dreambot.frontend.irc.FrontendIRC({"host": "abc123"}, {"output_dir": "/tmp"}, None)
    await irc.irc_join(["#channel1", "#channel2"])
    irc.send_cmd.assert_has_calls([call("JOIN", "#channel1"), call("JOIN", "#channel2")])


@pytest.mark.asyncio
async def test_irc_renick(mock_send_line):
    irc = dreambot.frontend.irc.FrontendIRC({"host": "abc123", "nickname": "abc"}, {"output_dir": "/tmp"}, None)

    await irc.irc_renick()
    assert irc.server["nickname"] == "abc_"
    assert irc.send_line.call_count == 1

    # Repeat renick to check we are incrementing the nick correctly.
    await irc.irc_renick()
    assert irc.server["nickname"] == "abc__"
    assert irc.send_line.call_count == 2


@pytest.mark.asyncio
async def test_irc_privmsg(mock_send_cmd):
    cb_publish_called = False

    async def cb_publish(trigger, packet):
        nonlocal cb_publish_called
        cb_publish_called = True
        pass

    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc"},
        {"output_dir": "/tmp", "triggers": ["!test"]},
        None,
    )
    irc.callback_send_workload = cb_publish

    message = irc.parse_line(":SomeUser`^!some@1.2.3.4 PRIVMSG #channel :Some message")
    await irc.irc_received_privmsg(message)
    assert irc.send_cmd.call_count == 0

    irc.options["triggers"].append("!test")
    message = irc.parse_line(":OtherUser^!other@2.3.4.5 PRIVMSG #place :!test something")
    await irc.irc_received_privmsg(message)
    assert cb_publish_called == True


@pytest.mark.asyncio
async def test_long_irc_line(mocker):
    mock_open_connection = mocker.patch("asyncio.open_connection", return_value=(AsyncMock(), AsyncMock()))
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc"},
        {"output_dir": "/tmp", "triggers": []},
        None,
    )
    irc.logger.warning = MagicMock()
    irc.writer = AsyncMock()

    await irc.send_line("a" * 512)

    irc.logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_handle_response_image(caplog, mock_builtins_open, mock_send_cmd):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc"},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    await irc.callback_receive_workload(
        None,
        {
            "reply-image": "UE5HIHRlc3QK",
            "prompt": "test prompt",
            "server": "test.server.com",
            "channel": "#testchannel",
            "user": "testuser",
        },
    )

    assert irc.send_cmd.call_count == 1
    assert open.call_count == 1
    open.assert_has_calls([call("/tmp/test_prompt.png", "wb")])
    handle = open()
    handle.write.assert_has_calls([call(b"PNG test\n")])
    irc.send_cmd.assert_has_calls(
        [
            call(
                "PRIVMSG",
                "#testchannel",
                "testuser: I dreamed this: http://testuri//test_prompt.png",
            )
        ]
    )


@pytest.mark.asyncio
async def test_handle_response_text(mock_send_cmd):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc"},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    await irc.callback_receive_workload(
        None,
        {
            "reply-text": "test text",
            "server": "test.server.com",
            "channel": "#testchannel",
            "user": "testuser",
        },
    )

    assert irc.send_cmd.call_count == 1
    irc.send_cmd.assert_has_calls([call("PRIVMSG", "#testchannel", "testuser: test text")])


@pytest.mark.asyncio
async def test_handle_response_error(mock_send_cmd):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc"},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    await irc.callback_receive_workload(
        None,
        {
            "error": "test error",
            "server": "test.server.com",
            "channel": "#testchannel",
            "user": "testuser",
        },
    )

    assert irc.send_cmd.call_count == 1
    irc.send_cmd.assert_has_calls(
        [
            call(
                "PRIVMSG",
                "#testchannel",
                "testuser: Dream sequence collapsed: test error",
            )
        ]
    )


@pytest.mark.asyncio
async def test_handle_response_usage(mock_send_cmd):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc"},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    await irc.callback_receive_workload(
        None,
        {
            "usage": "test usage",
            "server": "test.server.com",
            "channel": "#testchannel",
            "user": "testuser",
        },
    )

    assert irc.send_cmd.call_count == 1
    irc.send_cmd.assert_has_calls([call("PRIVMSG", "#testchannel", "testuser: test usage")])


@pytest.mark.asyncio
async def test_handle_response_unknown(mock_send_cmd):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc"},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    await irc.callback_receive_workload(
        None,
        {"server": "test.server.com", "channel": "#testchannel", "user": "testuser"},
    )

    assert irc.send_cmd.call_count == 1
    irc.send_cmd.assert_has_calls(
        [
            call(
                "PRIVMSG",
                "#testchannel",
                "testuser: Dream sequence collapsed, unknown reason.",
            )
        ]
    )


@pytest.mark.asyncio
async def test_handle_response_silence(mock_send_cmd):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc"},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    await irc.callback_receive_workload(
        None,
        {"reply-none": "test reply", "server": "test.server.com", "channel": "#testchannel", "user": "testuser"},
    )

    assert irc.send_cmd.call_count == 0


@pytest.mark.asyncio
async def test_handle_line_ping(mock_send_cmd):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc"},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    await irc.handle_line(b"PING :abc123")

    assert irc.send_cmd.call_count == 1
    irc.send_cmd.assert_has_calls([call("PONG", "abc123")])


@pytest.mark.asyncio
async def test_handle_line_001(mock_send_cmd):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc", "channels": ["#test1", "#test2"]},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    await irc.handle_line(b"001")

    assert irc.send_cmd.call_count == 2
    irc.send_cmd.assert_has_calls([call("JOIN", "#test1"), call("JOIN", "#test2")])


@pytest.mark.asyncio
async def test_handle_line_443(mock_send_cmd, mock_send_line):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc", "channels": ["#test1", "#test2"]},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    await irc.handle_line(b"443")

    assert irc.send_cmd.call_count == 0
    assert irc.send_line.call_count == 1
    irc.send_line.assert_has_calls([call("NICK abc_")])
    assert irc.server["nickname"] == "abc_"


@pytest.mark.asyncio
async def test_handle_line_privmsg(mock_irc_privmsg):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc", "channels": ["#test1", "#test2"]},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    await irc.handle_line(b"PRIVMSG #testchannel :!test")

    assert irc.irc_received_privmsg.call_count == 1
    irc.irc_received_privmsg.assert_has_calls(
        [call(dreambot.frontend.irc.Message(prefix=None, command="PRIVMSG", params=["#testchannel", "!test"]))]
    )


@pytest.mark.asyncio
async def test_handle_line_privmsg_publish_raises(mock_send_cmd):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc", "channels": ["#test1", "#test2"]},
        {"output_dir": "/tmp", "triggers": ["!test"], "uri_base": "http://testuri/"},
        None,
    )
    irc.callback_send_workload = AsyncMock(side_effect=Exception("test exception"))
    await irc.handle_line(b":testuser!testident@testhost PRIVMSG #testchannel :!test some text")

    assert irc.callback_send_workload.call_count == 1
    irc.callback_send_workload.assert_has_calls(
        [
            call(
                "!test",
                b'{"reply-to": "irc_abc123", "frontend": "irc", "server": "abc123", "channel": "#testchannel", "user": "testuser", "trigger": "!test", "prompt": "some text"}',
            )
        ]
    )


@pytest.mark.asyncio
async def test_handle_line_unknown(mock_irc_privmsg, mock_send_cmd, mock_send_line):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc", "channels": ["#test1", "#test2"]},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    await irc.handle_line(b"401")

    assert irc.irc_received_privmsg.call_count == 0
    assert irc.send_cmd.call_count == 0
    assert irc.send_line.call_count == 0


@pytest.mark.asyncio
async def test_handle_line_nonunicode(mock_irc_privmsg, mock_send_cmd, mock_send_line):
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc", "channels": ["#test1", "#test2"]},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    # This should be interpreted on the 'unknown' path, so no other calls will be made
    await irc.handle_line(b"\x9c")

    assert irc.irc_received_privmsg.call_count == 0
    assert irc.send_cmd.call_count == 0
    assert irc.send_line.call_count == 0


@pytest.mark.asyncio
async def test_handle_line_join():
    irc = dreambot.frontend.irc.FrontendIRC(
        {"host": "abc123", "nickname": "abc", "channels": ["#test1", "#test2"]},
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )
    await irc.handle_line(b":testuser!~testident@testhost JOIN #testchannel")

    assert irc.full_ident == ":testuser!~testident@testhost "


@pytest.mark.asyncio
async def test_boot_single_loop_connection_refused(
    mocker, mock_send_cmd, mock_send_line, mock_sleep, mock_stream_reader_ateof
):
    irc = dreambot.frontend.irc.FrontendIRC(
        {
            "host": "abc123",
            "port": "1234",
            "ssl": False,
            "nickname": "abc",
            "channels": ["#test1", "#test2"],
            "ident": "testident",
            "realname": "testrealname",
        },
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    reader = AsyncMock()
    writer = AsyncMock()

    mock_open_connection = mocker.patch(
        "asyncio.open_connection",
        return_value=(reader, writer),
        side_effect=ConnectionRefusedError,
    )

    await irc.boot(reconnect=False)

    mock_open_connection.assert_called_once()
    mock_sleep.assert_not_called()
    mock_send_line.assert_not_called()
    mock_send_cmd.assert_not_called()
    mock_stream_reader_ateof.assert_not_called()


@pytest.mark.asyncio
async def test_boot_single_loop_some_other_exception(
    mocker, mock_send_cmd, mock_send_line, mock_sleep, mock_stream_reader_ateof
):
    irc = dreambot.frontend.irc.FrontendIRC(
        {
            "host": "abc123",
            "port": "1234",
            "ssl": False,
            "nickname": "abc",
            "channels": ["#test1", "#test2"],
            "ident": "testident",
            "realname": "testrealname",
        },
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    reader = AsyncMock()
    writer = AsyncMock()

    mock_open_connection = mocker.patch("asyncio.open_connection", return_value=(reader, writer), side_effect=KeyError)

    await irc.boot(reconnect=False)

    mock_open_connection.assert_called_once()
    mock_sleep.assert_not_called()
    mock_send_line.assert_not_called()
    mock_send_cmd.assert_not_called()
    mock_stream_reader_ateof.assert_not_called()


@pytest.mark.asyncio
async def test_boot_single_loop_handshake(mocker, mock_send_cmd, mock_send_line, mock_sleep, mock_handle_line):
    irc = dreambot.frontend.irc.FrontendIRC(
        {
            "host": "abc123",
            "port": "1234",
            "ssl": False,
            "nickname": "abc",
            "channels": ["#test1", "#test2"],
            "ident": "testident",
            "realname": "testrealname",
        },
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    reader = asyncio.StreamReader()
    reader.feed_data(b"Testing input line, does not need to be RFC compliant")
    reader.feed_eof()

    writer = AsyncMock()
    mock_asyncio_open_connection = mocker.patch("asyncio.open_connection", return_value=(reader, writer))
    irc.send_line = mock_send_line

    await irc.boot(reconnect=False)

    mock_asyncio_open_connection.assert_called_once()
    mock_sleep.assert_not_called()
    assert mock_send_line.call_count == 2
    mock_send_line.assert_has_calls([call("NICK abc"), call("USER testident * * :testrealname")])
    mock_send_cmd.assert_not_called()
    mock_handle_line.assert_has_calls([call(b"Testing input line, does not need to be RFC compliant")])


@pytest.mark.asyncio
async def test_boot_single_loop_with_reply(mocker, mock_sleep):
    irc = dreambot.frontend.irc.FrontendIRC(
        {
            "host": "abc123",
            "port": "1234",
            "ssl": False,
            "nickname": "abc",
            "channels": ["#test1", "#test2"],
            "ident": "testident",
            "realname": "testrealname",
        },
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    reader = asyncio.StreamReader()
    reader.feed_data(b"001")
    reader.feed_eof()

    writer = AsyncMock()
    mock_asyncio_open_connection = mocker.patch("asyncio.open_connection", return_value=(reader, writer))

    await irc.boot(reconnect=False)

    mock_asyncio_open_connection.assert_called_once()
    mock_sleep.assert_not_called()
    writer.assert_has_calls(
        [
            call.__bool__(),
            call.write(b"NICK abc\r\n"),
            call.drain(),
            call.__bool__(),
            call.write(b"USER testident * * :testrealname\r\n"),
            call.drain(),
            call.__bool__(),
            call.write(b"JOIN #test1\r\n"),
            call.drain(),
            call.__bool__(),
            call.write(b"JOIN #test2\r\n"),
            call.drain(),
            call.__bool__(),
            call.close(),
            call.wait_closed(),
            call.__bool__(),
            call.close(),
            call.wait_closed(),
        ]
    )


@pytest.mark.asyncio
async def test_boot_reconnect_ConnectionRefusedError(mocker, mock_sleep):
    irc = dreambot.frontend.irc.FrontendIRC(
        {
            "host": "abc123",
            "port": "1234",
            "ssl": False,
            "nickname": "abc",
            "channels": ["#test1", "#test2"],
            "ident": "testident",
            "realname": "testrealname",
        },
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    reader = asyncio.StreamReader()
    reader.feed_data(b"001")
    reader.feed_eof()

    writer = MagicMock()
    mock_asyncio_open_connection = mocker.patch("asyncio.open_connection", return_value=(reader, writer))

    def side_effect(*args, **kwargs):
        if mock_asyncio_open_connection.call_count >= 5:
            irc.should_reconnect = False
        raise ConnectionRefusedError

    mock_asyncio_open_connection.side_effect = side_effect

    await irc.boot()

    assert mock_asyncio_open_connection.call_count == 5


# Not really sure how to write this test, since it's a loop that's supposed to be broken by an exception
@pytest.mark.asyncio
async def test_boot_reconnect_ConnectionResetError(mocker, mock_sleep):
    irc = dreambot.frontend.irc.FrontendIRC(
        {
            "host": "abc123",
            "port": "1234",
            "ssl": False,
            "nickname": "abc",
            "channels": ["#test1", "#test2"],
            "ident": "testident",
            "realname": "testrealname",
        },
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )

    reconnect_count = 5
    reader = AsyncMock()

    async def mock_readline():
        nonlocal reconnect_count
        nonlocal irc
        reconnect_count -= 1
        if reconnect_count <= 0:
            irc.should_reconnect = False
        raise ConnectionResetError

    reader.readline = AsyncMock(side_effect=mock_readline)
    reader.at_eof = MagicMock(return_value=False)

    writer = AsyncMock()
    writer.wait_closed = AsyncMock()
    mock_asyncio_open_connection = mocker.patch("asyncio.open_connection", return_value=(reader, writer))

    def side_effect(*args, **kwargs):
        if mock_asyncio_open_connection.call_count >= 5:
            irc.should_reconnect = False
        raise ConnectionResetError

    # mock_asyncio_open_connection.side_effect = side_effect

    await irc.boot()

    assert mock_asyncio_open_connection.call_count == 5


@pytest.mark.asyncio
async def test_shutdown(mocker):
    irc = dreambot.frontend.irc.FrontendIRC(
        {
            "host": "abc123",
            "port": "1234",
            "ssl": False,
            "nickname": "abc",
            "channels": ["#test1", "#test2"],
            "ident": "testident",
            "realname": "testrealname",
        },
        {"output_dir": "/tmp", "triggers": [], "uri_base": "http://testuri/"},
        None,
    )
    irc.reader = AsyncMock()
    irc.writer = AsyncMock()

    assert irc.should_reconnect is True

    await irc.shutdown()
    assert irc.should_reconnect is False

    assert irc.writer.close.call_count == 1
    assert irc.writer.wait_closed.call_count == 1
    assert irc.reader.feed_eof.call_count == 1


@pytest.mark.asyncio
async def test_send_line_no_writer():
    irc = dreambot.frontend.irc.FrontendIRC(
        {
            "host": "abc123",
            "port": "1234",
            "ssl": False,
            "nickname": "abc",
            "channels": ["#test1", "#test2"],
            "ident": "testident",
            "realname": "testrealname",
        },
        {"output_dir": "/tmp", "triggers": [], "uri_base": "testuri"},
        None,
    )

    with pytest.raises(ValueError):
        await irc.send_line("test")
