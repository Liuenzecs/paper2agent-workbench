# Vendi：无需论文、模型或 API 密钥的真实 MCP 演示

本示例使用 AISTATS 2024 论文 [Cousins Of The Vendi Score](https://proceedings.mlr.press/v238/pasarkar24a.html) 的官方 Vendi-Score 数值核心。原函数未经修改，薄适配层已经独立审阅和真实 MCP 验收。向量是明确给定的合成数值，没有使用模型生成。

从主程序根目录安装独立 CPU 环境，并运行演示：

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\vendi\install.ps1 -Python "C:\你的Python311目录\python.exe"
.\paper.cmd --data-dir "D:\我的研究资料\Paper2Agent" example-vendi --python ".\examples\vendi\mcp\.venv\Scripts\python.exe"
```

也可省略 `--data-dir`，使用自己的默认个人工作区。每次演示创建新的 `code-projects/vendi-<唯一ID>`，校验源文件哈希，实际调用两个工具；四项数值与保存的原函数结果比较，第五项验证 q=0 被明确拒绝。全部原始 MCP 响应、警告、输入与输出文件保存在个人工作区，不覆盖前一次演示。

| 输入 | 原函数期望值 |
| --- | ---: |
| 官方 README 相似度矩阵，q=1 | 2.1573004833739833 |
| 同一矩阵，q=2 | 1.9480519480519483 |
| `[[1,0],[1,0],[0,1]]`，q=1 | 1.8898815748423097 |
| 同一向量，q=2 | 1.7999999999999998 |

直接接入其他 MCP 客户端及完整参数说明见 [mcp/USAGE.md](mcp/USAGE.md)。独立示例仓库：[Liuenzecs/vendi-score-mcp](https://github.com/Liuenzecs/vendi-score-mcp)。

范围为两个 CPU 数值函数。q=0 的原实现受浮点误差影响，因此封装明确拒绝；完整原始异常数值仍在参考记录中。非常小的正 q 也需注意数值敏感性。本示例未执行训练、特征提取或论文全量实验。

`mcp/` 保持其 MIT 许可、上游 Princeton Vertaix Lab MIT 通知及已经公开的路径脱敏验证记录。主程序的 AGPL 许可不改变这些独立示例的原许可。
