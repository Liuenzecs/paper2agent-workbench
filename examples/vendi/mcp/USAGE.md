# Vendi Score 论文代码 → MCP 实测包

论文：**Cousins Of The Vendi Score: A Family Of Similarity-Based Diversity Metrics For Science And Machine Learning**，AISTATS 2024。论文与官方软件入口：https://proceedings.mlr.press/v238/pasarkar24a.html 。这是本项目新增的验证论文，不是声称 2026 年新发表。

原始实现：vertaix/Vendi-Score，固定 commit `ff1dfdbe6356b98a6087540f215b9a9db6db7c11`。包内 `vendor/repo/vendi_score/vendi.py` 是原文件，未经修改。MIT 许可保留在 `vendor/repo/LICENSE`。

## 安装、启动

已测试 Windows x64、Python 3.11。建议解压到较短的目录，例如 `C:\Vendi MCP`。在解压后的 `Vendi-Score-mcp` 目录打开 PowerShell，将第一行替换为自己电脑上已有的 Python 3.11 解释器路径：

```powershell
$Python311 = "C:\Python311\python.exe"
& $Python311 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --use-feature=truststore -r src\requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe src\Vendi-Score_mcp.py
```

最后一条命令启动 stdio MCP 服务，等待客户端连接。关闭该终端即可停止服务。安装需要联网；计算只用 CPU 上的数值库，**不需要 API 密钥、不调用本地模型、不生成向量、不使用 GPU**。没有安装图像、文本或分子特征提取的可选依赖。

服务文件与上游来源从解压目录内相对定位；不需要原开发目录。依赖版本固定在 `src/requirements.txt`。首次安装利用系统证书库，不关闭 TLS 验证。

## 两个工具

### `vendi_score_similarity`

直接调用官方 `vendi.score_K`。输入 `K` 为二维相似度矩阵，行列均对应样本；必须有限、对称、半正定。默认 `normalize=false` 时对角线应为 1；设为 true 时由原函数归一化，原对角线须为正。

`q` 默认 1，支持正数或字符串 `"inf"`。`p` 默认为 null（各样本等权），可传入长度与样本数相同、非负且和为 1 的概率列表。`normalize` 默认 false。`output_dir` 可指定结果目录，每次调用创建独立子目录。

官方 README 示例输入：

```json
{"K": [[1, 0.9, 0], [0.9, 1, 0], [0, 0, 1]], "q": 1}
```

原函数实测分数 `2.1573004833739833`；改变 `q=2` 得到 `1.9480519480519483`，`q="inf"` 得到 `1.5789473684210527`。

### `vendi_score_embeddings`

直接调用官方 `vendi.score_dual`。输入 `X` 为已准备好的数值向量矩阵，行对应样本，列对应特征。每行必须非零；默认 `normalize=true` 会调用原函数进行行归一化。若设为 false，应提供单位向量。`q`、`output_dir` 含义同上；本接口没有 `p` 参数。

这是一组明确标记为合成测试数据的输入，并非任何模型生成的向量：

```json
{"X": [[1, 0], [1, 0], [0, 1]], "q": 1}
```

原函数实测 `1.8898815748423097`；`q=2` 为 `1.7999999999999998`；`q="inf"` 为 `1.5`。

返回内容包括 `score`、样本数、参数和原实现来源，以及独立保存的 JSON 原始结果文件的绝对路径。不会在生产工具中重算一个替代算法以制造结果。

## 客户端连接

为支持 stdio MCP 的客户端配置本机解压目录的解释器与入口。例如，将下面的占位路径替换为自己的真实绝对路径：

```json
{
  "mcpServers": {
    "vendi-score": {
      "command": "C:/Vendi MCP/Vendi-Score-mcp/.venv/Scripts/python.exe",
      "args": ["C:/Vendi MCP/Vendi-Score-mcp/src/Vendi-Score_mcp.py"]
    }
  }
}
```

本交付没有自动注册或修改任何客户端。`server.py` 是同一服务的简短启动入口，可供 Paper2Agent 代码工作台测试使用。

## 验证范围与限制

- 科学计算限定于上述两个官方函数。验证官方 README 矩阵、明确标记的合成向量、改变 `q`、概率权重、归一化，以及无效输入；不声称复现整篇论文的模型或数据集实验。
- 原仓库缺少输入类型注解，Paper2Agent 静态扫描拒绝直接自动封装；此次由代理根据原接口编写薄适配层，然后进行独立科学验证和真实 MCP 调用。原算法没有修改。
- **`q=0` 此包明确不支持**：原始全 1 矩阵实测返回 2，而非理论秩 1，原因是微小正特征值使零阶分数对数值误差敏感。该原始记录留在工作项目内；没有修补上游算法或将异常记录改成成功。非常小的正 `q` 及近似退化的矩阵也须留意数值敏感性。
- 未封装 `score_X`，因为固定版本会忽略它的 `q`；未封装 `intdiv_X`，因为固定版本调用时缺少所需参数。模型特征提取、图像生成、训练和论文全量实验均未执行。
- 相似度核的选择和向量的科学意义由调用者确定。这组测试数据只展示计算行为，不证明真实模型的准确率或回答质量。
- 开发报告、测试数据、原始参考结果及失败记录在工作项目内保留，未装进默认运行 ZIP。
