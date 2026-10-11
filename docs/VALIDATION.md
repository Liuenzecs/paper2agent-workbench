# 验证范围

## 软件回归

自动化测试覆盖论文库隔离、PDF 导入、API 配置与凭据保护、引用定位、问答状态分类、代码扫描、原函数绑定、严格输入校验和结果存储。

离线测试使用合成文件、代码夹具和模拟 API 响应，验证软件接口与状态处理。论文答案的事实准确性需要结合对应原文单独评估。

## MCP 验证

科学回归将实际 MCP 调用与原函数直接执行进行对照，包括正常输入、默认参数、数值边界和错误输入。研究代码的统计假设与适用领域由上游论文、接口说明及来源记录定义。

开发仓库保留 Vendi Score 和 Mann–Kendall 的数值回归资料。验证覆盖所选函数及输入范围，完整实验复现需要另行设计。

## 发行验收

发行检查包括独立目录安装、依赖完整性、网页与 CLI 操作、MCP 调用、文件校验值和包内容审查。论文问答的远程集成验证与离线软件测试分别记录。

[GitHub Actions](https://github.com/Liuenzecs/paper2agent-workbench/actions/workflows/verify.yml) 提供提交级检查状态。[最新发行版](https://github.com/Liuenzecs/paper2agent-workbench/releases/latest) 附带包内容清单、SHA256 和验证记录，可用于核对来源与验证范围。
