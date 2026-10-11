# Vendi Score MCP 使用指南

## 安装与启动

运行环境为 Windows x64、Python 3.11。在程序目录创建环境并安装依赖：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --use-feature=truststore -r src\requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe src\Vendi-Score_mcp.py
```

服务使用 stdio，计算依赖已提供的数值输入。关闭服务终端可停止进程。源码和依赖按项目目录相对定位。

## 参数

### 相似度评分

`vendi_score_similarity` 调用 `vendi.score_K`。

| 参数 | 内容 |
| --- | --- |
| `K` | 有限、对称、半正定的二维相似度矩阵 |
| `q` | 正数或字符串 `"inf"`，默认 `1` |
| `p` | 非负、总和为 1 的样本概率列表；默认等权 |
| `normalize` | 默认 `false`；关闭时对角线为 1，开启时原对角线须为正 |
| `output_dir` | 可选结果目录，每次调用建立独立子目录 |

### 向量评分

`vendi_score_embeddings` 调用 `vendi.score_dual`。`X` 为二维数值矩阵，行表示样本，列表示特征，每行须非零。`normalize` 默认 `true`；设为 `false` 时提供单位向量。`q` 和 `output_dir` 含义与相似度评分相同。

两个工具返回分数、参数、来源信息和结果文件路径。核矩阵和向量的定义应与研究问题匹配。

## 客户端配置

在支持 stdio MCP 的客户端中配置解释器与服务入口，将以下路径替换为实际绝对路径：

```json
{
  "mcpServers": {
    "vendi-score": {
      "command": "C:/Vendi MCP/.venv/Scripts/python.exe",
      "args": ["C:/Vendi MCP/src/Vendi-Score_mcp.py"]
    }
  }
}
```

`server.py` 也可作为同一服务的启动入口。

## 数值条件与验证

`q=0` 对浮点特征值误差敏感，接口采用正阶评分。非常小的正 `q` 和近似退化的矩阵需结合数值精度评估。

原函数直接执行结果见 `notebooks/vendi_reference/reference.json`，MCP 对照检查见 `reports/verification-vendi_metrics.json`。验证采用固定矩阵和合成向量，覆盖选定的两个函数及输入范围。

实现固定于 Vendi-Score 提交 `ff1dfdbe6356b98a6087540f215b9a9db6db7c11`。原科学源码及 MIT 许可保存在 `vendor/repo`，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。
