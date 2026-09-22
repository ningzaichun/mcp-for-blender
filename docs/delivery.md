# 向 Yuxi 交付 Blender 文件

`upload_delivery_file(filepath)` 上传已保存文件的副本。原工程保留在生产方机器，后续修改继续使用该工程。工具不会下载模型、切换场景、隐式保存或导出 FBX。支持 `.blend`、`.fbx` 和 `.png`，单文件最多 100 MiB。

## 配置

先按 [HTTP 接入说明](lan-http.md) 安装并启动支持 Streamable HTTP 的版本，使用锁文件中的 MCP SDK（最低 1.29.0）。生产方进程设置：

```powershell
$env:YUXI_DELIVERY_ROOT = 'C:\BlenderDeliveries'
$env:YUXI_DELIVERY_UPLOAD_URL = 'http://storage.example.test:9000/yuxi-deliveries'
$env:YUXI_DELIVERY_TOKEN = '<管理员生成的随机凭据>'
$env:DISABLE_TELEMETRY = 'true'
```

示例目录、地址与凭据均需替换。授权根必须已存在；工具只读取该根内的普通文件，拒绝目录链接、符号链接、Windows reparse point、目录穿越和备用数据流。上传使用临时稳定副本；Windows 源文件句柄在调用期间拒绝并发写入或替换，因此请串行保存与上传。

Yuxi 的 MCP 配置增加请求头 `X-Yuxi-Delivery-Token`，与生产方环境值一致。Yuxi worker 为每次调用注入隐藏的 `X-Yuxi-Artifact-Upload` 描述，包括精确上传 URL、单一对象 key、大小限制及到期时间。工具 schema 只有 `filepath`；签名和管理凭据不进入模型参数。上传地址必须与生产方配置完全匹配，HTTP 重定向被拒绝。

这个凭据只约束上传工具。现有 Blender 代码执行工具与 HTTP 端点仍只适用于受信任网络、单用户测试；整个服务不具备多租户隔离。标准 STDIO 调用不具备隐藏 HTTP 描述，不能使用此交付工具。

## 交付顺序与结果

1. 保存本次修改后的 `.blend`，外部资源按需要打包。
2. 使用已有 `export_scene(format="fbx", filepath=..., object_names=[...])`，明确导出范围。导出失败时不要上传同名旧文件。
3. 通过已有 Blender 代码执行能力渲染或保存持久 PNG。普通截图的临时 Image 返回不能代替已保存的 PNG。
4. 对三个绝对路径分别调用 `upload_delivery_file`，生成和上传期间不继续改模。

工具以流式 multipart POST 上传，返回对象 `version_id`、实际字节数与 SHA-256。`uploaded` 只代表生产方收到了版本响应；Yuxi worker 独立回读该版本、校验摘要并确认当前 Run ownership 后，才发布 `ready` 并在对话提供预览/下载。

错误以脱敏 `error_code` 返回。网络响应不确定返回 `delivery_upload_unconfirmed`，对象可能已经写入；不会查询最新版本猜测成功。错误目标、认证失败、过期描述或授权根外路径在发送文件前拒绝。日志及返回值不包含上传描述或 HTTP 异常正文。

后续编辑继续使用生产方原工程；再次上传创建新的交付副本。FBX 是交换格式，需要重新导入核对尺寸、方向与对象范围，不保证原生分组、修改器、材质完全保留。

## 验证

运行 `python -m pytest tests/test_delivery.py -q` 验证授权、路径、版本返回、错误脱敏、Windows 写锁及 SDK 工具 schema。Windows 的目录链接测试需要允许创建符号链接的开发环境。

独立后台 Blender 样例命令（替换可执行文件路径）：

```powershell
& '<Blender可执行文件>' --background --factory-startup --python-exit-code 1 --python tools/delivery_demo.py -- local_tests/deliveries
```

脚本拒绝在交互式 Blender 执行，生成两版 `.blend`、`.fbx`、PNG 及 `verification.json`；通过现有 addon 导出器导出，再导入空场景检查世界尺寸分别为 `2×2×2` 与 `2×2×3`。样例不覆盖复杂工程、贴图依赖或多用户并发。上传与页面验收仍需 Yuxi 的真实 Run/worker 和私有版本桶。

把鉴权下载的六份同名文件保存到独立目录后，在命令末尾追加 `--verify-only` 并将输出参数改为该目录，可只重新打开 `.blend`、重新导入 FBX 核对几何，不生成覆盖文件。结果写入该目录的 `verification.json`。
