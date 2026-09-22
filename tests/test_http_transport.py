"""CLI compatibility and a real Streamable HTTP session (no Blender needed)."""
import asyncio
import socket
import sys
import threading
import time
from unittest.mock import Mock

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP

from blender_mcp import server
from blender_mcp.cli import configure_http, parse_server_args


def test_stdio_remains_default_and_blender_address_is_independent(monkeypatch):
    runner = Mock()
    monkeypatch.setattr(server.mcp, "run", runner)
    monkeypatch.setattr(sys, "argv", ["mcp-for-blender", "--host", "blender-box", "--port", "9877"])
    monkeypatch.setattr(server, "CLI_HOST", None)
    monkeypatch.setattr(server, "CLI_PORT", None)
    server.main()
    runner.assert_called_once_with()
    assert (server.CLI_HOST, server.CLI_PORT) == ("blender-box", 9877)


def test_http_dispatch_keeps_blender_and_http_ports_separate(monkeypatch):
    mcp = FastMCP("dispatch-test")
    runner = Mock()
    monkeypatch.setattr(mcp, "run", runner)
    monkeypatch.setattr(server, "mcp", mcp)
    monkeypatch.setattr(server, "CLI_HOST", None)
    monkeypatch.setattr(server, "CLI_PORT", None)
    monkeypatch.setattr(sys, "argv", ["mcp-for-blender", "--transport", "streamable-http",
                                     "--http-host", "192.168.1.42", "--http-port", "8001",
                                     "--host", "127.0.0.1", "--port", "9877"])
    server.main()
    runner.assert_called_once_with(transport="streamable-http")
    assert (mcp.settings.host, mcp.settings.port) == ("192.168.1.42", 8001)
    assert (server.CLI_HOST, server.CLI_PORT) == ("127.0.0.1", 9877)
    assert "192.168.1.42:8001" in mcp.settings.transport_security.allowed_hosts


@pytest.mark.parametrize("port", ["0", "-1", "65536", "not-a-port"])
def test_invalid_http_port_is_rejected(port):
    with pytest.raises(SystemExit):
        parse_server_args(["--http-port", port])


def test_wildcard_binding_requires_explicit_client_hostname():
    with pytest.raises(SystemExit):
        parse_server_args(["--transport", "streamable-http", "--http-host", "0.0.0.0"])


def test_help_after_transport_flag_does_not_start_server(monkeypatch, capsys):
    runner = Mock()
    monkeypatch.setattr(server.mcp, "run", runner)
    monkeypatch.setattr(sys, "argv", ["mcp-for-blender", "--transport", "streamable-http", "--help"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
    assert "--http-host" in capsys.readouterr().out
    runner.assert_not_called()


def test_real_http_session_accepts_lan_host_and_rejects_unknown_host_origin():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    mcp = FastMCP("http-test")

    @mcp.tool()
    def ping() -> str:
        return "pong"

    args = parse_server_args(["--transport", "streamable-http", "--http-host", "0.0.0.0",
                              "--http-port", str(port), "--http-allowed-host", "192.168.1.42"])
    configure_http(mcp, args)
    app = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), log_level="error"))
    thread = threading.Thread(target=app.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        deadline = time.monotonic() + 10
        while not app.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert app.started, "HTTP server did not start"

        async def check_session():
            async with streamablehttp_client(url, headers={"Host": f"192.168.1.42:{port}"}) as (read, write, _):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "http-test"
                    assert "ping" in [tool.name for tool in (await session.list_tools()).tools]
                    response = await session.call_tool("ping", {})
                    assert not response.isError
                    assert response.content[0].text == "pong"

        asyncio.run(check_session())
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        with httpx.Client(trust_env=False) as client:
            response = client.post(url, headers={**headers, "Host": f"untrusted.example:{port}"}, json={})
            assert response.status_code == 421
            response = client.post(url, headers={**headers, "Origin": "http://untrusted.example"}, json={})
            assert response.status_code == 403
    finally:
        app.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "HTTP test server did not stop"
