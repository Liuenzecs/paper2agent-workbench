"use strict";
// No remote assets, HTML injection, browser key storage, or model calls from the UI.
const $ = (id) => document.getElementById(id);
const state = {token: "", papers: [], selected: new Set(), activeJob: null, polling: null, scanPath: "", scanned: [], generated: null};
const decisions = {answered: "有证据的回答", refused: "缺少证据 · 已拒答", runtime_error: "远程服务故障", insufficient_source_coverage: "引用覆盖不足"};
const explanations = {
  answered: "引用已绑定到论文和页码。结论是否被原文完整支持，仍需结合原文检查。",
  refused: "论文证据不足以回答该问题。模型拒答已单独记录，并非服务失败。",
  runtime_error: "服务调用未正常完成，没有得到可评估的论文答案。请检查 API 配置或稍后重试。",
  insufficient_source_coverage: "当前答案没有覆盖要求的所有论文。此结果暂不视为完整回答，请检查遗漏引用后再使用。"
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function notice(message = "") { $("notice").textContent = message; $("notice").hidden = !message; }
function listOf(value, ...keys) {
  if (Array.isArray(value)) return value;
  for (const key of keys) if (value && Array.isArray(value[key])) return value[key];
  return [];
}
function paperId(paper) { return String(paper.paper_id || paper.id || ""); }
function validId(value) { return typeof value === "string" && /^[A-Za-z0-9_-]{1,120}$/.test(value); }

async function api(path, payload) {
  const options = {headers: {Accept: "application/json"}, cache: "no-store"};
  if (payload !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.headers["X-Lab-Token"] = state.token;
    options.body = JSON.stringify(payload);
  }
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error("无法连接本机工作台。请确认服务仍在运行，然后刷新页面。"); }
  let data;
  try { data = await response.json(); }
  catch { throw new Error("本机服务返回了无法读取的内容，请刷新页面重试。"); }
  if (!response.ok) throw new Error(data.message || "操作失败，请重试。");
  return data;
}

function switchView(name) {
  for (const view of ["reading", "settings", "code"]) $(`${view}-view`).hidden = view !== name;
  document.querySelectorAll(".nav").forEach((button) => { const active = button.dataset.view === name; button.classList.toggle("active", active); button.setAttribute("aria-current", active ? "page" : "false"); });
  $("view-title").textContent = {reading: "阅读论文，让答案有据可查。", settings: "你的论文库，你的远程服务。", code: "从现有代码，迈向可调用的工具。"}[name];
  notice();
}

async function refreshStatus() {
  const status = await api("/api/status");
  if (status.browser_token) state.token = status.browser_token;
  const config = status.settings || status.configuration || status.config || status.remote || status;
  for (const name of ["api_base", "chat_model", "embedding_model", "embedding_api_base"]) {
    const input = $("settings-form").elements.namedItem(name);
    if (typeof config[name] === "string") input.value = (config[name].includes(".invalid") || ["your-chat-model", "your-embedding-model"].includes(config[name])) ? "" : config[name];
  }
  const chatKey = config.chat_key_available ?? config.chat_credential_available ?? status.chat_key_available;
  const embeddingKey = config.embedding_key_available ?? config.embedding_credential_available ?? status.embedding_key_available;
  const credentials = status.credential_available || config.credential_available || {};
  $("key-state").textContent = `问答密钥：${(chatKey ?? credentials.chat) ? "已配置" : "待配置"} · 嵌入密钥：${(embeddingKey ?? credentials.embedding) ? "已配置" : "待配置"}`;
  const ready = status.ready ?? status.remote_ready ?? config.ready ?? Boolean((chatKey ?? credentials.chat) && (embeddingKey ?? credentials.embedding));
  $("connection-state").textContent = ready === false ? "本机论文库 · API 待配置" : "本机论文库 · 远程模型";
}

async function refreshPapers() {
  const value = await api("/api/papers");
  const previous = new Set(state.papers.map(paperId));
  state.papers = listOf(value, "papers", "items", "documents").filter((p) => validId(paperId(p)));
  const current = new Set(state.papers.map(paperId));
  state.selected = new Set([...state.selected].filter((id) => current.has(id)));
  for (const id of current) if (!previous.has(id)) state.selected.add(id);
  $("papers").replaceChildren();
  if (!state.papers.length) $("papers").append(el("p", "empty", "导入可提取文本的 PDF，开始建立自己的论文库。"));
  for (const paper of state.papers) {
    const row = el("label", "paper");
    const checkbox = el("input"); checkbox.type = "checkbox"; checkbox.checked = state.selected.has(paperId(paper));
    checkbox.addEventListener("change", () => checkbox.checked ? state.selected.add(paperId(paper)) : state.selected.delete(paperId(paper)));
    const copy = el("span", "", paper.title || paper.filename || paperId(paper));
    copy.append(el("small", "", `${paper.page_count || paper.pages || "—"} 页 · ${paper.backend || paper.parser?.name || "PDF"}`));
    row.append(checkbox, copy); $("papers").append(row);
  }
}

