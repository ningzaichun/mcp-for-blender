"""Read-only MCP HTTP check from another machine; Python standard library only."""
import argparse
import http.client
import json
from urllib.parse import urlsplit


def decode_tool_result(result):
    """同时验证 MCP 状态与 Blender 返回的业务错误。"""
    if result.get("isError"):
        raise RuntimeError(result)
    text = "\n".join(item["text"] for item in result["content"] if item["type"] == "text")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(text) from exc
    if not isinstance(payload, dict) or payload.get("error"):
        raise RuntimeError(payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="For example: http://192.168.1.42:8000/mcp")
    parser.add_argument("--scene", action="store_true", help="Also read Blender addon and scene status")
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme not in ("http", "https") or not url.hostname:
        parser.error("Expected an http:// or https:// MCP endpoint")
    connection_type = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    session_id = None
    protocol = "2025-03-26"
    request_id = 0

    def request(method, params=None, notification=False, delete=False):
        nonlocal session_id, request_id
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if session_id:
            headers["Mcp-Session-Id"] = session_id
            headers["MCP-Protocol-Version"] = protocol
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            request_id += 1
            payload["id"] = request_id
        connection = connection_type(url.hostname, url.port, timeout=30)
        try:
            connection.request("DELETE" if delete else "POST", url.path or "/mcp",
                               body=None if delete else json.dumps(payload), headers=headers)
            response = connection.getresponse()
            session_id = response.getheader("Mcp-Session-Id") or session_id
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}: {response.read(2000).decode(errors='replace')}")
            if delete or notification:
                response.read()
                return None
            if "text/event-stream" in response.getheader("Content-Type", ""):
                while line := response.readline():
                    if line.startswith(b"data:"):
                        result = json.loads(line[5:].strip())
                        if result.get("id") == request_id:
                            break
                else:
                    raise RuntimeError("SSE stream ended without an RPC response")
            else:
                result = json.loads(response.read())
            if "error" in result:
                raise RuntimeError(result["error"])
            return result["result"]
        finally:
            connection.close()

    def call_tool(name):
        result = request("tools/call", {"name": name, "arguments": {
            "user_prompt": "Read-only verification of remote HTTP connectivity."
        }})
        return decode_tool_result(result)

    try:
        initialized = request("initialize", {"protocolVersion": protocol, "capabilities": {},
                                               "clientInfo": {"name": "lan-http-check", "version": "1"}})
        protocol = initialized["protocolVersion"]
        request("notifications/initialized", notification=True)
        tools = request("tools/list")["tools"]
        report = {"url": args.url, "server": initialized["serverInfo"], "tool_count": len(tools)}
        if args.scene:
            addon = call_tool("get_addon_status")
            if not addon.get("up_to_date"):
                raise RuntimeError(addon)
            scene = call_tool("get_scene_info")
            report.update(addon_protocol=addon["protocol_version"], blender_version=addon["blender_version"],
                          scene=scene)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("HTTP_MCP_OK")
    finally:
        if session_id:
            request("", delete=True)


if __name__ == "__main__":
    main()
