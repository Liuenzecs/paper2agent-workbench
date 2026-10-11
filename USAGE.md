# 使用指南

Paper2Agent 提供本机网页和 CLI，共用论文库、API 配置、问答记录与 MCP 项目。本指南介绍安装、配置和日常操作。

## 安装与启动

运行环境为 Windows x64、Python 3.11。首次安装需要联网；获取 GitHub 源码需要 Git。

从 [最新发行版](https://github.com/Liuenzecs/paper2agent-workbench/releases/latest) 下载 ZIP 并解压，在程序根目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

指定解释器和独立环境目录：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Python "C:\Python311\python.exe" -EnvironmentDir "C:\P2A-env\workbench"
```

双击 `Start-Web.cmd` 启动网页，默认地址为 `http://127.0.0.1:8765`。双击 `Stop-Web.cmd` 停止后台服务。关闭浏览器标签页后，服务仍会继续运行。

## 工作区

默认数据目录为 `%LOCALAPPDATA%\Paper2AgentLab`。论文、设置、缓存、问答记录和 MCP 项目存放在数据目录中，与程序文件分离。

指定工作区和网页端口：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-web.ps1 -DataDir "D:\Paper2Agent-data" -Port 8767
powershell -ExecutionPolicy Bypass -File .\stop-web.ps1 -DataDir "D:\Paper2Agent-data"
.\paper.cmd --data-dir "D:\Paper2Agent-data" status
```

同一任务的网页和 CLI 使用相同的数据目录；并行任务可使用不同目录与端口。

## API 配置

在「API 设置」填写问答 API 地址、问答模型、嵌入模型和对应密钥。两类服务使用不同地址时，填写独立的嵌入 API 地址。地址使用 HTTPS，模型名称填写服务提供方支持的实际名称。

接口需兼容 OpenAI `chat completions` 和 `embeddings`。问答与嵌入通过配置的远程服务完成，论文片段与问题会发送至对应服务，费用按服务提供方的规则计算。

密钥可通过网页密码字段或 CLI 隐藏输入设置。勾选「加密保存密钥」或使用 `--remember-keys`，可由 Windows DPAPI 保存与当前系统用户绑定的密文。未加密保存的密钥仅保留在服务进程中；更换服务地址后需重新提供对应凭据。

```powershell
.\paper.cmd configure --api-base "https://api.provider.com/v1" --chat-model "CHAT_MODEL" --embedding-model "EMBEDDING_MODEL" --enter-keys --remember-keys
```

独立嵌入服务可追加 `--embedding-api-base "https://embedding.provider.com/v1"`。命令中的地址与模型名称应替换为实际配置。

## 论文问答

### 导入论文

点击「导入论文」添加 PDF，或通过 CLI 导入：

```powershell
.\paper.cmd ingest "D:\Papers\paper.pdf" --title "论文标题"
.\paper.cmd list
```

支持可搜索文本的 PDF，页数范围为 1–500 页，网页单份文件上限为 64 MB。扫描件需先完成 OCR。导入后会记录源文件校验值；文件内容变化时，重新导入可建立新的记录。

### 提问与引用

勾选论文，输入问题并选择回答语言。跨论文比较可勾选「引用全部选中论文」。系统检查引用覆盖，必要时扩展检索；结果区分别显示答案、证据不足、引用覆盖不足与服务错误。

引用卡提供原文、论文标题和 PDF 页码。页码采用 PDF 页序号，便于打开对应页面核对方法条件、数值与结论。

```powershell
.\paper.cmd ask "这篇论文的方法在什么条件下成立？" --paper-id PAPER_ID --language zh
.\paper.cmd ask "比较两篇论文的方法" --paper-id PAPER_ID_1 --paper-id PAPER_ID_2 --require-paper-id PAPER_ID_1 --require-paper-id PAPER_ID_2
```

将 `PAPER_ID` 等字段替换为 `list` 返回的论文 ID。`--language` 支持 `auto`、`zh` 和 `en`。Windows PowerShell 5.1 显示中文时，可设置 `[Console]::OutputEncoding = [Text.UTF8Encoding]::new()`。

### 历史与导出

从「问答历史」重新打开结果，使用 Markdown 或 JSON 按钮导出。CLI 对应命令：

```powershell
.\paper.cmd history
.\paper.cmd export ANSWER_ID
```

`ANSWER_ID` 为历史记录中的结果 ID。每次问答、重试和导出使用独立目录，原始结果保存在 `library\paperqa-answers`。

远程嵌入缓存位于 `library\embedding-cache`。文本、源文件或配置变化会产生新的缓存键。服务采用动态模型别名时，使用版本明确的模型有助于复现；需要强制重建缓存时，可停止工作区进程、重命名缓存目录后重新提问。

## 代码转 MCP

### 获取与扫描

在「代码转 MCP」填写公开 GitHub HTTPS 仓库地址并点击「获取代码」，或直接填写已准备好的本机代码目录。版本字段可指定分支、标签或提交。获取阶段下载源码，扫描阶段静态读取函数定义。

```powershell
.\paper.cmd code-fetch REPOSITORY_URL
.\paper.cmd code-scan "REPOSITORY_PATH"
```

扫描结果列出函数 ID、参数、源文件位置、依赖和转换条件。本机源码可用于已自行获取的私有仓库。

### 生成与测试

选择函数，填写 MCP 名称并生成项目。服务器绑定原函数，项目保留源文件 SHA256、上游提交信息和许可文件。

```powershell
.\paper.cmd code-mcp "REPOSITORY_PATH" --function MODULE:FUNCTION --name paper-tools
.\paper.cmd code-test "PROJECT_PATH" --tool TOOL_NAME --arguments-file arguments.json
```

将大写字段替换为扫描和生成结果中的实际值。`arguments.json` 是传给工具的 UTF-8 JSON 对象。网页中选择工具、填写 JSON 参数，再点击「运行测试」。默认使用程序环境；独立依赖环境可填写「Python 路径」或通过 CLI 的 `--python` 指定。

代码转换支持具有明确参数类型的同步 Python 函数及 JSON 可表达的输入。复杂类型、动态依赖和其他语言需要单独适配。工具调用会执行研究代码，应结合源代码审阅与原函数结果进行验证。

生成服务器启用严格 JSON 类型校验：布尔值和数字字符串不能替代数值，整数可用于浮点参数。领域约束由原函数或适配器检查。调用记录包含输入、MCP 响应、原生结果和错误；CLI 调用失败时返回非零退出码。

### 打包与客户端连接

```powershell
.\paper.cmd code-package "PROJECT_PATH"
```

生成包包含 `server.py`、依赖声明、使用指南、来源清单及原源码。按包内 `USAGE.md` 安装独立环境，在支持 stdio 的 MCP 客户端中配置解释器和 `server.py` 的绝对路径。

## 故障处理

| 情况 | 处理方法 |
| --- | --- |
| API 请求失败 | 核对地址、模型名称、账户权限、密钥和嵌入接口；查看保存的任务状态 |
| 问答超时 | 单次检索与问答等待上限为 5 分钟；检查服务状态后重试 |
| PDF 导入失败 | 检查文件是否损坏、加密、仅含扫描图像或超过大小与页数范围 |
| 端口占用 | 为工作区指定其他端口 |
| Windows 路径过长 | 使用较短的程序、环境和数据目录，或指定 `-EnvironmentDir` |
| 工具生成或调用失败 | 查看函数扫描条件、参数类型、领域约束及解释器依赖 |
| 加密密钥不可用 | 在当前 Windows 用户下重新配置服务凭据 |

更多数据与凭据管理说明见 [SECURITY.md](SECURITY.md)。