async function refreshHistory() {
  const value = await api("/api/history");
  const items = listOf(value, "history", "items", "answers", "results");
  $("history").replaceChildren();
  if (!items.length) $("history").append(el("p", "empty", "还没有问答记录。每次运行会独立保留结果。"));
  for (const item of items) {
    const id = String(item.answer_id || item.job_id || item.id || "");
    if (!validId(id)) continue;
    const row = el("button", "history-item"); row.type = "button";
    row.append(el("span", "history-question", item.question || "查看问答结果"));
    const rawTime = item.created_at || item.finished_at || item.started_at || "";
    const date = new Date(rawTime);
    row.append(el("span", "history-meta", `${decisions[item.decision] || item.status || "查看记录"}${Number.isNaN(date.getTime()) ? "" : ` · ${date.toLocaleString()}`}`));
    row.addEventListener("click", async () => {
      try {
        if (item.job_id && !item.answer_id) {
          const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
          if (job.result) showAnswer(job.result);
          else showFailure(job, item.question);
        } else showAnswer(await api(`/api/answers/${encodeURIComponent(id)}`));
        switchView("reading"); $("answer-card").scrollIntoView({behavior: "smooth", block: "start"});
      }
      catch (error) { notice(error.message); }
    });
    $("history").append(row);
  }
}

function showFailure(job, question = "") {
  showAnswer({question: job.question || question || "问答运行记录", decision: "runtime_error", answer: job.error || "问答未能完成，请检查 API 配置、网络连接和服务可用性。", citations: []});
}

function citationRows(result) {
  return listOf(result.citations || result.citation_audit?.references || result.references, "references", "items");
}
function showAnswer(raw) {
  const result = raw.result && typeof raw.result === "object" ? raw.result : raw;
  const decision = result.decision || "runtime_error";
  const id = String(result.answer_id || result.id || "");
  $("answer-card").hidden = false;
  $("answer-state").className = `answer-state ${Object.hasOwn(decisions, decision) ? decision : "runtime_error"}`;
  $("answer-state").textContent = decisions[decision] || "结果状态待确认";
  $("answer-question").textContent = result.question || "论文问答";
  const missing = result.coverage?.missing_paper_ids || result.source_coverage?.missing_paper_ids || [];
  const missingTitles = missing.map((paper) => state.papers.find((p) => paperId(p) === paper)?.title || paper);
  $("answer-explanation").textContent = (explanations[decision] || explanations.runtime_error) + (missingTitles.length ? ` 尚未引用：${missingTitles.join("、")}。` : "");
  // Render native content only as text. Raw HTML, markdown scripts and COT are never interpreted.
  $("answer-text").textContent = result.answer || result.raw_answer || result.native_session?.answer || result.native_session?.raw_answer || result.message || "本次运行未返回可展示的答案。";
  for (const [element, format] of [["export-md", "markdown"], ["export-json", "json"]]) {
    $(element).hidden = !validId(id);
    if (validId(id)) $(element).href = `/api/exports/${encodeURIComponent(id)}?format=${format}`;
  }
  const references = citationRows(result);
  $("citations").replaceChildren();
  $("citation-count").textContent = `${references.length} 条可回查引用`;
  for (const [index, ref] of references.entries()) {
    const title = ref.title || state.papers.find((p) => paperId(p) === ref.paper_id)?.title || "论文原文";
    const locations = Array.isArray(ref.locations) ? ref.locations : [];
    const pages = Array.isArray(ref.pages) ? ref.pages : locations.length ? [...new Set(locations.map((l) => l.page))] : [ref.page || ref.page_number].filter(Boolean);
    const validPages = pages.map(Number).filter((n) => Number.isInteger(n) && n > 0 && n <= 500);
    const card = el("section", "citation"); const heading = el("div", "citation-head");
    heading.append(el("span", "citation-title", `引用 ${ref.number || index + 1} · ${title}${validPages.length ? ` · 第 ${validPages.join("、")} 页` : " · 页码待确认"}`));
    if (validId(String(ref.paper_id || "")) && validPages.length) {
      const link = el("a", "", "打开 PDF 原页 ↗"); link.href = `/papers/${encodeURIComponent(ref.paper_id)}.pdf#page=${validPages[0]}`; link.target = "_blank"; link.rel = "noopener noreferrer";
      heading.append(link);
    }
    const details = el("details"); details.append(el("summary", "", "展开引用原文"));
    details.append(el("blockquote", "", ref.source_text || ref.text || ref.quote || "此引用未返回原文片段，请查看 PDF 原页。"));
    card.append(heading, details);
    if (ref.context_id) card.append(el("span", "citation-id", ref.context_id));
    $("citations").append(card);
  }
  if (!references.length) $("citations").append(el("p", "empty", "本次没有可定位的引用。请勿把服务故障或缺证据结果当作论文结论。"));
}

