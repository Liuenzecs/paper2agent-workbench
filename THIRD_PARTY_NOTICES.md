# Third-party software and source

The workbench is an independent project inspired by [Paper2Agent](https://github.com/jmiao24/Paper2Agent). It uses [PaperQA](https://github.com/Future-House/paper-qa), LMI, LiteLLM, FastMCP, PyMuPDF and other installed dependencies. Exact runtime versions are pinned in requirements-lock.txt; dependency binaries are not bundled.

PyMuPDF is available under GNU AGPL v3 or a commercial license: https://pymupdf.io/licensing . This open-source workbench uses the AGPL path and publishes its corresponding source under AGPL-3.0-only. See LICENSE and NOTICE. Installed dependencies retain their own notices; this statement does not relicense them.

`examples/vendi/mcp` is the separately MIT-licensed Vendi MCP project. Its unmodified scientific implementation is from Princeton Vertaix Lab's Vendi-Score, commit `ff1dfdbe6356b98a6087540f215b9a9db6db7c11`; source and license notices are retained there. The Vendi example uses provided numeric vectors and does not generate embeddings.

`tests/fixtures/humaneval` contains only OpenAI HumanEval evaluation source and its MIT license, for static scan regression testing. No HumanEval dataset, generated program execution or personal papers are bundled.

No research PDFs, model weights, API keys, individual libraries, or paid service access are included. Users supply their own papers and remote services.
