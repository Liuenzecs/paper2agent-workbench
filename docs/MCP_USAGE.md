# MCP 工具使用指南

本项目通过 stdio 提供 MCP 工具，调用 `vendor/repo` 中保留的研究函数。函数来源、文件校验值、参数说明与依赖信息记录于 `manifest.json`，上游许可保存在源码目录中。

## 安装与启动

使用 Python 3.11 创建独立环境，审阅源码及依赖声明后安装：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

最后一条命令启动 MCP 服务，等待客户端连接。依赖应与研究函数的运行要求匹配。

## 工具调用

在支持 stdio 的 MCP 客户端中，将解释器设置为环境内的 Python，启动参数设置为 `server.py` 的绝对路径。

命令行测试入口：

```powershell
.\.venv\Scripts\python.exe example_call.py TOOL_NAME arguments.json
```

`TOOL_NAME` 为 `manifest.json` 中的工具名称，`arguments.json` 为 UTF-8 JSON 参数对象。输入需符合函数类型与领域约束。每次调用在 `outputs/` 中保存独立结果文件，包含原生结果与源码引用。

## 结果核对

使用同一输入直接调用原函数，将结果与 MCP 返回的 `native_result` 对照。关键数值、统计假设和数据适用范围应结合上游论文及接口说明核对。

## 运行管理

工具调用会导入模块并执行研究代码，具有当前系统用户的权限。源码与依赖审阅用于判断运行条件；复杂类型、动态依赖和外部数据需要按项目要求配置。
