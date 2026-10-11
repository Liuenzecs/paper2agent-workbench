"""A portable PDF evidence library. Retrieval never implies scientific validation."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
from pathlib import Path
import re
import shutil
import time
import threading
import unicodedata
import uuid
from datetime import datetime, timezone

import pymupdf

DOCLING_LOCK = threading.Lock()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    # English paper vocabulary; CJK bigrams allow literal Chinese corpus lookup.
    words = re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(run[i:i+2] for i in range(max(1, len(run)-1)))
    return words


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class PaperLibrary:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def ingest(self, pdf_path: str, title: str = "", source_url: str = "", backend: str = "pymupdf") -> dict:
        source = Path(pdf_path).resolve(strict=True)
        if backend not in ("pymupdf", "docling"):
            raise ValueError("backend must be pymupdf or docling")
        if not source.is_file() or source.read_bytes()[:5] != b"%PDF-":
            raise ValueError("Input must be a real PDF file")
        digest = sha256(source)
        with pymupdf.open(source) as pdf:
            if pdf.needs_pass:
                raise ValueError("Password protected PDF is unsupported")
            if not 1 <= len(pdf) <= 500:
                raise ValueError("PDF must have 1..500 pages")
            page_count = len(pdf)
            source_title = title.strip() or pdf.metadata.get("title") or source.stem
        paper_id = digest[:24]
        directory = self.root / paper_id
        # Build in a unique staging directory; a failed parser cannot corrupt a prior bundle.
        stage = self.root / f".staging-{uuid.uuid4().hex}"
        stage.mkdir()
        started = time.perf_counter()
        try:
            shutil.copyfile(source, stage / "source.pdf")
            if backend == "pymupdf":
                blocks = []
                with pymupdf.open(stage / "source.pdf") as pdf:
                    for page in pdf:
                        for i, b in enumerate(page.get_text("blocks", sort=True)):
                            if b[6] != 0 or not b[4].strip():
                                continue
                            blocks.append(dict(id=f"p{page.number+1}-b{i}", text=b[4].strip(), label="text",
                                               locations=[dict(page=page.number+1, bbox=list(b[:4]),
                                                               origin="top-left", units="PDF points")]))
            else:
                with DOCLING_LOCK:
                    blocks = self._docling(stage)
            if not blocks:
                raise ValueError("No text extracted; use OCR-enabled processing for scanned PDFs")
            extracted_pages = {l["page"] for b in blocks for l in b["locations"]}
            replacement_pages = sorted({l["page"] for b in blocks if "\ufffd" in b["text"] for l in b["locations"]})
            parser_warnings = ((stage / "parser-warnings.txt").read_text(encoding="utf-8").splitlines()
                               if (stage / "parser-warnings.txt").exists() else [])
            bundle = dict(schema_version=1, paper_id=paper_id, title=source_title, source_url=source_url,
                          source_sha256=digest, source_file="source.pdf", page_count=page_count,
                          parser=dict(name=backend, version=importlib.metadata.version(backend)),
                          parsed_at=datetime.now(timezone.utc).isoformat(),
                          elapsed_seconds=time.perf_counter()-started, blocks=blocks,
                          warnings=([f"Page {p} has no searchable text" for p in range(1,page_count+1) if p not in extracted_pages] +
                                    [f"Page {p} contains replacement glyphs; inspect PDF for formulas/symbols" for p in replacement_pages] +
                                    parser_warnings),
                          validation=dict(mechanical="source_hash_and_page_bounds_checked", knowledge="unreviewed",
                                          execution="not_applicable", science="not_reproduced"))
            for b in blocks:
                for location in b["locations"]:
                    if not 1 <= location["page"] <= page_count:
                        raise ValueError("Parser produced invalid page provenance")
            write_json(stage / "bundle.json", bundle)
            write_json(stage / "integrity.json", {p.name: sha256(p) for p in stage.iterdir() if p.is_file()})
            directory.mkdir(exist_ok=True)
            # Backend snapshots are immutable; a rerun with changed parser/title remains distinct.
            final = directory / f"{backend}-{sha256(stage / 'bundle.json')[:12]}"
            stage.rename(final)
            return dict(paper_id=paper_id, bundle_path=str(final / "bundle.json"), backend=backend,
                        page_count=page_count, block_count=len(blocks), elapsed_seconds=bundle["elapsed_seconds"],
                        validation=bundle["validation"], warnings=bundle["warnings"])
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    @staticmethod
    def _docling(stage: Path) -> list[dict]:
        # Honour Windows' trusted corporate certificates; retain TLS verification.
        import truststore
        truststore.inject_into_ssl()
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, AcceleratorOptions, AcceleratorDevice
        from docling.document_converter import DocumentConverter, PdfFormatOption
        options = PdfPipelineOptions()
        options.do_ocr = False  # digitally born AI PDFs; explicit scope, not automatic OCR.
        options.do_table_structure = True
        options.accelerator_options = AcceleratorOptions(num_threads=2, device=AcceleratorDevice.CPU)
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        warning_log = logging.FileHandler(stage / "parser-warnings.txt", encoding="utf-8")
        warning_log.setLevel(logging.WARNING)
        warning_log.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        logging.getLogger().addHandler(warning_log)
        try:
            result = converter.convert(stage / "source.pdf")
        finally:
            logging.getLogger().removeHandler(warning_log)
            warning_log.close()
        if result.status.value != "success":
            raise RuntimeError(f"Docling conversion was not complete: {result.status}")
        document = result.document
        document.save_as_json(stage / "docling.json")
        document.save_as_markdown(stage / "docling.md")
        blocks = []
        extraction_warnings = []
        for i, (item, _) in enumerate(document.iterate_items()):
            label = str(getattr(item, "label", "")).split(".")[-1]
            text = getattr(item, "text", "")
            representation = "parsed_text"
            if not text and getattr(item, "orig", ""):
                text = item.orig
                representation = "original_flattened_text"
            if label == "table":
                text = item.export_to_markdown(doc=document)
                representation = "unreviewed_table_markdown"
            provenance = getattr(item, "prov", [])
            if label == "formula":
                pages = ",".join(str(p.page_no) for p in provenance)
                extraction_warnings.append(f"Formula item {i} on PDF page {pages}: flattened text only; inspect source page image")
            if not text or not provenance:
                continue
            blocks.append(dict(id=f"item-{i}", label=label, text=text, representation=representation,
                               requires_visual_check=label in {"formula", "table"}, locations=[
                dict(page=p.page_no, bbox=[p.bbox.l,p.bbox.t,p.bbox.r,p.bbox.b],
                     origin=p.bbox.coord_origin.value, units="PDF points") for p in provenance]))
        if extraction_warnings:
            with (stage / "parser-warnings.txt").open("a",encoding="utf-8") as stream:
                stream.write("\n".join(extraction_warnings)+"\n")
        return blocks

    def bundles(self, backend: str = "pymupdf") -> list[tuple[Path, dict]]:
        if backend not in ("pymupdf", "docling"):
            raise ValueError("Unsupported backend")
        selected = {}
        for path in sorted(self.root.glob(f"*/{backend}-*/bundle.json")):
            bundle = self.load(path)
            key = bundle["paper_id"]
            if key not in selected or bundle["parsed_at"] > selected[key][1]["parsed_at"]:
                selected[key] = (path, bundle)
        return list(selected.values())

    def load(self, path: str | Path) -> dict:
        path = Path(path).resolve(strict=True)
        if not path.is_relative_to(self.root):
            raise ValueError("Bundle must be inside this library")
        integrity = json.loads((path.parent / "integrity.json").read_text(encoding="utf-8"))
        if not {"bundle.json", "source.pdf"}.issubset(integrity):
            raise ValueError("Incomplete integrity manifest")
        for name, expected in integrity.items():
            artifact = path.parent / name
            if Path(name).name != name or not artifact.is_file() or sha256(artifact) != expected:
                raise ValueError(f"Artifact integrity failed: {name}")
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if bundle["source_sha256"] != sha256(path.parent / "source.pdf"):
            raise ValueError("Source hash mismatch")
        return bundle

    def inventory(self, backend: str = "pymupdf") -> list[dict]:
        return [dict(paper_id=b["paper_id"], title=b["title"], pages=b["page_count"], parser=b["parser"],
                     source_sha256=b["source_sha256"], validation=b["validation"], bundle_path=str(p))
                for p,b in self.bundles(backend)]

    def read_page(self, paper_id: str, page: int, backend: str = "pymupdf", render: bool = True) -> dict:
        if not isinstance(page,int) or isinstance(page,bool):
            raise ValueError("page must be an integer PDF page number")
        for path,bundle in self.bundles(backend):
            if bundle["paper_id"] == paper_id:
                if not 1 <= page <= bundle["page_count"]:
                    raise ValueError("page is outside this PDF")
                result = dict(paper_id=paper_id,title=bundle["title"],page=page,
                            source_sha256=bundle["source_sha256"],pdf_path=str(path.parent / "source.pdf"),
                            parser=bundle["parser"],validation=bundle["validation"],warnings=bundle["warnings"],
                            blocks=[b for b in bundle["blocks"] if any(l["page"]==page for l in b["locations"])])
                if render:
                    dest = self.root / "page_views" / uuid.uuid4().hex
                    dest.mkdir(parents=True)
                    with pymupdf.open(path.parent / "source.pdf") as pdf:
                        pix = pdf[page-1].get_pixmap(matrix=pymupdf.Matrix(1.5,1.5))
                        pix.save(dest / "source-page.png")
                    result["source_page_image"] = str(dest / "source-page.png")
                return result
        raise ValueError("Unknown paper_id")

    def search(self, query: str, top_k: int = 5, paper_ids: list[str] | None = None,
               backend: str = "pymupdf") -> dict:
        if not isinstance(top_k, int) or isinstance(top_k,bool) or not 1 <= top_k <= 20:
            raise ValueError("top_k must be 1..20")
        terms = tokens(query)
        if not terms or not query.strip():
            raise ValueError("Query must have searchable text")
        bundles = self.bundles(backend)
        available = {b["paper_id"] for _,b in bundles}
        if paper_ids is not None and (not paper_ids or set(paper_ids)-available):
            raise ValueError("paper_ids contains unknown IDs or is empty")
        chunks = []
        for path,bundle in bundles:
            if paper_ids is not None and bundle["paper_id"] not in paper_ids:
                continue
            for block in bundle["blocks"]:
                # Preserve complete original blocks; do not cut equations/tables in half.
                chunks.append((path,bundle,block))
        if not chunks:
            return dict(query=query, mode="extractive_evidence", hits=[], status="empty_library")
        corpus = [tokens(b["text"]) for _,_,b in chunks]
        # Positive-IDF BM25L avoids negative common-term scores in tiny libraries.
        from rank_bm25 import BM25L
        scores = BM25L(corpus).get_scores(terms)
        order = sorted(range(len(chunks)), key=lambda i: float(scores[i]), reverse=True)
        hits = []
        for i in order:
            if not set(terms).intersection(corpus[i]):
                continue
            path, bundle, block = chunks[i]
            hits.append(dict(paper_id=bundle["paper_id"], title=bundle["title"], block_id=block["id"],
                             score=float(scores[i]), text=block["text"], locations=block["locations"],
                             source_sha256=bundle["source_sha256"], source_url=bundle["source_url"],
                             pdf_path=str(path.parent / "source.pdf"), bundle_sha256=sha256(path),
                             parser=bundle["parser"], knowledge_status=bundle["validation"]["knowledge"]))
            hits[-1]["text_warnings"] = ["Replacement glyphs present; inspect source PDF"] if "\ufffd" in block["text"] else []
            if block.get("requires_visual_check"):
                hits[-1]["text_warnings"].append("Unreviewed formula/table representation; inspect source page image")
            if len(hits) == top_k:
                break
        return dict(query=query, mode="extractive_evidence", status="ok" if hits else "no_matching_terms", hits=hits,
                    limitation="Lexical retrieval only. Scores are ranking values, not factual confidence. "
                               "No answer generation, cross-language translation or formula/image reasoning.")

    def export_evidence(self, query: str, top_k: int = 5, paper_ids: list[str] | None = None,
                        backend: str = "pymupdf") -> dict:
        result = self.search(query, top_k, paper_ids, backend)
        dest = self.root / "queries" / uuid.uuid4().hex
        dest.mkdir(parents=True)
        write_json(dest / "evidence.json", result)
        lines = [f"# Evidence: {query}", "", result.get("limitation", ""), ""]
        for hit in result["hits"]:
            pages = ", ".join(str(l["page"]) for l in hit["locations"])
            lines += [f"## {hit['title']} — PDF page {pages}", "", hit["text"], "",
                      f"Source SHA-256: {hit['source_sha256']}", "", f"Block: {hit['block_id']}", ""]
        (dest / "evidence.md").write_text("\n".join(lines), encoding="utf-8")
        return dict(**result, output_json=str(dest / "evidence.json"), output_markdown=str(dest / "evidence.md"))
