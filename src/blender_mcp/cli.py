"""Shared CLI options for the server and addon installer help."""
import argparse
import logging


def http_port(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("HTTP port must be between 1 and 65535")
    return port


def add_server_arguments(parser):
    parser.add_argument("--host", default=None,
                        help="Blender socket host (overrides BLENDER_HOST)")
    parser.add_argument("--port", type=int, default=None,
                        help="Blender socket port (overrides BLENDER_PORT)")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio",
                        help="MCP client transport (default: stdio)")
    parser.add_argument("--http-host", default="127.0.0.1",
                        help="HTTP bind address; use this machine's LAN IP for remote access")
    parser.add_argument("--http-port", type=http_port, default=8000,
                        help="HTTP listening port (default: 8000; endpoint: /mcp)")
    parser.add_argument("--http-allowed-host", action="append", default=[], metavar="HOST",
                        help="Additional HTTP hostname/IP without scheme or port; repeatable. "
                             "Required with --http-host 0.0.0.0 or ::")


def parse_server_args(argv):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    add_server_arguments(parser)
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        logging.getLogger("BlenderMCPServer").warning(
            "Ignoring unrecognized command-line arguments: %s", unknown
        )
    if (args.transport == "streamable-http"
            and args.http_host in ("0.0.0.0", "::")
            and not args.http_allowed_host):
        parser.error("Wildcard HTTP binding requires --http-allowed-host with the LAN IP "
                     "or hostname clients will use")
    return args


def configure_http(mcp, args):
    """Keep explicit Host/Origin checks when exposing the SDK on a LAN."""
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = ["127.0.0.1", "localhost", "::1", *args.http_allowed_host]
    if args.http_host not in ("0.0.0.0", "::"):
        hosts.append(args.http_host)
    authorities = []
    for host in dict.fromkeys(hosts):
        # Brackets delimit IPv6 literals in HTTP authority/Origin values.
        host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        authorities.append(f"{host}:{args.http_port}")
        if args.http_port == 80:
            authorities.append(host)
    mcp.settings.host = args.http_host
    mcp.settings.port = args.http_port
    mcp.settings.streamable_http_path = "/mcp"
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=authorities,
        allowed_origins=[f"http://{authority}" for authority in authorities],
    )
