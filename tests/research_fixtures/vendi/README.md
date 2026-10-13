# Vendi Score 数值回归

此目录保存 Vendi Score 的 MCP 适配器、依赖配置与科学回归资料。来源为 AISTATS 2024 论文 [Cousins Of The Vendi Score](https://proceedings.mlr.press/v238/pasarkar24a.html) 的官方实现，固定提交见 [第三方说明](mcp/THIRD_PARTY_NOTICES.md)。

## 环境与运行

在主仓库根目录安装独立环境并运行回归检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\tests\research_fixtures\vendi\install.ps1 -Python "C:\Python311\python.exe"
.\.venv\Scripts\python.exe -X utf8 tests\support\verify_references.py vendi --data-dir "C:\P2A-data\vendi" --python ".\tests\research_fixtures\vendi\mcp\.venv\Scripts\python.exe"
```

主程序的开发环境由根目录 `install.ps1` 或 `requirements-dev.txt` 安装。回归脚本在指定工作区创建独立项目，保存每次输入、MCP 响应和对照记录。

## 验证内容

检查包括相似度矩阵与数值向量的四项原函数对照，以及 `q=0` 的预期拒绝。输入采用明确的合成数据，参考结果见 `mcp/notebooks/vendi_reference/reference.json`。

`q=0` 对微小正特征值敏感，接口采用正 `q` 或字符串 `"inf"`。两种工具的参数、数值条件与客户端连接方法见 [mcp/USAGE.md](mcp/USAGE.md)。

## 许可

Vendi MCP 适配器及文档采用 MIT，原科学源码保留 Princeton Vertaix Lab 的 MIT 版权声明。独立项目见 [vendi-score-mcp](https://github.com/Liuenzecs/vendi-score-mcp)。
