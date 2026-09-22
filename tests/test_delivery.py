"""交付工具的文件与上传能力边界。"""

import base64
import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from starlette.datastructures import Headers

from blender_mcp import delivery, server


@pytest.fixture
def upload_setup(tmp_path, monkeypatch):
    """提供临时授权根及明确的测试上传描述。"""
    monkeypatch.setenv("YUXI_DELIVERY_ROOT", str(tmp_path))
    monkeypatch.setenv("YUXI_DELIVERY_TOKEN", "test-only-token")
    monkeypatch.setenv("YUXI_DELIVERY_UPLOAD_URL", "https://storage.example.com/yuxi-deliveries")
    value = {"protocol_version": 1, "artifact_id": "artifact-1",
             "upload_url": "https://storage.example.com/yuxi-deliveries",
             "form_fields": {"key": "delivery/key", "policy": "test-only-policy"},
             "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(), "max_bytes": 1024}
    path = tmp_path / "scene.fbx"
    path.write_bytes(b"FBX test file")
    return value, path


def headers(value, token="test-only-token"):
    """构造与真实 HTTP 相同的不区分大小写请求头。"""
    return Headers({delivery.TOKEN_HEADER: token, delivery.UPLOAD_HEADER:
                    base64.urlsafe_b64encode(json.dumps(value).encode()).decode()})


@pytest.mark.parametrize("case", ["auth", "destination", "expired", "size", "outside", "extension", "directory"])
def test_invalid_authority_never_sends_file(upload_setup, monkeypatch, tmp_path, case):
    """拒绝非法授权、目标或路径；失败必须发生在上传副作用之前。"""
    value, path = upload_setup
    token = "test-only-token"
    if case == "auth":
        token = "wrong"
    elif case == "destination":
        value["upload_url"] = "https://other.example.com/yuxi-deliveries"
    elif case == "expired":
        value["expires_at"] = "2000-01-01T00:00:00+00:00"
    elif case == "size":
        value["max_bytes"] = 2
    elif case == "outside":
        path = tmp_path.parent / "outside.fbx"
    elif case == "extension":
        path = tmp_path / "secret.txt"
    elif case == "directory":
        path = tmp_path / "folder.fbx"
        path.mkdir()
    def no_network(*args, **kwargs):
        pytest.fail("invalid input reached network")
    monkeypatch.setattr(delivery.httpx, "Client", no_network)
    with pytest.raises(delivery.DeliveryError):
        delivery.upload_delivery_file(str(path), headers(value, token))


@pytest.mark.parametrize("status,version", [(307, "v1"), (204, None), (204, "v1")])
def test_upload_bytes_and_version_response(upload_setup, monkeypatch, status, version):
    """真正的 multipart 编码必须包含文件字节，重定向和无版本响应不得成功。"""
    value, path = upload_setup
    client_type = httpx.Client
    observed = []
    def receive(request):
        observed.append(request.read())
        return httpx.Response(status, headers={"x-amz-version-id": version} if version else {})
    monkeypatch.setattr(delivery.httpx, "Client", lambda **kwargs:
                        client_type(transport=httpx.MockTransport(receive), **kwargs))
    if status != 204 or version is None:
        with pytest.raises(delivery.DeliveryError):
            delivery.upload_delivery_file(str(path), headers(value))
    else:
        result = delivery.upload_delivery_file(str(path), headers(value))
        assert result["status"] == "uploaded"
        assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert result["version_id"] == "v1"
        assert path.read_bytes() in observed[0]
        assert "test-only-policy" not in json.dumps(result)


@pytest.mark.skipif(os.name != "nt", reason="Windows 文件共享句柄语义")
def test_windows_source_cannot_be_changed_while_snapshotted(upload_setup):
    """已打开的授权文件拒绝并发替换或写入。"""
    _, path = upload_setup
    with delivery.open_delivery_source(str(path), path.parent) as source:
        with pytest.raises(OSError):
            path.write_bytes(b"changed")
        assert source.read() == b"FBX test file"


def test_upload_tool_schema_has_only_file_and_structured_result():
    """SDK 自动注册必须保留结构化返回及隐藏的 Context。"""
    tool = next(item for item in asyncio.run(server.mcp.list_tools()) if item.name == "upload_delivery_file")
    assert set(tool.inputSchema["properties"]) == {"filepath"}
    assert tool.outputSchema is not None


def test_link_inside_root_cannot_publish_external_file(upload_setup, tmp_path, monkeypatch):
    """真实目录链接不能把授权根外的文件带入上传。"""
    value, _ = upload_setup
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.blend").write_bytes(b"not authorized")
    link = tmp_path / "linked"
    link.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(delivery.httpx, "Client", lambda **kwargs: pytest.fail("link reached network"))
    with pytest.raises(delivery.DeliveryError, match="invalid_delivery_path"):
        delivery.upload_delivery_file(str(link / "secret.blend"), headers(value))


def test_network_failure_never_returns_authorization(upload_setup, monkeypatch):
    """可能已上传的网络异常只返回不确定状态，不传播带签名的异常文本。"""
    value, path = upload_setup
    client_type = httpx.Client
    def disconnected(request):
        raise httpx.ReadError("private policy: test-only-policy", request=request)
    monkeypatch.setattr(delivery.httpx, "Client", lambda **kwargs:
                        client_type(transport=httpx.MockTransport(disconnected), **kwargs))
    with pytest.raises(delivery.DeliveryError) as failure:
        delivery.upload_delivery_file(str(path), headers(value))
    assert str(failure.value) == "delivery_upload_unconfirmed"
