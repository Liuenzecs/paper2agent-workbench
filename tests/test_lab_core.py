"""Offline wrapper/cache contracts with fake remote interfaces; no factual QA score."""
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest
from lmi import EmbeddingModel, EmbeddingModes
from pydantic import Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from embedding_cache import CachedRemoteEmbedding, embedding_fingerprint
import paperqa_bridge as bridge


class FakeRemote(EmbeddingModel):
    calls: list = Field(default_factory=list)
    mode: EmbeddingModes = EmbeddingModes.DOCUMENT
    bad: str | None = None

    def set_mode(self, mode):
        self.mode = EmbeddingModes(mode)

    async def embed_documents(self, texts):
        self.calls.append((self.mode, list(texts)))
        if self.bad == "nan":
            return [[float("nan"), 1] for _ in texts]
        if self.bad == "count":
            return []
        if self.bad == "dimension":
            return [[1, 2, 3] for _ in texts]
        return [[1.0, (int(hashlib.sha256(t.encode()).hexdigest()[:4], 16) + 1) / 65536] for t in texts]


def cache(path, remote=None, fingerprint="model-v1", **kw):
    return CachedRemoteEmbedding(name="openai/fake", wrapped=remote or FakeRemote(name="openai/fake"),
                                 cache_path=path / "vectors.sqlite3", fingerprint=fingerprint, **kw)


def fake_library(tmp_path):
    rows = []
    for i, paper_id in enumerate(("a", "b")):
        bundle = dict(paper_id=paper_id, source_sha256=hashlib.sha256(paper_id.encode()).hexdigest(),
                      title=f"Paper {paper_id}", source_url=f"https://example.org/{paper_id}",
                      blocks=[dict(id=f"p1-b{j}", text=f"{paper_id} source block {j} " * 150,
                                   locations=[dict(page=1, bbox=[0,0,1,1])]) for j in range(8)])
        rows.append((tmp_path / paper_id / "bundle.json", bundle))
    return SimpleNamespace(root=tmp_path, bundles=lambda backend: rows)


def remote_settings():
    from paperqa import Settings
    base = dict(model_list=[dict(model_name="test-chat", litellm_params=dict(
        model="openai/test", api_base="https://example.org/v1", api_key="fake-not-a-secret"))])
    return Settings(llm="test-chat", summary_llm="test-chat", llm_config=base, summary_llm_config=base,
        embedding="openai/fake", embedding_config=dict(kwargs=dict(api_base="https://example.org/v1")),
        answer=dict(evidence_skip_summary=True, evidence_k=2, answer_max_sources=2))


def test_restart_and_mode_cache(tmp_path):
    async def run():
        remote = FakeRemote(name="openai/fake")
        first = cache(tmp_path, remote)
        docs = await first.embed_documents(["same", "same"])
        assert len(remote.calls) == 1 and remote.calls[0][1] == ["same"]
        fresh_remote = FakeRemote(name="openai/fake")
        restarted = cache(tmp_path, fresh_remote)
        assert await restarted.embed_documents(["same"]) == docs[:1]
        assert not fresh_remote.calls
        restarted.set_mode(EmbeddingModes.QUERY)
        await restarted.embed_documents(["same"])
        assert fresh_remote.calls[0][0] == EmbeddingModes.QUERY
        restarted.set_mode(EmbeddingModes.DOCUMENT)
        await restarted.embed_documents(["same"])
        assert len(fresh_remote.calls) == 1
    asyncio.run(run())


def test_source_config_and_text_invalidation(tmp_path):
    async def run():
        first = cache(tmp_path, source_fingerprints={"text": "source-1:block-v1"})
        await first.embed_documents(["text"])
        changed_source = cache(tmp_path, source_fingerprints={"text": "source-2:block-v1"})
        await changed_source.embed_documents(["text"])
        assert changed_source.stats["remote_texts"] == 1
        changed_config = cache(tmp_path, fingerprint="model-v2")
        await changed_config.embed_documents(["text"])
        assert changed_config.stats["remote_texts"] == 1
        await first.embed_documents(["different"])
        assert first.stats["remote_texts"] == 2
    asyncio.run(run())


@pytest.mark.parametrize("bad", ["nan", "count", "dimension"])
def test_invalid_remote_vectors(tmp_path, bad):
    async def run():
        model = cache(tmp_path, FakeRemote(name="openai/fake", bad=bad, ndim=2))
        with pytest.raises(ValueError):
            await model.embed_documents(["text"])
    asyncio.run(run())


