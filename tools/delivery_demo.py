"""在独立后台 Blender 中生成并复验三种交付文件，不修改交互式场景。"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def main():
    """生成两个尺寸不同的版本，再把 FBX 导入空场景核对几何。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--verify-only", action="store_true", help="只检查从对话下载的同名文件")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("仅允许在 --background --factory-startup 的测试进程中运行")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    addon_path = Path(__file__).resolve().parents[1] / "addon.py"
    spec = importlib.util.spec_from_file_location("delivery_demo_addon", addon_path)
    addon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(addon)
    exporter = addon.BlenderMCPServer()
    records = []
    for revision, height in ((1, 2.0), (2, 3.0)):
        blend = output / f"model-v{revision}.blend"
        fbx = output / f"model-v{revision}.fbx"
        if not args.verify_only:
            bpy.ops.wm.read_factory_settings(use_empty=False)
            model = bpy.data.objects["Cube"]
            model.name = "DeliveryDemo"
            model.dimensions = (2.0, 2.0, height)
            bpy.context.view_layer.update()
            scene = bpy.context.scene
            scene.render.engine = "BLENDER_WORKBENCH"
            scene.render.resolution_x = 480
            scene.render.resolution_y = 360
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = "PNG"
            scene.render.filepath = str(output / f"preview-v{revision}.png")
            bpy.ops.wm.save_as_mainfile(filepath=str(blend))
            exported = exporter.export_scene(str(fbx), format="fbx", object_names=[model.name])
            if exported.get("error") or not fbx.is_file() or fbx.stat().st_size == 0:
                raise RuntimeError("FBX export failed")
            bpy.ops.render.render(write_still=True)
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        model = bpy.data.objects["DeliveryDemo"]
        assert all(abs(actual - expected) < 0.001 for actual, expected in zip(model.dimensions, (2, 2, height)))
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=str(fbx))
        bpy.context.view_layer.update()
        imported = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        assert len(imported) == 1
        bounds = [imported[0].matrix_world @ Vector(corner) for corner in imported[0].bound_box]
        dimensions = tuple(max(point[axis] for point in bounds) - min(point[axis] for point in bounds) for axis in range(3))
        assert all(abs(actual - expected) < 0.001 for actual, expected in zip(dimensions, (2, 2, height))), dimensions
        records.append({"revision": revision, "world_dimensions": dimensions, "blend_reopen": True, "fbx_reimport": True})
    (output / "verification.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    print("DELIVERY_DEMO_GEOMETRY_VERIFIED")


if __name__ == "__main__":
    main()
