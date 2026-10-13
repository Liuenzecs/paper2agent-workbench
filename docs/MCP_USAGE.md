# MCP 工具使用指南

Paper2Agent 生成通过 stdio 提供服务的 MCP 项目，直接调用保留的研究函数或审核入口。函数来源、文件校验值、参数说明与依赖信息记录于 `manifest.json`；适配器许可保存在项目根目录，上游许可保存在 `vendor/repo` 中。

## 转换方式

| 方式 | 接受的入口 | 必要条件 |
| --- | --- | --- |
| Python 类型扫描 | 明确注解的 Python 函数与 JSON 类型 | 静态扫描通过，依赖可安装 |
| Python 接口契约 | 数组、表格、文件路径、异步函数、类方法 | 明确审核、源码哈希与参数适配定义 |
| 原生命令契约 | R、Python 命令行入口及可执行程序 | 审核的 JSON 请求入口、固定参数模板、来源哈希与运行时身份 |

扫描发现候选入口；科学参数和调用方式由接口审阅确定。复杂代码不会仅凭函数名自动成为已验证工具。

## 接口契约

接口文件采用 UTF-8 JSON，在网页「接口定义」导入，或通过 `code-mcp --contracts-file` 指定。源文件变化后，需重新审阅并更新哈希。

### Python

顶层字段为 `schema_version: 1` 与 `tools`。工具键使用扫描返回的完整函数 ID。每项工具必须有 `reviewed: true`、`source_sha256` 与 `parameters`。

参数定义包含 `schema`，可包含 `adapter`。支持的适配方式：

| `adapter.kind` | 定义与输入 |
| --- | --- |
| `identity` | 按声明的 JSON 类型原样传入 |
| `ndarray` | 声明 `dtype` 为 `float64`、`int64` 或 `bool`，以及 `shape`；JSON 数组维度与元素类型需匹配 |
| `dataframe` | 固定列顺序及列类型；输入对象包含 `columns` 与 `data` |
| `path` | 声明 `mode: input/output`、`return_type: str/path`；传入托管目录中的相对路径 |

输入文件放在生成项目的 `inputs/` 中；输出文件写入本次调用目录，路径不得越过托管目录。类方法需要 `constructor.parameters`，每次调用创建独立实例。科学依赖需安装在选定的 Python 环境中。

`rules` 可声明接口支持的领域约束。结果默认拒绝非有限数值；已审核的 `null_with_mask` 策略可将合法结构性 NaN 表示为 `null` 并保留位置掩码。该表示策略不能代替原生结果核对。

### 原生命令

顶层字段为 `schema_version: 1`、`route: command` 与 `tools`。工具键为合法标识符，每项工具包含：

| 字段 | 内容 |
| --- | --- |
| `reviewed` | 必须为 `true` |
| `entry_file`、`source_files` | 审核入口的相对路径与全部绑定源文件 SHA256 |
| `parameters` | 参数的 `schema`、`description` 与可选 `default` |
| `argv` | 固定参数数组，各包含一次 `{entry_file}`、`{request_file}`、`{output_dir}` |
| `runtime` | `kind: r/python/executable`、`env: P2A_NATIVE_RUNTIME`、版本检查参数和预期版本文字；可执行程序必须绑定 SHA256 |
| `result_file` | 本次输出目录内的 JSON 结果相对路径 |
| `timeout_seconds` | 原生调用时限，1–180 秒 |
| `description`、`reference`、`native_dependencies` | 科学用途、出处与所需原生依赖 |

可选字段为 `rules` 与 `result_schema`。入口从请求文件读取经过校验的 JSON，在指定目录保存结果及原生文件；用户参数不插入命令字符串。R 项目库的激活由审核入口负责。原生错误、超时、格式不合法或来源变化会使 MCP 调用失败。

## 安装与启动

使用 Python 3.11 创建独立环境，审阅源码及依赖声明后安装：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

最后一条命令启动 MCP 服务，等待客户端连接。依赖应与研究函数的运行要求匹配。Windows 的程序、环境与工作区路径宜保持简短，避免科学依赖生成嵌套文件时超过系统路径限制。

原生命令工具还需指定已安装的运行程序：

```powershell
$env:P2A_NATIVE_RUNTIME = "NATIVE_PROGRAM_PATH"
$env:P2A_R_PROJECT = "R_PROJECT_PATH"
$env:LC_ALL = "English_United States.utf8"
```

`P2A_R_PROJECT` 只用于需要项目库的 R 入口。该区域设置适用于 Windows；其他系统使用其可用的 UTF-8 区域设置。启动方式和依赖身份以生成项目的 `USAGE.md` 与 `manifest.json` 为准。

## 工具调用

在支持 stdio 的 MCP 客户端中，将解释器设置为环境内的 Python，启动参数设置为 `server.py` 的绝对路径。

命令行测试入口：

```powershell
.\.venv\Scripts\python.exe call_tool.py TOOL_NAME arguments.json
```

`TOOL_NAME` 为 `manifest.json` 中的工具名称，`arguments.json` 为 UTF-8 JSON 参数对象。输入需符合函数类型与领域约束。Python 函数调用在 `outputs/` 中保存独立结果；原生命令在 `artifacts/` 中保存请求、原生输出、标准输出、标准错误和运行状态。

## 结果核对

使用同一输入直接调用原函数，将完整结果与 MCP 返回值及保留的原生文件对照。比对全部字段、数组、置信区间、属性和缺失值位置，并测试变化输入及失败输入。关键数值、统计假设和数据适用范围应结合上游论文及接口说明核对。

## 运行管理

工具调用会导入模块并执行研究代码，具有当前系统用户的权限。源码与依赖审阅用于判断运行条件；复杂类型、动态依赖和外部数据需要按项目要求配置。