def test_corrupt_cached_entry_is_refetched(tmp_path):
    async def run():
        await cache(tmp_path).embed_documents(["text"])
        with sqlite3.connect(tmp_path / "vectors.sqlite3") as con:
            con.execute("UPDATE vectors SET vector_json='[NaN,1]'")
        new = cache(tmp_path)
        await new.embed_documents(["text"])
        assert new.stats["invalid_entries"] == 1 and new.stats["remote_texts"] == 1
    asyncio.run(run())


def test_no_cache_mode(tmp_path):
    async def run():
        model = cache(tmp_path, enabled=False)
        await model.embed_documents(["text"])
        await model.embed_documents(["text"])
        assert model.stats["remote_requests"] == 2
        assert not (tmp_path / "vectors.sqlite3").exists()
    asyncio.run(run())

def test_successful_embedding_batches_survive_later_failure(tmp_path):
    class Remote(FakeRemote):
        async def embed_documents(self,texts):
            if 'unavailable' in texts:raise RuntimeError('Remote batch failed')
            return await super().embed_documents(texts)
    async def run():
        remote=Remote(name='openai/fake')
        model=cache(tmp_path,remote,remote_batch_size=1,request_interval_seconds=0)
        with pytest.raises(RuntimeError):await model.embed_documents(['saved','unavailable'])
        restarted_remote=FakeRemote(name='openai/fake')
        restarted=cache(tmp_path,restarted_remote)
        result=await restarted.embed_documents(['saved'])
        assert len(result)==1 and not restarted_remote.calls and restarted.stats['hits']==1
        assert model.stats['remote_errors']==1
    asyncio.run(run())

def test_embedding_rate_limit_retry_is_bounded(tmp_path,monkeypatch):
    import embedding_cache
    class RateLimited(Exception):status_code=429
    class Remote(FakeRemote):
        async def embed_documents(self,texts):
            self.calls.append((self.mode,list(texts)))
            raise RateLimited('Too frequent')
    async def no_wait(delay):return None
    monkeypatch.setattr(embedding_cache.asyncio,'sleep',no_wait)
    async def run():
        remote=Remote(name='openai/fake');model=cache(tmp_path,remote,request_interval_seconds=0)
        with pytest.raises(RateLimited):await model.embed_documents(['source'])
        assert len(remote.calls)==2 and model.stats['remote_errors']==2
        assert model.stats['rate_limit_retries']==1
        with sqlite3.connect(tmp_path/'vectors.sqlite3') as con:
            assert con.execute('SELECT count(*) FROM vectors').fetchone()[0]==0
    asyncio.run(run())


def test_fingerprint_excludes_key_but_includes_endpoint():
    one = remote_settings()
    one.embedding_config["kwargs"]["api_key"] = "first"
    two = one.model_copy(deep=True)
    two.embedding_config["kwargs"]["api_key"] = "second"
    assert embedding_fingerprint(one) == embedding_fingerprint(two)
    two.embedding_config["kwargs"]["api_base"] = "https://another.example.org/v1"
    assert embedding_fingerprint(one) != embedding_fingerprint(two)


def test_selection_validation_and_upstream_partitioning(tmp_path):
    async def run():
        library = fake_library(tmp_path)
        for bad in ([], ["unknown"]):
            with pytest.raises(ValueError):
                await bridge.build_paperqa_docs(library, paper_ids=bad)
        single, citations = await bridge.build_paperqa_docs(library, paper_ids=["b"])
        assert len(single.docs) == 1 and {c["paper_id"] for c in citations.values()} == {"b"}
        docs, citations = await bridge.build_paperqa_docs(library)
        settings = remote_settings()
        settings.texts_index_mmr_lambda = 1.0
        partitions = {d.dockey: i for i, d in enumerate(docs.docs.values())}
        results = await docs.retrieve_texts("query", 4, settings=settings,
            embedding_model=FakeRemote(name="openai/fake"),
            partitioning_fn=lambda text: partitions[text.doc.dockey])
        assert len(results) == 4
        assert [citations[t.name]["paper_id"] for t in results] == ["a", "b", "a", "b"]
    asyncio.run(run())


def fake_answer(ids=("a",), decision="answered"):
    return dict(question="Q", answer="fake answer", decision=decision,
                native_session=dict(raw_answer="fake native answer"), model_calls=[],
                citation_audit=dict(references=[dict(paper_id=p) for p in ids],
                                    citation_identity_pass=True, native_refused=decision=="refused"))