async function watchJob(jobId) {
  state.activeJob = jobId;
  const start = Date.now();
  const poll = async () => {
    if (state.activeJob !== jobId) return;
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      $("job-elapsed").textContent = `已等待 ${Math.floor((Date.now() - start) / 1000)} 秒`;
      $("job-message").textContent = typeof job.stage === "string" ? job.stage : typeof job.message === "string" ? job.message : ({queued: "任务已排队，等待远程问答…", running: "正在检索论文并向远程服务请求答案…", completed: "问答完成", failed: "任务失败"}[job.status] || "正在处理…");
      if (job.status === "completed" || job.status === "failed") {
        state.activeJob = null; $("job-status").hidden = true; $("ask-button").disabled = false;
        if (job.result && typeof job.result === "object") showAnswer(job.result);
        else if (validId(String(job.answer_id || job.result_id || ""))) showAnswer(await api(`/api/answers/${encodeURIComponent(job.answer_id || job.result_id)}`));
        else if (job.status === "failed") { showFailure(job, $("question").value.trim()); notice("问答未能完成。这是运行故障，不是缺证据拒答；请检查远程 API 配置。失败记录已保留。"); }
        else notice("任务结束，但没有可读取的答案记录。请查看历史或重试。");
        await refreshHistory(); return;
      }
      state.polling = setTimeout(poll, 1400);
    } catch (error) {
      state.activeJob = null; $("job-status").hidden = true; $("ask-button").disabled = false;
      notice(`${error.message} 本次任务可能仍在后台运行；请稍后刷新历史。`);
    }
  };
  await poll();
}

$("question-form").addEventListener("submit", async (event) => {
  event.preventDefault(); notice();
  if (!state.selected.size) { notice("请先导入并选中至少一篇论文。"); return; }
  $("ask-button").disabled = true; $("job-status").hidden = false; $("job-message").textContent = "正在创建问答任务…";
  try {
    const ids = [...state.selected];
    const result = await api("/api/ask", {question: $("question").value.trim(), paper_ids: ids,
      required_paper_ids: $("compare").checked ? ids : null, answer_language: $("language").value});
    if (!validId(String(result.job_id || ""))) throw new Error("本机服务没有返回有效任务编号。");
    await watchJob(result.job_id);
  } catch (error) { $("ask-button").disabled = false; $("job-status").hidden = true; notice(error.message); }
});

function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("无法读取 PDF 文件。"));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.readAsDataURL(file);
  });
}
$("pdf-input").addEventListener("change", async (event) => {
  const files = [...event.target.files]; let done = 0; const errors = [];
  $("pdf-input").disabled = true; notice();
  for (const file of files) {
    $("upload-status").textContent = `导入 ${done + 1}/${files.length}：${file.name}`;
    try {
      if (file.size > 64 * 1024 * 1024) throw new Error("文件超过 64 MB");
      const value = await api("/api/upload", {filename: file.name, content_base64: await readBase64(file)});
      const warnings = listOf(value.warnings);
      if (warnings.length) errors.push(`${file.name}：${warnings.slice(0, 3).join("；")}`);
      done++;
    } catch (error) { errors.push(`${file.name}：${error.message}`); }
  }
  $("upload-status").textContent = `完成 ${done}/${files.length} 篇导入。`;
  $("pdf-input").value = ""; $("pdf-input").disabled = false;
  try { await refreshPapers(); } catch (error) { errors.push(error.message); }
  if (errors.length) notice(errors.join("\n"));
});

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault(); notice(); const button = event.target.querySelector("button[type=submit]"); button.disabled = true;
  const settings = {};
  for (const [name, value] of new FormData(event.target)) {
    // Empty keys are omitted; stored runtime credentials remain intact.
    if ((name === "chat_key" || name === "embedding_key") && !String(value).trim()) continue;
    settings[name] = String(value).trim();
  }
  settings.remember_keys = $("remember-keys").checked;
  try { await api("/api/settings", settings); await refreshStatus(); notice("远程 API 配置已保存。现在可以返回论文问答开始验证。"); }
  catch (error) { notice(error.message); }
  finally { event.target.elements.chat_key.value = ""; event.target.elements.embedding_key.value = ""; button.disabled = false; }
});

