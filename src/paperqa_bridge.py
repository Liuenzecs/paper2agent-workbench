"""Import already parsed evidence into real PaperQA without re-parsing PDFs."""
from pathlib import Path
import asyncio
import json
import logging
import re
import time
import uuid
from paper_library import PaperLibrary, write_json
from remote_config import prepare_remote_settings, check_remote_targets

REMOTE_QUERY_TIMEOUT_SECONDS = 300

def _validate_paper_ids(value, name):
    if value is not None and (not isinstance(value, list) or not value or
                             any(not isinstance(v, str) or not v.strip() for v in value)):
        raise ValueError(f"{name} must be a nonempty list of paper IDs")

async def build_paperqa_docs(library: PaperLibrary, backend: str = "pymupdf", chunk_mode: str = "blocks", paper_ids: list[str] | None = None):
    from paperqa import Docs, Settings
    from paperqa.types import Doc, Text
    docs = Docs()
    settings = Settings(parsing={"defer_embedding": True, "use_doc_details": False, "multimodal": 0})
    citations = {}
    _validate_paper_ids(paper_ids, "paper_ids")
    if chunk_mode not in {"blocks", "page_windows"}:
        raise ValueError("Unknown PaperQA chunk mode")
    bundles = library.bundles(backend)
    available = {b["paper_id"] for _, b in bundles}
    if paper_ids is not None and (not paper_ids or set(paper_ids) - available):
        raise ValueError("paper_ids contains unknown IDs or is empty")
    for bundle_path, bundle in bundles:
        if paper_ids is not None and bundle["paper_id"] not in paper_ids:
            continue
        doc = Doc(docname=f"P{bundle['paper_id']}", dockey=bundle["source_sha256"],
                  citation=f"{bundle['title']}. {bundle['source_url']}", content_hash=bundle["source_sha256"])
        texts = []
        groups = []
        for block in bundle["blocks"]:
            if chunk_mode == "page_windows" and groups and (
                groups[-1][-1]["locations"][0]["page"] == block["locations"][0]["page"]
                and sum(len(b["text"]) + 2 for b in groups[-1]) + len(block["text"]) < 3200
            ):
                groups[-1].append(block)
            else:
                groups.append([block])
        for group in groups:
            locations = [loc for b in group for loc in b["locations"]]
            pages = ",".join(str(p) for p in sorted({l["page"] for l in locations}))
            block_ids = [b["id"] for b in group]
            name = f"{doc.docname} pages {pages} block {'+'.join(block_ids)}"
            texts.append(Text(text="\n\n".join(b["text"] for b in group), name=name, doc=doc))
            citations[name] = dict(paper_id=bundle["paper_id"], source_sha256=bundle["source_sha256"],
                                   source_url=bundle["source_url"], title=bundle["title"],
                                   locations=locations, block_id=block_ids[0], block_ids=block_ids,
                                   pdf_path=str(bundle_path.parent / "source.pdf"),
                                   parser_warnings=bundle.get("warnings", []))
        await docs.aadd_texts(texts, doc, settings=settings)
    return docs, citations


async def export_paperqa_index(library: PaperLibrary, output_path: str, backend: str = "pymupdf") -> dict:
    docs, citations = await build_paperqa_docs(library, backend)
    path = Path(output_path).resolve()
    # Deferred embeddings have no populated vector index. Reconstruct the official
    # default store rather than serializing its abstract VectorStore field.
    write_json(path, docs.model_dump(mode="json", exclude={"texts_index"}))
    write_json(path.with_suffix(".citations.json"), citations)
    # Actual PaperQA deserialization, not a mock LLM test.
    from paperqa import Docs
    restored = Docs.model_validate_json(path.read_text(encoding="utf-8"))
    if len(restored.docs) != len(docs.docs) or len(restored.texts) != len(docs.texts):
        raise RuntimeError("PaperQA JSON roundtrip changed index")
    return dict(documents=len(docs.docs), chunks=len(docs.texts), index_path=str(path),
                citations_path=str(path.with_suffix(".citations.json")),
                embeddings="deferred", answer_generation="not_run", backend=backend)


def load_settings(settings_path: str):
    """Remote-only execution; credentials are resolved into memory, never artifacts.

    No default paid model or fabricated answer. API credentials remain in environment.
    """
    raw = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    resolved, _ = prepare_remote_settings(raw)
    import truststore
    truststore.inject_into_ssl()
    from paperqa import Settings
    return Settings(**resolved)


def audit_session(session, citations: dict) -> dict:
    """Check citation identity/provenance only; this does not judge claim entailment."""
    ids = list(dict.fromkeys(re.findall(r"\bpqac-[\w-]+", session.raw_answer)))
    contexts = {c.id: c for c in session.contexts}
    references, invalid = [], []
    for cid in ids:
        context = contexts.get(cid)
        entry = citations.get(context.text.name) if context else None
        if entry is None:
            invalid.append(cid)
        else:
            references.append(dict(context_id=cid, text_name=context.text.name,
                                   relevance_score=context.score, source_text=context.text.text,
                                   evidence_summary=context.context, **entry))
    # Native PaperQA standardized refusal phrase. A substantive answer can explain
    # a missing result; only a leading refusal is classified automatically.
    refused = bool(re.match(r"\s*(?:I cannot answer|I can['’]t answer|Insufficient information)", session.raw_answer, re.I))
    return dict(native_refused=refused, cited_context_ids=ids, references=references,
                invalid_citations=invalid, citation_identity_pass=not invalid and (refused or bool(ids)),
                claim_support_status="requires_source_review",
                limitation="Citation identity and source location do not establish scientific claim support")


