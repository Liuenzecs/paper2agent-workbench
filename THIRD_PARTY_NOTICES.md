# 第三方组件与许可

## 运行依赖

Paper2Agent 使用 PaperQA、LMI、LiteLLM、FastMCP、PyMuPDF 等组件。运行版本锁定于 `requirements-lock.txt`，各组件适用其自身许可。

PyMuPDF 提供 AGPL 与商业许可路径，见 [官方许可说明](https://pymupdf.io/licensing)。本项目采用 AGPL-3.0-only，并公开对应源码；许可正文见 [LICENSE](LICENSE)。

## 研究代码来源

| 组件 | 来源与固定版本 | 许可与保留位置 |
| --- | --- | --- |
| Vendi Score | Princeton Vertaix Lab，[vertaix/Vendi-Score](https://github.com/vertaix/Vendi-Score)，提交 `ff1dfdbe6356b98a6087540f215b9a9db6db7c11` | MIT；原版权声明保存在 `tests/research_fixtures/vendi/mcp/vendor/repo/LICENSE` |
| pyMannKendall | [mmhs013/pyMannKendall](https://github.com/mmhs013/pyMannKendall)，v1.4.3，提交 `1ff32de4c4f4b50e1d922763ae88b3cb677c52b0` | 科学源码 MIT，见 `tests/research_fixtures/mannkendall/source/LICENSE`；类型适配器 AGPL-3.0-only |
| HumanEval 评估函数 | [OpenAI HumanEval](https://github.com/openai/human-eval) | MIT，见 `tests/fixtures/humaneval/LICENSE` |

研究代码用于开发回归验证，原科学实现及许可保留在对应目录。Vendi MCP 适配器与其独立文档采用 MIT；工作台和 pyMannKendall 类型适配器采用 AGPL-3.0-only。

## 项目致谢

项目受到 [Paper2Agent](https://github.com/jmiao24/Paper2Agent) 启发，论文证据问答基于 [PaperQA](https://github.com/Future-House/paper-qa)。第三方组件的引用与许可不改变其原有版权归属。
