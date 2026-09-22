"""HTTP 探针必须根据业务结果判定场景读取是否成功。"""
import importlib.util
import json
from pathlib import Path

import pytest


def test_scene_business_error_is_not_reported_as_http_success():
    """MCP 成功封装中的 Blender 错误仍必须使探针失败。"""
    path = Path(__file__).parents[1] / "tools" / "check_http.py"
    spec = importlib.util.spec_from_file_location("check_http", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = {"isError": False, "content": [{"type": "text", "text": json.dumps({"error": "Blender offline"})}]}
    with pytest.raises(RuntimeError, match="Blender offline"):
        module.decode_tool_result(result)
    result["content"][0]["text"] = json.dumps({"name": "Scene", "objects": []})
    assert module.decode_tool_result(result) == {"name": "Scene", "objects": []}
