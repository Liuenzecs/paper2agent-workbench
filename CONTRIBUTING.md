# 贡献指南

欢迎通过 Issue 报告问题、讨论功能，通过 Pull Request 提交改进。

## 开发环境

使用 Python 3.11，在仓库根目录创建环境并安装开发依赖：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --use-feature=truststore -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
```

`requirements-dev.txt` 包含锁定版本的运行依赖和测试依赖。离线测试使用合成 PDF、代码夹具及模拟服务响应。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `src/` | 论文库、远程服务接入、CLI、网页后端和 MCP 生成逻辑 |
| `web/` | 本机网页界面 |
| `configs/` | 服务配置模板 |
| `tests/` | 软件回归测试 |
| `scripts/` | 发行包构建与科学回归工具 |
| `docs/` | 验证范围说明 |

科学回归资料与独立环境配置保存在开发仓库中，运行流程见 [CI 配置](.github/workflows/verify.yml)。

`tests/test_code_independent.py` 的独立集成检查使用 `P2A_INDEPENDENT_RUN` 指定新的结果目录。SALib 科学对照另需 `P2A_SALIB_REPOSITORY` 与 `P2A_SALIB_CONTRACTS`，分别指向固定版本源码及经过审核的接口契约；需在检查环境中安装其科学依赖。

## 提交流程

1. 在 Issue 或 Pull Request 中说明问题、预期行为及适用场景。
2. 将修改集中于相关功能，并同步操作文档。
3. 运行与改动相关的检查，提供可复现的输入与结果。
4. 在 Pull Request 中概述改动和验证情况。

问答功能应保留原文引用、结论条件和量化对象，区分证据不足、引用覆盖不足与服务错误。代码工具应绑定已有原函数，保留来源与许可，并通过原函数对照验证数值行为。

工作区隔离、凭据保护、严格输入校验和独立结果目录属于公共接口约定。测试输入使用可公开的数据；诊断信息应脱敏，生成环境和个人工作区存放在源代码目录之外。

## 文档与许可

文档面向使用者说明功能、操作和适用范围。新增命令、配置项或界面行为应在使用指南中说明。

主程序与文档采用 AGPL-3.0-only。第三方源码保留各自的许可与版权声明，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
