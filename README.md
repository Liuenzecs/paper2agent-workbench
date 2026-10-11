# Paper2Agent Workbench

**本机网页 + CLI：每个人用自己的论文库和远程 API，读论文、核对引用，并把公开研究代码接成 MCP。**

这是独立开发的通用论文工作台，领域不限于机器学习/AI。受 [Paper2Agent](https://github.com/jmiao24/Paper2Agent) 启发，论文证据问答基于 [PaperQA](https://github.com/Future-House/paper-qa)。当前发行版面向 Windows x64 / Python 3.11，本地模型和 GPU 执行被禁用。

## 能做什么

- 导入自己的可搜索文本 PDF，选择问答范围、跨论文比较，查看引用原文与 PDF 页码。
- 配置自己的 OpenAI 兼容远程问答及嵌入 API；两种服务可独立配置。密钥不回显，可选 Windows 用户绑定的加密存储。
- 区分缺证据拒答、引用覆盖不足及服务故障；原始问答、重试记录与导出按唯一目录保留。
- 获取公开 GitHub 源码，静态扫描适合的 Python 函数，生成绑定原函数的 MCP 项目，明确选择后进行真实测试与打包。
- 运行内置 [Vendi 示例](examples/vendi/README.md)，验证“论文代码 → 原函数 → MCP → 实际调用”的完整链路，无需模型或 API 密钥。

## 安装与打开

下载 [Releases](https://github.com/Liuenzecs/paper2agent-workbench/releases) 的完整 ZIP 并解压，或克隆本仓库。首次安装需联网，源码下载另需 Git。在根目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Python "C:\你的Python311目录\python.exe"
```

双击 `Start-Web.cmd` 打开本机网页；默认地址 `http://127.0.0.1:8765`。双击 `Stop-Web.cmd` 停止服务。关闭浏览器标签页不会停止后台服务。

CLI 入口：

```powershell
.\paper.cmd status
.\paper.cmd ingest "D:\自己的论文\paper.pdf" --title "论文标题"
.\paper.cmd list
.\paper.cmd configure --api-base "https://自己的服务/v1" --chat-model "问答模型" --embedding-model "嵌入模型" --enter-keys --remember-keys
```

随后用 `paper.cmd ask` 提问，或在网页勾选论文后提问。完整命令、分离的嵌入 API 配置、引用核对、故障处理见 [USAGE.md](USAGE.md)。默认服务和模型是待填写模板；保存配置不会发起模型调用。

## 每个人独立使用

默认个人目录为 `%LOCALAPPDATA%\Paper2AgentLab`。论文、设置、问答原始结果、缓存、MCP 项目均写入个人目录，与程序源文件分离。可给不同课题指定不同的 `--data-dir`，网页启动对应 `-DataDir`；避免并发进程共用同一目录。

远程问答会向你配置的服务发送选中论文的文本片段和问题，并产生该服务的费用。不要把个人数据目录或 API 密钥提交到仓库。此发行版没有论文、个人数据、密钥、模型权重或可用的共享 API 账户。

## 真实 Vendi 演示

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\vendi\install.ps1 -Python "C:\你的Python311目录\python.exe"
.\paper.cmd example-vendi --python ".\examples\vendi\mcp\.venv\Scripts\python.exe"
```

演示会在自己的工作区创建新项目，实际调用 `vendi_score_similarity` 和 `vendi_score_embeddings`，对照已保存原函数数值，验证 q=0 拒绝并保留每次原始记录。原函数期望值为 2.1573004833739833、1.9480519480519483、1.8898815748423097 和 1.7999999999999998。

Vendi 官方函数缺少类型注解，现有静态扫描会拒绝直接自动封装；内置示例提供经过审阅的适配层。一般仓库仍受类型、依赖和可验证输入条件限制。当前不承诺一键复现任意论文。

## 验证与边界

本次发行的验收记录见 [docs/VALIDATION.md](docs/VALIDATION.md)。测试使用合成 PDF、静态代码夹具和假的远程响应；这类离线测试验证软件行为，不证明论文答案事实正确。此前真实远程问答的独立原文审查包含成功、缺证据拒答及一次服务超时；公开版只分享审核摘要，不分享论文或私有 API 原始响应。

代码接入当前支持部分同步 Python 函数及 JSON 可表达的参数；模型加载、训练、其他语言和完整实验往往需要另行适配。模型保护不是通用安全沙箱，只运行可信且审阅过的研究代码。扫描 PDF 需先自行 OCR；引用定位成功也仍需核对数字、公式及成立条件。

## 开源许可

主程序和文档：**AGPL-3.0-only**，见 [LICENSE](LICENSE)。PyMuPDF 使用其 AGPL 开源许可路径。独立 Vendi 示例及 HumanEval 测试来源保留 MIT 许可和上游通知；见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

开发与贡献见 [CONTRIBUTING.md](CONTRIBUTING.md)，本机使用与密钥说明见 [SECURITY.md](SECURITY.md)。
