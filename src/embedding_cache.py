"""Persistent cache of remote embedding vectors only; no models or pickle files."""
from __future__ import annotations

import hashlib
import asyncio
import time
import importlib.metadata
import json
import math
from pathlib import Path
import sqlite3

from lmi import EmbeddingModel, EmbeddingModes
from pydantic import Field


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _public_config(value):
    if isinstance(value, dict):
        return {str(k): _public_config(v) for k, v in value.items()
                if str(k).lower() not in {"api_key", "token", "access_token", "secret",
                                           "password", "authorization", "credential_envs"}}
    if isinstance(value, list):
        return [_public_config(v) for v in value]
    return value


def embedding_fingerprint(settings):
    versions = {}
    for package in ("paper-qa", "fhlmi", "litellm"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unknown"
    return _hash(dict(schema=1, embedding=settings.embedding,
                      config=_public_config(settings.embedding_config or {}), versions=versions))


def validate_vector(vector, ndim=None):
    if (not isinstance(vector, list) or not vector or
        any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in vector) or not any(v != 0 for v in vector)):
        raise ValueError("Remote embedding contains invalid or zero vector values")
    if ndim is not None and len(vector) != ndim:
        raise ValueError("Remote embedding dimension changed; clear cache or select a versioned model")
    return [float(v) for v in vector]


class CachedRemoteEmbedding(EmbeddingModel):
    """Wrap the public LMI embedding interface with a SQLite vector cache."""
    wrapped: EmbeddingModel = Field(exclude=True)
    cache_path: Path = Field(exclude=True)
    fingerprint: str
    source_fingerprints: dict[str, str] = Field(default_factory=dict, exclude=True)
    enabled: bool = True
    remote_batch_size: int = Field(default=8,ge=1,le=64,exclude=True)
    request_interval_seconds: float = Field(default=1.0,ge=0,le=60,exclude=True)
    last_remote_at: float = Field(default=0,exclude=True)
    mode: EmbeddingModes = EmbeddingModes.DOCUMENT
    stats: dict[str, int] = Field(default_factory=lambda: dict(hits=0, misses=0,
        remote_requests=0, remote_texts=0, invalid_entries=0,remote_errors=0,rate_limit_retries=0), exclude=True)

    def set_mode(self, mode):
        self.mode = EmbeddingModes(mode)
        self.wrapped.set_mode(self.mode)

    def _connect(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.cache_path, timeout=30)
        con.execute("CREATE TABLE IF NOT EXISTS vectors (fingerprint TEXT, cache_key TEXT, "
                    "dimension INTEGER, vector_json TEXT, PRIMARY KEY (fingerprint, cache_key))")
        con.execute("CREATE TABLE IF NOT EXISTS dimensions (fingerprint TEXT PRIMARY KEY, dimension INTEGER)")
        return con

    async def embed_documents(self, texts):
        if not texts:
            return []
        if any(not isinstance(t, str) for t in texts):
            raise ValueError("Embedding input must be text")
        keys = [_hash(dict(mode=self.mode.value, text_sha=hashlib.sha256(t.encode()).hexdigest(),
                          source=self.source_fingerprints.get(t, "query"))) for t in texts]
        result = [None] * len(texts)
        dimension = self.wrapped.ndim or self.ndim
        if self.enabled:
            with self._connect() as con:
                row = con.execute("SELECT dimension FROM dimensions WHERE fingerprint=?",
                                  (self.fingerprint,)).fetchone()
                if row:
                    if dimension and dimension != row[0]:
                        raise ValueError("Cached embedding dimension does not match remote model configuration")
                    dimension = row[0]
                for i, key in enumerate(keys):
                    row = con.execute("SELECT dimension,vector_json FROM vectors WHERE fingerprint=? AND cache_key=?",
                                      (self.fingerprint, key)).fetchone()
                    if row:
                        try:
                            vector = validate_vector(json.loads(row[1]), dimension)
                            if len(vector) != row[0]:
                                raise ValueError("Invalid cached dimension")
                            dimension = dimension or len(vector)
                            result[i] = vector
                            self.stats["hits"] += 1
                        except (ValueError, TypeError, json.JSONDecodeError):
                            self.stats["invalid_entries"] += 1
                            con.execute("DELETE FROM vectors WHERE fingerprint=? AND cache_key=?", (self.fingerprint, key))
        missing_keys = list(dict.fromkeys(keys[i] for i, v in enumerate(result) if v is None))
        if missing_keys:
            missing_texts = [texts[keys.index(key)] for key in missing_keys]
            self.stats["misses"] += sum(v is None for v in result)
            self.wrapped.set_mode(self.mode)
            valid = {}
            for start in range(0,len(missing_keys),self.remote_batch_size):
                batch_keys=missing_keys[start:start+self.remote_batch_size]
                batch_texts=missing_texts[start:start+self.remote_batch_size]
                for attempt in range(2):
                    delay=self.request_interval_seconds-(time.monotonic()-self.last_remote_at)
                    if delay>0:await asyncio.sleep(delay)
                    self.stats['remote_requests']+=1;self.stats['remote_texts']+=len(batch_texts)
                    try:vectors=await self.wrapped.embed_documents(batch_texts)
                    except Exception as exc:
                        self.last_remote_at=time.monotonic();self.stats['remote_errors']+=1
                        if attempt==0 and (getattr(exc,'status_code',None)==429 or type(exc).__name__=='RateLimitError'):
                            self.stats['rate_limit_retries']+=1
                            await asyncio.sleep(4)
                            continue
                        raise
                    self.last_remote_at=time.monotonic()
                    break
                if len(vectors)!=len(batch_texts):raise ValueError('Remote embedding response count differs from input count')
                batch={}
                for key,vector in zip(batch_keys,vectors):
                    vector=validate_vector(vector,dimension);dimension=dimension or len(vector);batch[key]=vector
                if self.enabled:
                    with self._connect() as con:
                        existing=con.execute('SELECT dimension FROM dimensions WHERE fingerprint=?',(self.fingerprint,)).fetchone()
                        if existing and existing[0]!=dimension:raise ValueError('Concurrent remote embedding dimension changed')
                        con.execute('INSERT OR IGNORE INTO dimensions VALUES (?,?)',(self.fingerprint,dimension))
                        con.executemany('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)',[
                            (self.fingerprint,key,len(v),json.dumps(v,allow_nan=False)) for key,v in batch.items()])
                self.ndim=dimension
                valid.update(batch)
            for i, key in enumerate(keys):
                if result[i] is None:
                    result[i] = valid[key]
        self.ndim = dimension
        return result
