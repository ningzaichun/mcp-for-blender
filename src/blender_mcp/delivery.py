"""将已保存的本地交付副本上传到 Yuxi 指定的私有对象。"""

import base64
import ctypes
import hashlib
import hmac
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

UPLOAD_HEADER = "X-Yuxi-Artifact-Upload"
TOKEN_HEADER = "X-Yuxi-Delivery-Token"
MAX_BYTES = 100 * 1024 * 1024


class DeliveryError(Exception):
    """可安全返回模型的上传错误码。"""


def read_upload_descriptor(headers):
    """验证部署认证及当前调用的有限上传授权。"""
    token = os.environ.get("YUXI_DELIVERY_TOKEN", "")
    expected_url = os.environ.get("YUXI_DELIVERY_UPLOAD_URL", "")
    root = os.environ.get("YUXI_DELIVERY_ROOT", "")
    if not token or not expected_url or not root:
        raise DeliveryError("delivery_not_configured")
    if not hmac.compare_digest(headers.get(TOKEN_HEADER, ""), token):
        raise DeliveryError("delivery_unauthorized")
    encoded = headers.get(UPLOAD_HEADER, "")
    if not encoded or len(encoded) > 8192:
        raise DeliveryError("invalid_upload_descriptor")
    try:
        value = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
        parsed = urlsplit(value["upload_url"])
        expires = datetime.fromisoformat(value["expires_at"])
        if (
            value["protocol_version"] != 1
            or value["upload_url"] != expected_url
            or parsed.scheme not in {"http", "https"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not isinstance(value["artifact_id"], str)
            or not value["artifact_id"]
            or not isinstance(value["form_fields"], dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in value["form_fields"].items())
            or not value["form_fields"].get("key")
            or type(value["max_bytes"]) is not int
            or not 0 < value["max_bytes"] <= MAX_BYTES
            or expires.tzinfo is None
            or expires <= datetime.now(timezone.utc)
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise DeliveryError("invalid_upload_descriptor") from None
    return value, Path(root).resolve(strict=True)


@contextmanager
def open_delivery_source(filepath, root):
    """以实际打开的文件句柄校验授权根，Windows 禁止并发写入与替换。"""
    path = Path(filepath)
    if not path.is_absolute() or path.suffix.lower() not in {".blend", ".fbx", ".png"}:
        raise DeliveryError("invalid_delivery_path")
    if ".." in path.parts or any(":" in part for part in path.parts[1:]):
        raise DeliveryError("invalid_delivery_path")
    try:
        path.relative_to(root)
        for parent in (path, *path.parents):
            if parent == root:
                break
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise DeliveryError("invalid_delivery_path")
        if os.name == "nt":
            import msvcrt
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                          wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x08000000, None)
            if handle == wintypes.HANDLE(-1).value:
                raise OSError("source unavailable")
            try:
                kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                                           wintypes.DWORD, wintypes.DWORD]
                buffer = ctypes.create_unicode_buffer(32768)
                length = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
                if not 0 < length < len(buffer):
                    raise OSError("invalid source handle")
                resolved = buffer.value
                if resolved.startswith("\\\\?\\UNC\\"):
                    resolved = "\\\\" + resolved[8:]
                elif resolved.startswith("\\\\?\\"):
                    resolved = resolved[4:]
                Path(resolved).relative_to(root)
                fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel.CloseHandle(handle)
                raise
        else:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                Path(f"/proc/self/fd/{fd}").resolve(strict=True).relative_to(root)
            except BaseException:
                os.close(fd)
                raise
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise DeliveryError("invalid_delivery_path")
            yield source
    except (OSError, ValueError):
        raise DeliveryError("delivery_source_unavailable") from None


def upload_delivery_file(filepath, headers):
    """上传稳定副本并返回待 Yuxi 独立核验的版本信息。"""
    descriptor, root = read_upload_descriptor(headers)
    with open_delivery_source(filepath, root) as source, tempfile.TemporaryFile() as snapshot:
        before = os.fstat(source.fileno())
        if not 0 < before.st_size <= descriptor["max_bytes"]:
            raise DeliveryError("delivery_size_exceeded")
        digest = hashlib.sha256()
        size = 0
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > descriptor["max_bytes"]:
                raise DeliveryError("delivery_size_exceeded")
            digest.update(chunk)
            snapshot.write(chunk)
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != before.st_size:
            raise DeliveryError("delivery_source_changed")
        snapshot.seek(0)
        try:
            with httpx.Client(timeout=httpx.Timeout(120, connect=10), follow_redirects=False) as client:
                response = client.post(descriptor["upload_url"], data=descriptor["form_fields"],
                                       files={"file": ("delivery", snapshot, "application/octet-stream")})
            if response.status_code not in {200, 201, 204}:
                raise DeliveryError("delivery_upload_rejected")
            version = response.headers.get("x-amz-version-id")
            if not version or version == "null":
                raise DeliveryError("delivery_version_missing")
        except httpx.HTTPError:
            raise DeliveryError("delivery_upload_unconfirmed") from None
    return {"status": "uploaded", "artifact_id": descriptor["artifact_id"],
            "filename": Path(filepath).name, "size": size, "sha256": digest.hexdigest(), "version_id": version}