$("code-fetch-form").addEventListener("submit", async (event) => {
  event.preventDefault(); notice(); $("fetch-button").disabled = true;
  $("code-fetch-status").textContent = "正在下载公开仓库源码，最多等待约两分钟；不执行代码或下载模型权重…";
  try {
    const result = await api("/api/code/fetch", {repository_url: $("repository-url").value.trim(), ref: $("repository-ref").value.trim()});
    if (!result.repository_path) throw new Error("没有得到已下载的源码目录。");
    $("repository-path").value = result.repository_path;
    $("code-fetch-status").textContent = `下载完成 · 来源：${result.url || $("repository-url").value} · 提交：${result.commit || "待核对"}`;
    $("code-scan-form").requestSubmit();
  } catch (error) { $("code-fetch-status").textContent = "源码下载未完成。"; notice(error.message); }
  finally { $("fetch-button").disabled = false; }
});

function functionId(item) { return String(item.id || item.selection || item.callable || (item.module && (item.function || item.name) ? `${item.module}:${item.function || item.name}` : item.qualified_name || "")); }
function displayReason(item) {
  const reason = item.reason || item.blocked_reason || item.reasons?.join("；") || "当前函数尚不可生成工具";
  return reason.replace(/malformed node or string[^；]*/g, "默认参数无法自动解析，需人工适配");
}
$("code-scan-form").addEventListener("submit", async (event) => {
  event.preventDefault(); notice(); $("scan-button").disabled = true; $("code-build-form").hidden = true; $("code-result").hidden = true; $("code-test-form").hidden = true; state.generated = null;
  $("code-scan-status").textContent = "正在静态扫描源码，不执行仓库…";
  state.scanPath = $("repository-path").value.trim();
  try {
    const scan = await api("/api/code/scan", {repository_path: state.scanPath});
    state.scanned = listOf(scan, "candidates", "functions", "tools", "items");
    $("code-functions").replaceChildren(); let count = 0;
    for (const item of state.scanned) {
      const selection = functionId(item); if (!selection) continue;
      const row = el("label", "code-function"); const checkbox = el("input"); checkbox.type = "checkbox"; checkbox.value = selection;
      const eligible = item.eligible !== false && item.supported !== false && item.can_wrap !== false;
      checkbox.disabled = !eligible;
      const text = el("span"); text.append(el("code", "", selection));
      text.append(el("small", "", item.description || item.docstring || item.signature || "现有源码函数"));
      if (!eligible) text.append(el("small", "", displayReason(item)));
      row.append(checkbox, text); $("code-functions").append(row); count++;
    }
    $("code-count").textContent = `${count} 个函数条目`;
    $("code-scan-status").textContent = count ? "扫描完成。请依据函数用途和环境要求选择工具。" : "没有找到可绑定的 Python 函数。请确认仓库目录或通过 CLI 查看扫描报告。";
    $("code-build-form").hidden = !count;
  } catch (error) { $("code-scan-status").textContent = "扫描未完成。"; notice(error.message); }
  finally { $("scan-button").disabled = false; }
});

$("code-build-form").addEventListener("submit", async (event) => {
  event.preventDefault(); notice();
  if (state.scanPath !== $("repository-path").value.trim()) { notice("仓库路径已改变，请重新扫描后再生成。"); return; }
  const selections = [...$("code-functions").querySelectorAll("input:checked")].map((input) => input.value);
  if (!selections.length) { notice("请勾选至少一个现有函数。"); return; }
  $("build-button").disabled = true;
  try {
    const result = await api("/api/code/build", {repository_path: state.scanPath, selections, name: $("mcp-name").value.trim()});
    $("code-result").replaceChildren(el("h3", "", "MCP 项目已生成"));
    const directory = result.project_path || result.output_dir || result.project_dir || result.directory || result.path;
    if (directory) $("code-result").append(el("p", "", `保存位置：${directory}`));
    if (result.message) $("code-result").append(el("p", "", result.message));
    const commands = result.commands || result.test_commands || result.next_steps || [];
    if (Array.isArray(commands)) for (const command of commands) $("code-result").append(el("pre", "", typeof command === "string" ? command : JSON.stringify(command, null, 2)));
    else if (typeof commands === "object") for (const [label, command] of Object.entries(commands)) { $("code-result").append(el("p", "", label), el("pre", "", Array.isArray(command) ? command.join("\n") : command)); }
    $("code-result").append(el("p", "", "生成阶段没有执行仓库函数、安装依赖或运行模型。请按生成项目的使用说明进行环境与真实输入验证。"));
    $("code-result").hidden = false;
    const tools = listOf(result, "selected_tools", "tools").filter((tool) => typeof tool.tool_name === "string");
    if (directory && tools.length) {
      state.generated = {project_path: directory, tools};
      $("test-tool").replaceChildren();
      for (const tool of tools) { const option = el("option", "", tool.tool_name); option.value = tool.tool_name; $("test-tool").append(option); }
      $("test-arguments").value = "{}"; $("test-result").hidden = true; $("code-test-form").hidden = false;
      showTestParameters();
    }
  } catch (error) { notice(error.message); }
  finally { $("build-button").disabled = false; }
});

