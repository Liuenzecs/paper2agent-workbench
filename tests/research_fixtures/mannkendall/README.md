# Mann–Kendall 数值回归

此目录保存 pyMannKendall 趋势检验的类型适配器、依赖配置和科学回归资料。论文为 [pyMannKendall，JOSS 2019](https://joss.theoj.org/papers/10.21105/joss.01556)，源码固定为 v1.4.3；版本、提交和原文件校验值见 [provenance.json](provenance.json)。

## 环境与运行

在主仓库根目录安装工作台环境，再安装统计依赖并运行检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\tests\research_fixtures\mannkendall\install.ps1 -Python "C:\Python311\python.exe" -EnvironmentDir "C:\P2A-env\mannkendall"
.\.venv\Scripts\python.exe -X utf8 tests\support\verify_references.py mannkendall --data-dir "C:\P2A-data\mannkendall" --python "C:\P2A-env\mannkendall\Scripts\python.exe"
```

验证脚本通过通用 MCP 生成器创建新项目，实际调用 `original_trend` 和 `seasonal_trend`。五项正常输入对照全部九个返回字段，六项无效输入验证拒绝行为。输入、响应、失败信息和汇总保存于指定工作区的独立目录。

## 参数与结果

可在代码工作台扫描 `tests/research_fixtures/mannkendall/source`，选择 `mannkendall_adapter:original_trend` 和 `mannkendall_adapter:seasonal_trend`。

缺失观测使用 `null` 保留原时间位置。`alpha` 取值范围为 `0 < alpha < 1`，季节周期为正整数。严格类型校验拒绝布尔值和数字字符串。参数条件、统计假设与九个字段含义见 [source/README.md](source/README.md)。

[reference.json](reference.json) 使用合成数组直接调用原 API，覆盖缺失位置、周期变化、反向序列与常量序列，对照容差为 `1e-12`。依赖版本锁定于 `requirements-lock.txt`。

## 来源与许可

论文归档版本为 v1.1，统计库固定版本为后续维护版 v1.4.3。原始趋势检验和季节趋势检验的科学实现采用 MIT；类型适配器与工作台采用 AGPL-3.0-only。许可正文保存在 `source/LICENSE` 和 `source/COPYING`。
