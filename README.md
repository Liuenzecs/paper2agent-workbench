# Paper2Agent

[![CI](https://github.com/Liuenzecs/paper2agent-workbench/actions/workflows/verify.yml/badge.svg)](https://github.com/Liuenzecs/paper2agent-workbench/actions/workflows/verify.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)

Paper2Agent 是一个连接论文阅读与研究代码的开源工作台。通过本机网页或命令行管理论文、进行带引用的问答，并将论文关联的 Python 代码转换为可调用的 MCP 工具。

## 功能

- **论文库**：导入 PDF，选择检索范围，按独立工作区管理论文与问答记录。
- **论文问答**：基于论文内容回答问题，展示引用原文与 PDF 页码，支持跨论文比较和缺证据拒答。
- **API 配置**：对接 OpenAI 兼容的问答与嵌入服务，两类服务可分别配置；支持 Windows 用户加密保存密钥。
- **代码转 MCP**：获取公开 GitHub 仓库或读取本机代码，扫描 Python 函数，生成调用原函数的 MCP 项目，测试调用并打包分发。
- **结果管理**：保留每次问答和工具调用记录，支持将问答导出为 Markdown 或 JSON。

## 安装

### 系统要求

- Windows x64
- Python 3.11
- Git（获取 GitHub 仓库代码时使用）
- 支持 OpenAI 兼容接口的问答与嵌入 API（论文问答时使用）

### 获取程序

从 [最新发行版](https://github.com/Liuenzecs/paper2agent-workbench/releases/latest) 下载 `Paper2Agent-1.0.0.zip` 并解压，或克隆仓库：

```powershell
git clone https://github.com/Liuenzecs/paper2agent-workbench.git
cd paper2agent-workbench
```

### 安装依赖

在程序根目录打开 PowerShell，运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装器会创建 Python 环境并安装锁定版本的依赖。首次安装需要联网。

指定 Python 路径和环境目录时，使用以下参数：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Python "C:\Python311\python.exe" -EnvironmentDir "C:\P2A-env\workbench"
```

## 网页使用

双击 `Start-Web.cmd` 启动，浏览器默认打开 `http://127.0.0.1:8765`。双击 `Stop-Web.cmd` 停止服务。

### 配置 API

打开「API 设置」，填写服务信息并保存：

| 配置项 | 内容 |
| --- | --- |
| 问答 API 地址 | 服务的 HTTPS 基础地址 |
| 问答模型 | 服务提供的模型名称 |
| 嵌入 API 地址 | 嵌入服务的基础地址；留空沿用问答地址 |
| 嵌入模型 | 服务提供的嵌入模型名称 |
| 问答密钥、嵌入密钥 | 对应服务的 API 密钥 |

问答和文本嵌入通过配置的远程 API 完成。论文片段与问题会发送至对应服务，调用费用由服务提供方收取。密钥支持在当前 Windows 用户下加密保存。

### 论文问答

1. 点击「导入论文」，添加可搜索文本的 PDF。
2. 勾选论文，输入问题，选择回答语言。
3. 查看答案、引用原文与 PDF 页码，或导出问答结果。

跨论文比较时，可勾选「引用全部选中论文」。问答记录可从「问答历史」重新打开。

### 代码转 MCP

1. 打开「代码转 MCP」，填写公开 GitHub 仓库地址并获取代码，或填写本机代码目录。
2. 扫描函数，选择需要转换的函数，生成 MCP 项目。
3. 选择工具，填写 JSON 参数并运行测试。独立依赖环境可通过「Python 路径」指定。
4. 通过 CLI 的 `code-package` 命令打包项目，在 MCP 客户端中使用生成的 `server.py`。

代码转换支持具有明确参数类型的同步 Python 函数及 JSON 可表达的输入。生成项目保留原函数绑定、源文件校验值和上游许可，便于追踪与核对。

## CLI 使用

在程序根目录通过 `paper.cmd` 操作：

```powershell
.\paper.cmd --help
.\paper.cmd status
.\paper.cmd ingest "D:\Papers\paper.pdf" --title "论文标题"
.\paper.cmd list
.\paper.cmd configure --api-base "https://api.provider.com/v1" --chat-model "CHAT_MODEL" --embedding-model "EMBEDDING_MODEL" --enter-keys --remember-keys
```

将命令中的路径、服务地址和模型名称替换为实际配置。密钥通过隐藏输入填写。

| 命令 | 用途 |
| --- | --- |
| `ingest`、`list` | 导入论文、查看论文库 |
| `configure`、`status` | 配置 API、查看工作区状态 |
| `ask`、`history`、`export` | 论文问答、历史记录、结果导出 |
| `code-fetch`、`code-scan` | 获取代码、扫描函数 |
| `code-mcp`、`code-test`、`code-package` | 生成、测试与打包 MCP |
| `web` | 启动网页服务 |

运行 `paper.cmd <命令> --help` 查看参数。详细操作见 [使用指南](USAGE.md)。

## 工作区与数据

论文库、API 设置、缓存、问答记录与 MCP 项目保存在独立数据目录。使用 `--data-dir` 可为不同任务指定工作区：

```powershell
.\paper.cmd --data-dir "D:\Paper2Agent-data" status
powershell -ExecutionPolicy Bypass -File .\start-web.ps1 -DataDir "D:\Paper2Agent-data" -Port 8767
```

数据存储和凭据管理见 [安全说明](SECURITY.md)，安装与故障处理见 [使用指南](USAGE.md)。

## 开发与贡献

开发环境、测试与贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。自动化验证覆盖论文库、API 配置、引用定位、代码转换与 MCP 调用，验证范围见 [docs/VALIDATION.md](docs/VALIDATION.md)。

欢迎通过 [Issues](https://github.com/Liuenzecs/paper2agent-workbench/issues) 报告问题或提出建议，通过 Pull Request 参与改进。

## 许可与致谢

主程序和文档采用 **AGPL-3.0-only** 许可，见 [LICENSE](LICENSE)。第三方组件的许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

项目受到 [Paper2Agent](https://github.com/jmiao24/Paper2Agent) 启发，论文证据问答基于 [PaperQA](https://github.com/Future-House/paper-qa)，MCP 工具服务使用 [FastMCP](https://github.com/PrefectHQ/fastmcp)。