function showTestParameters() {
  if (!state.generated) return;
  const tool = state.generated.tools.find((t) => t.tool_name === $("test-tool").value);
  let info = $("test-parameter-info");
  if (!info) { info = el("p", "hint"); info.id = "test-parameter-info"; $("test-arguments").parentElement.after(info); }
  const parameters = tool?.parameters || [];
  info.textContent = parameters.length ? `源码参数：${parameters.map((p) => `${p.name}${p.required ? "（必填）" : "（可选）"}${p.annotation ? ` : ${p.annotation}` : ""}`).join("；")}。请自行提供真实输入。` : "该工具没有参数。";
}
$("test-tool").addEventListener("change", showTestParameters);
$("code-test-form").addEventListener("submit", async (event) => {
  event.preventDefault(); notice();
  if (!state.generated) { notice("请先生成 MCP 项目。"); return; }
  let testArguments;
  try {
    testArguments = JSON.parse($("test-arguments").value);
    if (!testArguments || Array.isArray(testArguments) || typeof testArguments !== "object") throw new Error();
  } catch { notice("测试参数需要是合法 JSON 对象，例如 {\"x\": 2}。请根据原函数填写真实输入。"); return; }
  $("test-button").disabled = true; $("test-result").hidden = false;
  $("test-result").replaceChildren(el("p", "", "正在运行所选原函数测试…最多等待约 30 秒。"));
  try {
    const result = await api("/api/code/test", {project_path: state.generated.project_path, tool_name: $("test-tool").value,
      arguments: testArguments, python_executable: $("test-python").value.trim()});
    const labels = {completed: "真实 MCP 调用完成", execution_error: "执行失败 · 已保留记录", timeout: "调用超时 · 未计成功"};
    $("test-result").replaceChildren(el("h3", "", labels[result.decision] || "测试状态待核对"));
    if (result.result) $("test-result").append(el("pre", "", JSON.stringify(result.result, null, 2)));
    if (result.error) $("test-result").append(el("pre", "", result.error));
    if (result.report_path) $("test-result").append(el("p", "", `记录位置：${result.report_path}`));
    $("test-result").append(el("p", "", "这次调用验证工具能够接收输入并执行原函数；结果是否符合论文，需要与上游直接运行的参考结果独立比较。"));
  } catch (error) { $("test-result").replaceChildren(el("p", "", error.message)); }
  finally { $("test-button").disabled = false; }
});

$("vendi-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("vendi-button").disabled = true;
  $("vendi-result").hidden = false;
  $("vendi-result").replaceChildren(el("p", "", "正在实际调用两个 Vendi 工具，结果会保存在你的工作区…"));
  try {
    const result = await api("/api/examples/vendi", {python_executable: $("vendi-python").value.trim()});
    $("vendi-result").replaceChildren(el("h3", "", result.success ? "五项实测通过，已保留原始结果" : "存在未通过项目，请检查保存记录"));
    for (const item of result.cases || []) $("vendi-result").append(el("p", "", `${item.case}：${item.passed ? "通过" : "未通过"}${item.score === null ? " · 预期拒绝" : ` · 分数 ${item.score}`}`));
    $("vendi-result").append(el("p", "", `完整记录：${result.report_path}`));
  } catch (error) { $("vendi-result").replaceChildren(el("p", "", error.message)); }
  finally { $("vendi-button").disabled = false; }
});

document.querySelectorAll(".nav").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
$("select-all").addEventListener("click", () => {
  const select = state.selected.size !== state.papers.length;
  state.selected = new Set(select ? state.papers.map(paperId) : []);
  $("papers").querySelectorAll("input").forEach((input) => { input.checked = select; });
});
$("refresh-history").addEventListener("click", () => refreshHistory().catch((error) => notice(error.message)));
Promise.all([refreshStatus(), refreshPapers(), refreshHistory()]).catch((error) => notice(error.message));