def test_bounded_coverage_retry_and_preserved_attempts(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(bridge, "load_settings", lambda path: remote_settings())
        monkeypatch.setattr(bridge, "embedding_for_settings", lambda settings: FakeRemote(name="openai/fake"))
        calls = []
        async def query(*args, **kwargs):
            calls.append(kwargs)
            return fake_answer()
        monkeypatch.setattr(bridge, "query_paperqa", query)
        result = await bridge.ask_legacy_paperqa(fake_library(tmp_path), "Q", "unused", required_paper_ids=["a", "b"])
        assert result["decision"] == "insufficient_source_coverage" and len(calls) == 2
        assert result["source_coverage"]["missing_paper_ids"] == ["b"]
        originals = [json.loads(Path(p).read_text(encoding="utf-8")) for p in result["attempt_paths"]]
        assert all(r["decision"] == "answered" and r["answer"] == "fake answer" for r in originals)
        assert Path(result["result_path"]).is_file()
        assert all(c["partitioning_fn"] is not None for c in calls)
        again = await bridge.ask_legacy_paperqa(fake_library(tmp_path), "Q", "unused")
        assert again["result_path"] != result["result_path"]
    asyncio.run(run())


def test_successful_coverage_retry(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(bridge, "load_settings", lambda path: remote_settings())
        monkeypatch.setattr(bridge, "embedding_for_settings", lambda settings: FakeRemote(name="openai/fake"))
        answers = iter([fake_answer(), fake_answer(("a", "b"))])
        async def query(*args, **kwargs):
            return next(answers)
        monkeypatch.setattr(bridge, "query_paperqa", query)
        result = await bridge.ask_legacy_paperqa(fake_library(tmp_path), "Q", "unused", required_paper_ids=["a", "b"])
        assert result["decision"] == "answered" and result["attempt_count"] == 2
        assert result["source_coverage"]["complete"]
    asyncio.run(run())


@pytest.mark.parametrize("decision", ["refused", "runtime_error"])
def test_refusal_and_failure_are_not_coverage_retries(tmp_path, monkeypatch, decision):
    async def run():
        monkeypatch.setattr(bridge, "load_settings", lambda path: remote_settings())
        monkeypatch.setattr(bridge, "embedding_for_settings", lambda settings: FakeRemote(name="openai/fake"))
        async def query(*args, **kwargs):
            return fake_answer((), decision)
        monkeypatch.setattr(bridge, "query_paperqa", query)
        result = await bridge.ask_legacy_paperqa(fake_library(tmp_path), "Q", "unused", required_paper_ids=["a", "b"])
        assert result["decision"] == decision and result["attempt_count"] == 1
    asyncio.run(run())


def test_invalid_required_selection(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(bridge, "load_settings", lambda path: remote_settings())
        for selected, required in ((["a"], ["b"]), (["a"], [])):
            with pytest.raises(ValueError):
                await bridge.ask_paperqa(fake_library(tmp_path), "Q", "unused", paper_ids=selected,
                                        required_paper_ids=required)
    asyncio.run(run())


@pytest.mark.parametrize("bad", ["a", [None], [""], [" "]])
def test_invalid_paper_id_types(tmp_path, bad):
    async def run():
        with pytest.raises(ValueError):
            await bridge.build_paperqa_docs(fake_library(tmp_path), paper_ids=bad)
    asyncio.run(run())


def test_public_query_service_failure_saved_without_provider_exception(tmp_path):
    async def run():
        class BrokenDocs:
            docs = {"fake": True}
            async def aquery(self, *args, **kwargs):
                raise RuntimeError("fake-provider-sensitive-information")
        result = await bridge.query_paperqa(BrokenDocs(), {}, "Q", remote_settings(),
                                            embedding_model=FakeRemote(name="openai/fake"))
        assert result["decision"] == "runtime_error" and not result["counts_as_refusal"]
        assert result["native_session"] is None
        assert "fake-provider-sensitive-information" not in json.dumps(result)
    asyncio.run(run())


def test_local_model_target_rejected_before_query():
    async def run():
        settings = remote_settings()
        settings.embedding_config["kwargs"]["api_base"] = "http://localhost:1234/v1"
        class NeverCalled:
            docs = {"fake": True}
            async def aquery(self, *args, **kwargs):
                pytest.fail("Local-target request must never execute")
        with pytest.raises(ValueError):
            await bridge.query_paperqa(NeverCalled(), {}, "Q", settings)
    asyncio.run(run())


def test_remote_query_deadline_is_service_error_not_refusal(monkeypatch):
    async def run():
        class HungDocs:
            docs = {"fake": True}
            async def aquery(self, *args, **kwargs):
                await asyncio.sleep(10)
        monkeypatch.setattr(bridge, "REMOTE_QUERY_TIMEOUT_SECONDS", 0.01)
        result = await bridge.query_paperqa(HungDocs(), {}, "Q", remote_settings(),
                                            embedding_model=FakeRemote(name="openai/fake"))
        assert result["decision"] == "runtime_error" and result["error_type"] == "RemoteTimeout"
        assert not result["counts_as_refusal"] and result["native_session"] is None
    asyncio.run(run())
