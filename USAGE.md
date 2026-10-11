# Paper2Agent Workbench 0.3.2

把自己的论文变成可核对原文的问答库，并把论文关联的 Python 代码做成可测试的 MCP 工具。本机网页与 CLI 共用同一个个人工作区；问答及嵌入只使用远程 API。这个版本不调用本地模型或 GPU。

## 安装、打开与关闭

已测试环境为 Windows x64、Python 3.11。首次安装需要联网获取依赖；源码下载功能另需 Git。建议解压到较短的目录，例如 `C:\Paper2Agent`，可包含空格；过深目录可能触发 Windows 路径长度限制。在该目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

如果没有 `py -3.11`，可指定已安装的 Python 3.11：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Python "C:\你的Python目录\python.exe"
```

安装完双击 `Start-Web.cmd`。网页默认地址为 `http://127.0.0.1:8765`，服务在后台运行。双击 `Stop-Web.cmd` 关闭后台服务；关闭网页标签页不会停止服务。

每个人的默认数据目录为 `%LOCALAPPDATA%\Paper2AgentLab`，论文、API 配置、缓存、问答原始结果和 MCP 项目都存放在这里，与程序目录分离。可同时给不同任务配置独立目录和端口：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-web.ps1 -DataDir "D:\我的研究资料\Paper2Agent" -Port 8767
powershell -ExecutionPolicy Bypass -File .\stop-web.ps1 -DataDir "D:\我的研究资料\Paper2Agent"
.\paper.cmd --data-dir "D:\我的研究资料\Paper2Agent" status
```

## 配置自己的远程 API

首次打开「API 设置」填写以下信息：问答服务 HTTPS 基础地址、问答模型名、嵌入模型名、两类 API 密钥。嵌入服务与问答服务不同时，还要填独立的嵌入 API 地址。模型名填写服务支持的实际名称，不加 `openai/` 前缀。本版要求 OpenAI 兼容的 chat completions 和 embeddings 接口；默认配置为待填写模板，必须设置自己的服务。`configs/providers/volcengine-agent-plan.example.json` 保留此前实测的火山引擎 Agent Plan 示例；示例没有密钥，不保证其它服务已经兼容。

密钥默认只留在服务进程内，重启后需重新填写。勾选「在此 Windows 用户下加密保存」会用 Windows DPAPI 保存密文，只能由同一 Windows 用户解密。密钥字段再次打开时为空，不会回显。更换服务地址后，需重新提供对应密钥，程序不把原服务密钥发送给新服务。此公开版不会把已有全局密钥自动绑定到未配置的模板。请通过网页密钥字段或 CLI 的 `--enter-keys` 设置当前工作区凭据。不要把个人数据目录、加密密钥文件或环境变量一起分享给别人。

CLI 可使用隐藏输入配置密钥：

```powershell
.\paper.cmd configure --api-base "https://服务地址/v1" --chat-model "问答模型名" --embedding-model "嵌入模型名" --enter-keys --remember-keys
```

请求会把所选论文的文本发送到你配置的远程服务，并产生该服务的费用。当前服务返回动态模型别名时，无法精确计算费用，也无法保证下次请求使用同一组模型权重。

## 论文问答

1. 导入可搜索文本 PDF，勾选要使用的论文，再输入问题。当前支持 1–500 页、网页单份不超过 64 MB 的 PDF；扫描件需先自行 OCR。
2. 跨论文比较时，勾选要求引用所选论文。系统检查实际引用覆盖；缺少指定论文时最多扩展检索再答一次，仍缺失就不显示未经充分支持的结论，两次原始结果都保留。
3. 结果卡显示引用原文、论文标题和 PDF 页码，可打开对应页。页码是 PDF 页序号，未必等于论文印刷页码。
4. 导出 Markdown 或 JSON。每次导出及问答都创建独立目录，保留以前的记录。服务错误单独标为错误，不记作缺证据拒答。

CLI 用法（Windows PowerShell 5.1 先设置下列 UTF-8 显示选项，避免中文路径输出乱码）：

```powershell
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
.\paper.cmd ingest "D:\论文\paper.pdf" --title "我的论文" --url "https://论文来源"
.\paper.cmd list
.\paper.cmd ask "这篇论文的方法在什么条件下成立？" --paper-id <list输出的paper_id> --language zh
.\paper.cmd ask "比较两篇论文" --paper-id <ID1> --paper-id <ID2> --require-paper-id <ID1> --require-paper-id <ID2>
.\paper.cmd history
.\paper.cmd export <历史中的answer_id>
```

尖括号表示需要替换的值，不能直接照抄。CLI 的 `ask` 输出保留较详细的审计及原始结果路径；网页展示过滤后的答案。每个原始结果保存于个人目录 `library\paperqa-answers\<唯一ID>`。

引用定位成功表示能找到对应原文，不代表每句话已经通过专家判断。本版的验收使用固定版本论文和指定问题，实际研究中仍应核对重要数字、公式、条件及附加推论。

远程嵌入缓存在 `library\embedding-cache`，配置、文本或源文件变化会产生新缓存键。若服务在相同模型别名下更换权重，程序不能自动识别。可使用版本明确的嵌入模型；需强制重建时，先关闭网页服务及其它问答进程，将此文件夹改名备份后重新问答。不要删除 `paperqa-answers`，其中是历史原始结果。

## 论文代码 → 测试 → MCP

在「代码 → MCP」中填论文公开 GitHub 仓库链接，点「下载源码」，或直接选择本机已有仓库。下载只获取源码，不安装依赖、不执行仓库代码，不下载 Git LFS 模型内容。当前仅提供公开 GitHub HTTPS 仓库下载，私有仓库请自行准备本机源码。

扫描会列出原函数、参数、来源位置和不能自动转换的原因。选择适合的函数生成 MCP 项目：服务器直接绑定保留的原函数，不自行编造论文算法。项目记录上游 commit（存在时）、原文件 SHA256、所选参数假设，并保留许可文件。

当前支持公开同步 Python 函数及可表达为 JSON 的基础参数。类方法、异步函数、复杂未注明类型、训练流水线或模型加载通常需要人工适配。候选函数能生成包，不代表整个仓库已复现。没有可用代码的论文需要另行实现和比对，当前不承诺自动写出等价算法。

明确选择工具、填写 JSON 参数后，点「运行原函数测试」，才会通过真实 MCP 调用原代码。默认使用程序的 Python 环境；其它依赖请先审阅并安装到独立环境，再在测试表单指定该解释器。程序不自动安装研究代码依赖。涉及本地模型或加速框架的代码会阻止执行。模型保护不是通用安全沙箱，仅测试可信且已审阅的代码。

官方 HumanEval 示例只测试 `estimate_pass_at_k` 统计函数，不运行生成程序或 `check_correctness`：

```powershell
.\paper.cmd code-fetch https://github.com/openai/human-eval
.\paper.cmd code-scan "<下载结果中的repository_path>"
.\paper.cmd code-mcp "<repository_path>" --function human_eval.evaluation:estimate_pass_at_k --name humaneval-statistics
Set-Content -LiteralPath .\arguments.json -Encoding UTF8 -Value '{"num_samples":10,"num_correct":[0,1,5,10],"k":1}'
.\paper.cmd code-test "<生成结果中的project_path>" --tool human_eval_evaluation__estimate_pass_at_k --arguments-file .\arguments.json
.\paper.cmd code-package "<project_path>"
```

该函数的论文定义域是 `0 ≤ c ≤ n`、`1 ≤ k ≤ n`，每项正确样本数应与对应总样本数匹配，数组长度一致。上游函数不完整校验这些约束，越界结果不能当作有效估计。该函数使用 NumPy，本程序锁定环境已包含它。其它工具按生成项目中的 `requirements.txt` 人工准备依赖；需要本地模型的工具不在本试用版执行范围。测试报告保留输入、实际 MCP 响应、原生结果与错误，不能仅凭进程启动成功认定科研结果正确。

生成的 MCP 包内 `USAGE.md` 给出独立安装和启动方法。可以把通过测试的包给 MCP 客户端使用，启动入口为包内 `server.py`。本程序不会自动修改你的客户端注册配置。

## 故障处理与试用范围

API 错误：检查账户权限、模型名、服务基础地址以及嵌入接口是否可用。单次检索与远程问答设置 5 分钟总等待上限；超时归为服务错误，不算缺证据拒答。原始结果或失败任务保留；错误和模型拒答分别显示。

端口被占用：给自己的工作区指定其它端口。停止服务仅停止与记录路径相符的 Paper2Agent 进程。

论文导入失败：检查是否密码保护、扫描件、超过页数上限或损坏。源 PDF 后续被改动时校验会失败，需重新导入。

源码不能测试：查看函数限制与依赖。本版针对 CPU 可执行、可追溯的 Python 工具，不覆盖所有语言或完整训练复现。论文、API 账户、研究仓库均由使用者自行提供；程序安装需要网络，问答需要可用的远程 API。

## Vendi 示例

此仓库包含已验收的 Vendi MCP 源码、固定依赖、MIT 许可及验证记录，见 [examples/vendi/README.md](examples/vendi/README.md)。安装该示例的独立 CPU 环境后，运行 `paper.cmd example-vendi --python <示例环境解释器>`，会在自己的个人工作区复制新的项目、实际调用两个工具并保存原始结果。此演示不需要论文或 API 密钥。

主程序 AGPL-3.0-only；Vendi 示例保持其 MIT 许可。源码和下载 ZIP 均不含个人数据。

较深目录安装遇到 Windows 文件路径过长时，可给 install.ps1 追加 -EnvironmentDir 指定短的可写目录，例如 C:\P2A-env\workbench。安装成功后本机 runtime-path.txt 记录实际解释器，paper.cmd 与网页启动器均自动读取。此文件只包含本机路径，不含密钥，不进入开源仓库。Vendi 安装器支持同名选项。

## 非 AI 统计示例与严格输入

见 examples/mannkendall/README.md，安装独立统计环境后执行 `paper.cmd example-mannkendall --python <统计环境解释器>`，或在网页“论文代码”页运行非 AI 示例。两个入口均生成新 MCP，核对五组全部九字段结果并拒绝六组错误输入，保留每次原始记录。

所有新生成 MCP 默认显式开启严格 JSON 类型校验：布尔值和数字字符串不能冒充数字；合法整数可用于浮点参数。领域条件仍由可信原函数或适配器检查。CLI code-test 在调用失败时返回非零退出码，并保留失败记录；网页显示失败。旧 MCP 项目需重新生成，原有项目和结果不会自动改写。代码复测无需调用任何模型。
