# 内网 HTTP 调用测试

本分支增加 MCP Streamable HTTP 入口，地址为 `http://服务器内网IP:8000/mcp`。
默认启动方式仍是 STDIO；Blender 插件无需修改，继续通过本机 `127.0.0.1:9876` 连接。

## 服务端启动（运行 Blender 的机器）

1. 打开 Blender，在 MCP for Blender 面板启动连接服务。
2. 在仓库根目录打开 PowerShell，设置环境变量并启动 HTTP 服务。将示例 IP 换成这台机器实际的内网地址：

```powershell
$env:DISABLE_TELEMETRY = 'true'
& '.\.venv\Scripts\mcp-for-blender.exe' --transport streamable-http --http-host 192.168.1.42 --http-port 8000 --host 127.0.0.1 --port 9876
```

`--http-host` / `--http-port` 设置 MCP 对外监听地址；`--host` / `--port` 指向 Blender 插件。
不传 `--transport` 时仍以 STDIO 启动，不监听 HTTP 端口。HTTP 默认只绑定 `127.0.0.1:8000`，内网测试需要显式指定内网地址。

本地开发环境若尚未安装，在仓库目录先执行：

```powershell
& "$env:USERPROFILE\.local\bin\uv.exe" sync --locked --link-mode copy
```

锁文件固定已验证的依赖，使用 `--locked` 安装。从旧目录移动 `.venv` 后需追加 `--reinstall-package mcp-for-blender` 修复源码引用。

## 客户端连接（另一台机器）

在支持 Streamable HTTP 的 MCP 客户端中填写：

```text
http://192.168.1.42:8000/mcp
```

这是 MCP 协议入口，不是网页，也不是普通 REST 建模接口。在浏览器地址栏打开可能返回 406 等协议错误；使用 MCP 客户端完成初始化和工具调用。

建议先执行工具列表查询，再调用 `get_addon_status` 和 `get_scene_info`。如果能列出工具但 Blender 状态失败，检查服务端 Blender 插件是否已启动、9876 端口是否匹配。

客户端可先在 PowerShell 测试 TCP 连通性：

```powershell
Test-NetConnection 192.168.1.42 -Port 8000
```

仓库提供只依赖 Python 标准库的完整协议检查工具。在另一台机器上复制 `tools/check_http.py`，或在本机 WSL 中直接执行：

```bash
python3 tools/check_http.py http://192.168.1.42:8000/mcp --scene
```

不传 `--scene` 时只验证 MCP 初始化和工具列表；传入时额外读取插件状态和场景，不修改模型。成功后输出 `HTTP_MCP_OK`。

示例 IP 需要替换为客户端实际可达的 Windows 地址。WSL 客户端可使用 Windows 的 WSL 网卡地址；普通局域网设备应使用相应物理网卡地址。WSL 网络重建后需重新确认监听地址。

若失败，检查所选网卡地址、路由及服务端 Windows 防火墙。仅需允许测试客户端到 HTTP 端口 8000；Blender 端口 9876 保留在本机。此开发过程不会自动修改防火墙。

## 多网卡或主机名访问

服务会保留 MCP SDK 的 Host / Origin 校验。直接绑定内网 IP 时自动允许该地址；若客户端通过其他主机名访问，追加 `--http-allowed-host 主机名`，不要包含协议或端口。

监听所有 IPv4 网卡时，必须显式列出客户端访问使用的 IP 或主机名：

```powershell
& '.\.venv\Scripts\mcp-for-blender.exe' --transport streamable-http --http-host 0.0.0.0 --http-allowed-host 192.168.1.42 --http-allowed-host 192.168.1.16
```

收到 421 通常表示访问地址不在允许的 Host 列表内；403 可能是 Origin 不匹配。

## 测试范围与停止方式

- 当前提供明文 HTTP，无用户认证，仅用于受控、可信内网验证。连接者可以调用包括 Python 执行在内的现有工具，不能直接开放到公网。
- 多客户端会共享同一个 Blender 场景；第一轮验证请只让一个任务修改场景。
- 本分支尚未加入 OSS 上传、任务调度、多实例并行或浏览器跨域支持。
- 服务启动时 Blender 可以未运行，仍可验证 MCP 握手与工具列表；建模工具需要 Blender 插件在线。
- 服务端终端按 Ctrl+C 停止 HTTP 服务；Blender 插件可在其面板中单独停止。