def embedding_for_settings(settings):
    """Honor Nomic's documented task prefixes using LMI's public mode hook."""
    inner = settings.get_embedding_model()
    if not (settings.embedding_config or {}).get("nomic_task_prefixes", False):
        return inner
    from lmi import EmbeddingModel, EmbeddingModes
    class NomicTaskEmbedding(EmbeddingModel):
        wrapped: EmbeddingModel
        mode: str = "document"
        def set_mode(self, mode):
            self.mode = mode
            self.wrapped.set_mode(mode)
        async def embed_documents(self, texts):
            prefix = "search_query: " if self.mode == EmbeddingModes.QUERY else "search_document: "
            return await self.wrapped.embed_documents([prefix + t for t in texts])
    return NomicTaskEmbedding(name=inner.name, wrapped=inner)


class _ContextFailureHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.failures = 0
    def emit(self, record):
        self.failures += 1


async def query_paperqa(docs, citations: dict, question: str, settings, *, embedding_model=None,
                       partitioning_fn=None, progress=None) -> dict:
    """Run upstream PaperQA; keep model/service failures distinct from abstention."""
    if not question.strip():
        raise ValueError("Question cannot be empty")
    if not docs.docs:
        raise ValueError("PaperQA cannot answer from an empty library")
    check_remote_targets(settings.model_dump(mode="json"))
    calls = []
    def record(result):
        calls.append(dict(name=getattr(result, "name", None), model=result.model,
                          prompt_tokens=result.prompt_count, completion_tokens=result.completion_count,
                          text=result.text, seconds=getattr(result, "seconds_to_last_token", None)))
    summary = settings.get_summary_llm()
    llm = settings.get_llm()
    summary.llm_result_callback = record
    llm.llm_result_callback = record
    handler = _ContextFailureHandler()
    logger = logging.getLogger("paperqa.core")
    logger.addHandler(handler)
    started = time.perf_counter()
    if progress:
        progress("正在检索论文原文并请求远程问答服务")
    try:
        session = await asyncio.wait_for(docs.aquery(question, settings=settings, llm_model=llm, summary_llm_model=summary,
                                   embedding_model=embedding_model or embedding_for_settings(settings),
                                   partitioning_fn=partitioning_fn), timeout=REMOTE_QUERY_TIMEOUT_SECONDS)
    except Exception as exc:
        return dict(question=question, answer="远程服务未完成请求，请检查服务状态及配置后重试。",
                    decision="runtime_error", error_type="RemoteTimeout" if isinstance(exc, TimeoutError) else "RemoteRequestError", model=settings.llm,
                    embedding=settings.embedding, model_calls=calls, native_session=None,
                    seconds=round(time.perf_counter()-started, 3),
                    context_creation_errors=handler.failures, citation_audit=dict(references=[],
                    invalid_citations=[], citation_identity_pass=False, native_refused=False,
                    claim_support_status="not_evaluated"),
                    counts_as_refusal=False, science_status="not_reproduced")
    finally:
        logger.removeHandler(handler)
    audit = audit_session(session, citations)
    if handler.failures or not calls:
        decision = "runtime_error"
    elif audit["native_refused"]:
        decision = "refused"
    elif not audit["citation_identity_pass"]:
        decision = "blocked_invalid_citations"
    else:
        decision = "answered"
    native = session.model_dump(mode="json")
    native.pop("answer_reasoning", None)
    return dict(question=question, answer=session.answer if decision in {"answered", "refused"} else
                "Answer withheld: model execution or citation validation failed.",
                decision=decision, model=settings.llm, embedding=settings.embedding,
                seconds=round(time.perf_counter()-started, 3), native_session=native,
                model_calls=calls, context_creation_errors=handler.failures,
                citation_audit=audit, provenance="Official PaperQA answer with source-bound citation mapping",
                science_status="not_reproduced")


