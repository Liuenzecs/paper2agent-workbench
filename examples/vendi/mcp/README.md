# Vendi Score MCP — verified CPU tools

将公开 AI/机器学习论文的数值核心做成可调用 MCP：获取官方代码 → 直接测试原函数 → 薄封装 → 真实调用 → 解压安装验收。

A tested, CPU-only MCP adapter for the official Vendi Score implementation. Two tools compute diversity from a supplied similarity kernel or supplied numeric vectors. No local models, GPU, embedding generation, or API credentials are needed.

Paper: [Cousins Of The Vendi Score: A Family Of Similarity-Based Diversity Metrics For Science And Machine Learning](https://proceedings.mlr.press/v238/pasarkar24a.html), AISTATS 2024. This is a newly validated example, not a claim of publication in 2026.
Official code: [vertaix/Vendi-Score](https://github.com/vertaix/Vendi-Score), pinned to `ff1dfdbe6356b98a6087540f215b9a9db6db7c11`.

## Install and connect

Tested on **Windows x64 / Python 3.11**. Download `Vendi-Score-mcp.zip` from [Releases](https://github.com/Liuenzecs/vendi-score-mcp/releases), extract it, and follow [USAGE.md](USAGE.md). Initial dependency installation needs internet access; computation needs no API.

To use this source checkout, open PowerShell here and substitute your Python 3.11 path:

```powershell
$Python311 = "C:\Python311\python.exe"
& $Python311 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --use-feature=truststore -r src\requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe src\Vendi-Score_mcp.py
```

The final command starts a stdio MCP server for a compatible client. Client configuration and complete parameter descriptions are in [USAGE.md](USAGE.md).

## Tools and actual results

| Tool | Unmodified scientific function | Input |
| --- | --- | --- |
| `vendi_score_similarity` | `vendi.score_K` | finite symmetric positive-semidefinite kernel `K`; optional `q`, `p`, `normalize` |
| `vendi_score_embeddings` | `vendi.score_dual` | already supplied numeric vectors `X`; optional `q`, `normalize` |

Example arguments: `{"X": [[1,0],[1,0],[0,1]], "q": 1}`. These are explicitly synthetic numeric vectors, not model-generated embeddings.

| Test | Original function | Actual MCP call |
| --- | ---: | ---: |
| Official README kernel, q=1 | 2.1573004833739833 | 2.1573004833739833 |
| Official README kernel, q=2 | 1.9480519480519483 | 1.9480519480519483 |
| Supplied duplicated vectors, q=1 | 1.8898815748423097 | 1.8898815748423097 |
| Supplied duplicated vectors, q=2 | 1.7999999999999998 | 1.7999999999999998 |
| Three orthogonal samples, q=1 | 2.9999999999999996 | 2.9999999999999996 |
| Three identical samples, q=1 | 1.0000000000000013 | 1.0000000000000013 |

Each successful call saves its complete inputs, native scalar, source provenance and original warnings in a unique result file, and returns that file's absolute path.

## Verification and reproducibility

- 81 independent tests passed; development real stdio checks covered 33 successful calls and 38 expected errors.
- Project, independent clean environment and independently extracted final ZIP each passed 18 formal MCP acceptance cases.
- The extracted ZIP additionally passed 13 scientific comparisons and 5 expected errors; maximum difference from the original functions was 0.
- The existing Paper2Agent CLI also performed 7 successful real MCP calls and one expected error.
- [Saved native references](notebooks/vendi_reference/reference.json), [independent verification](reports/verification-vendi_metrics.json), [ZIP installation/call acceptance](reports/delivery-validation.json), and [CLI evidence](reports/product-cli-validation.json) are included.

Public report copies redact workstation path strings only; numeric payloads, errors and scientific results are retained. [Publication provenance](reports/publication-provenance.json) records both original and public-copy hashes. Original local records remain unchanged. Paths in archived reports are provenance metadata; fresh runs write actual paths for your machine.

The archived `manifest.json` records implementation handoff status. Final acceptance is established by the later verification and delivery reports linked above.

Run the existing independent suite in this checkout:

```powershell
.\.venv\Scripts\python.exe -m pip install --use-feature=truststore -r requirements-dev.txt
$env:VENDI_VERIFICATION_OUTPUT = Join-Path (Get-Location) "tests\results\local-run"
.\.venv\Scripts\python.exe -X utf8 -m pytest tests\code\vendi_metrics\test_vendi_score_similarity.py tests\code\vendi_metrics\test_vendi_score_embeddings.py -q
New-Item -ItemType Directory -Force tests\logs | Out-Null
.\.venv\Scripts\python.exe -X utf8 tests\code\vendi_metrics\verify_stdio.py
```

The stdio verifier intentionally refuses to overwrite its `tests/results/vendi_metrics/stdio-v1` evidence directory. Preserve or move it before rerunning.

## Scope and known limits

- Original functions lacked type annotations; static automatic conversion refused them. This project adds a reviewed typed adapter and independently tests it. The original scientific algorithm is unchanged.
- `q=0` is explicitly rejected: the pinned original all-ones kernel returns 2 although its mathematical rank is 1, due to tiny positive eigenvalue roundoff. The original result is preserved. Very small positive q can also be numerically sensitive.
- Positive finite `q` and the literal `"inf"` are supported. Invalid shapes, nonfinite inputs, invalid probabilities, invalid kernels and zero vectors are rejected.
- Verification covers these two numerical functions. It does not reproduce model training, feature extraction, datasets or the whole paper's benchmarks.
- Model-import blocking and disabled GPU visibility are scope guards, not a security sandbox.
- This repository is the focused Vendi MCP example produced during the Paper2Agent trial; it does not include the whole trial web application or anyone's personal paper library.

## License

MIT for the adapter and documentation. Upstream scientific source retains its original MIT notices; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
