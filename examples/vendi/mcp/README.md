# Vendi Score MCP

Vendi Score MCP 提供相似度核和数值向量的多样性评分工具，通过 stdio 与 MCP 客户端连接。工具直接调用固定版本的 Vendi Score 科学实现。

## 工具

| 工具 | 原函数 | 输入 |
| --- | --- | --- |
| `vendi_score_similarity` | `vendi.score_K` | 有限、对称、半正定的相似度矩阵 |
| `vendi_score_embeddings` | `vendi.score_dual` | 已准备好的非零数值向量 |

调用结果包含分数、输入参数、来源信息与独立 JSON 记录路径。完整参数与客户端配置见 [USAGE.md](USAGE.md)。

## 安装

运行环境为 Windows x64、Python 3.11。在此目录打开 PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --use-feature=truststore -r src\requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe src\Vendi-Score_mcp.py
```

最后一条命令启动 MCP 服务，等待客户端连接。依赖版本锁定于 `src/requirements.txt`。

## 验证

数值回归覆盖相似度核、合成向量、概率权重、归一化和无效输入。数据与记录见 [原函数参考](notebooks/vendi_reference/reference.json) 和 [科学验证报告](reports/verification-vendi_metrics.json)。发行与文件来源记录见 [reports/publication-provenance.json](reports/publication-provenance.json)。

```powershell
.\.venv\Scripts\python.exe -m pip install --use-feature=truststore -r requirements-dev.txt
.\.venv\Scripts\python.exe -X utf8 -m pytest tests\code\vendi_metrics -q
```

`q` 支持正数及字符串 `"inf"`；零阶评分对微小特征值误差敏感，接口拒绝 `q=0`。核矩阵与向量的科学含义应结合应用场景选择。

## 来源与许可

论文：[Cousins Of The Vendi Score](https://proceedings.mlr.press/v238/pasarkar24a.html)，AISTATS 2024。实现来源：[vertaix/Vendi-Score](https://github.com/vertaix/Vendi-Score)，固定提交 `ff1dfdbe6356b98a6087540f215b9a9db6db7c11`。

适配器与文档采用 MIT；上游源码保留原 MIT 版权声明。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
