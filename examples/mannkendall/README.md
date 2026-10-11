# pyMannKendall：非 AI 统计示例

[JOSS 2019 论文](https://joss.theoj.org/papers/10.21105/joss.01556)研究有序时间序列的非参数趋势检验，可用于水文、气候等领域。保留[官方源码 v1.4.3](https://github.com/mmhs013/pyMannKendall/tree/1ff32de4c4f4b50e1d922763ae88b3cb677c52b0)及 MIT 原许可，未修改统计公式。2019 论文归档版本是 v1.1，本例使用后续维护版。来源和文件哈希见 [provenance.json](provenance.json)。科学源码保留 MIT；新适配器和工作台采用根目录 AGPL-3.0-only。

先安装工作台，再安装独立 CPU 统计环境：

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\mannkendall\install.ps1 -Python "C:\你的Python311目录\python.exe" -EnvironmentDir "C:\P2A-env\mannkendall"
.\paper.cmd example-mannkendall --python "C:\P2A-env\mannkendall\Scripts\python.exe"
```

网页“论文代码”页的“运行非 AI 示例”填同一个解释器即可。两种入口均通过通用生成器创建新 MCP，真实调用两个工具。五项成功输入对照全部九个原生字段，六项错误输入明确拒绝。每次生成、输入、响应或失败与汇总均保存在个人 `code-projects` 中；重复运行不覆盖。不需要论文、API 密钥或模型。

也可扫描 `examples/mannkendall/source`，开放 `mannkendall_adapter:original_trend` 和 `mannkendall_adapter:seasonal_trend`。`{"x":[1,null,3]}` 的原始斜率为 1，`null` 保留时间位置，不能删成 `[1,3]`。

新生成 MCP 显式开启严格 JSON 类型校验，不依赖环境变量。`true`、`"1"` 不会被当作数字；整数仍可用于浮点参数。适配器另检查 `0 < alpha < 1`、正整数周期和有效观测配对。旧项目需重新生成，历史包和结果不改写。

原始检验斜率是每个观测位置，季节检验是每个完整周期。原始检验不修正自相关或季节性，季节检验也未添加自相关修正。参数、假设和九个字段含义见 [source/README.md](source/README.md)。仅验收两个工具，不声称整篇论文或所有方法已复现。

[reference.json](reference.json) 保存直接官方 API 参考：输入为合成数组，包含缺失位置、周期变化、反向序列与常量序列，并非实测气候数据，容差 `1e-12`。固定依赖见 `requirements-lock.txt`。不分发论文、个人数据或密钥。