async def ask_paperqa(library: PaperLibrary, question: str, settings_path: str, backend: str = "pymupdf",
                     paper_ids: list[str] | None = None, required_paper_ids: list[str] | None = None,
                     use_cache: bool = True, progress=None, answer_language: str = "auto") -> dict:
    """Use official remote PaperQA on selected papers and preserve each native attempt."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question cannot be empty")
    if answer_language not in {"auto", "zh", "en"}:
        raise ValueError("answer_language must be auto, zh or en")
    if backend != "pymupdf":
        raise ValueError("Paper2Agent supports pymupdf source text only; local parser models are disabled")
    _validate_paper_ids(paper_ids, "paper_ids")
    _validate_paper_ids(required_paper_ids, "required_paper_ids")
    if not isinstance(use_cache, bool):
        raise ValueError("use_cache must be a boolean")
    if progress:
        progress("正在检查远程配置和所选论文")
    settings = load_settings(settings_path)
    docs, citations = await build_paperqa_docs(library, backend, chunk_mode="page_windows", paper_ids=paper_ids)
    selected = list(dict.fromkeys(c["paper_id"] for c in citations.values()))
    if not selected:
        raise ValueError("PaperQA cannot answer from an empty library")
    if required_paper_ids is not None and (not required_paper_ids or set(required_paper_ids) - set(selected)):
        raise ValueError("required_paper_ids must be a nonempty subset of selected paper_ids")
    required = list(dict.fromkeys(required_paper_ids or []))
    from embedding_cache import CachedRemoteEmbedding, embedding_fingerprint
    import hashlib
    source_fingerprints = {t.text: hashlib.sha256(json.dumps(dict(
        source_sha256=citations[t.name]["source_sha256"], block_ids=citations[t.name]["block_ids"],
        chunk_mode="page_windows", schema=1), sort_keys=True).encode()).hexdigest() for t in docs.texts}
    embedding = CachedRemoteEmbedding(name=settings.embedding, wrapped=embedding_for_settings(settings),
        cache_path=library.root / "embedding-cache" / "vectors.sqlite3", enabled=use_cache,
        fingerprint=embedding_fingerprint(settings), source_fingerprints=source_fingerprints)
    language_rule = {"auto": "Answer in the user's question language.", "zh": "Answer in Chinese.",
                     "en": "Answer in English."}[answer_language]
    settings.prompts.system += "\n" + language_rule + (
        " Preserve the upstream English refusal prefix 'I cannot answer' when evidence is insufficient, "
        "then explain in the requested language. Source citations locate evidence, not verified truth.")
    partitions = {doc.dockey: i for i, doc in enumerate(docs.docs.values())}
    partitioning_fn = None
    if len(required) > 1:
        partitioning_fn = lambda text: partitions[text.doc.dockey]
        settings.texts_index_mmr_lambda = 1.0
        settings.answer.evidence_k = max(settings.answer.evidence_k, len(selected) * 2)
        settings.answer.answer_max_sources = max(settings.answer.answer_max_sources, settings.answer.evidence_k)
    if required:
        titles = list(dict.fromkeys(c["title"] for c in citations.values() if c["paper_id"] in required))
        settings.prompts.system += ("\nThis question requires evidence coverage from each of these papers: "
                                   + json.dumps(titles, ensure_ascii=False) +
                                   ". Cite original evidence for claims about each paper. If relevant support "
                                   "is absent, explicitly decline that claim; do not manufacture citations.")
    directory = library.root / "paperqa-answers" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    attempts = []
    for attempt in range(1, 3):
        result = await query_paperqa(docs, citations, question, settings, embedding_model=embedding,
                                     partitioning_fn=partitioning_fn, progress=progress)
        covered = list(dict.fromkeys(r["paper_id"] for r in result["citation_audit"]["references"]))
        missing = [p for p in required if p not in covered]
        result["source_coverage"] = dict(selected_paper_ids=selected, required_paper_ids=required,
                                        cited_paper_ids=covered, missing_paper_ids=missing,
                                        complete=not missing)
        result["cache"] = dict(enabled=use_cache, fingerprint=embedding.fingerprint,
                                stats=dict(embedding.stats),
                                limitation="remote_requests counts public embedding interface calls, not transport-level batches. "
                                           "Remote alias weight changes require clearing cache or a versioned model configuration")
        attempt_path = directory / f"attempt-{attempt}.json"
        write_json(attempt_path, result)
        attempts.append(str(attempt_path))
        if not missing or result["decision"] != "answered" or attempt == 2:
            break
        if progress:
            progress("引用未覆盖全部指定论文，正在进行一次扩展检索")
        settings.answer.evidence_k = min(len(docs.texts), max(settings.answer.evidence_k * 2, len(selected) * 4))
        settings.answer.answer_max_sources = max(settings.answer.answer_max_sources, settings.answer.evidence_k)
        settings.prompts.system += ("\nA previous attempt lacked required paper citations. Recheck the expanded "
                                   "original evidence and cite each required paper where its claim is supported. "
                                   "Omit or decline unsupported claims; do not invent source coverage.")
    if result["decision"] == "answered" and missing:
        result = dict(result, decision="insufficient_source_coverage",
                      answer="引用尚未覆盖全部指定论文，完整答案暂不提供。请查看保存的原始尝试与缺失论文。",
                      counts_as_refusal=False)
    result["attempt_paths"] = attempts
    result["attempt_count"] = len(attempts)
    result["chunk_mode"] = "page_windows"
    result["answer_language"] = answer_language
    result["remote_only"] = True
    write_json(directory / "result.json", result)
    if progress:
        progress("结果与原始问答记录已保存")
    return dict(result, result_path=str(directory / "result.json"), chunk_mode="page_windows")
