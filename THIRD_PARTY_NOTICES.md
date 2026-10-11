# Third-party software and source

The workbench is an independent project inspired by [Paper2Agent](https://github.com/jmiao24/Paper2Agent). It uses [PaperQA](https://github.com/Future-House/paper-qa), LMI, LiteLLM, FastMCP, PyMuPDF and other installed dependencies. Exact runtime versions are pinned in requirements-lock.txt; dependency binaries are not bundled.

PyMuPDF is available under GNU AGPL v3 or a commercial license: https://pymupdf.io/licensing . This open-source workbench uses the AGPL path and publishes its corresponding source under AGPL-3.0-only. See LICENSE and NOTICE. Installed dependencies retain their own notices; this statement does not relicense them.

`examples/vendi/mcp` is the separately MIT-licensed Vendi MCP project. Its unmodified scientific implementation is from Princeton Vertaix Lab's Vendi-Score, commit `ff1dfdbe6356b98a6087540f215b9a9db6db7c11`; source and license notices are retained there. The Vendi example uses provided numeric vectors and does not generate embeddings.

`tests/fixtures/humaneval` contains only OpenAI HumanEval evaluation source and its MIT license, for static scan regression testing. No HumanEval dataset, generated program execution or personal papers are bundled.

No research PDFs, model weights, API keys, individual libraries, or paid service access are included. Users supply their own papers and remote services.

## pyMannKendall 非 AI 示例

examples/mannkendall/source/pymannkendall.py 来自 mmhs013/pyMannKendall v1.4.3，提交 1ff32de4c4f4b50e1d922763ae88b3cb677c52b0，科学源码未修改；MIT 原许可保存在 source/LICENSE。新类型适配器使用主程序 AGPL-3.0-only。示例只含明确的合成数组，不含论文 PDF 或私人观测数据。
