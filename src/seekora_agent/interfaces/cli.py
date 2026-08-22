from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

from ..domain.models import SearchQuery
from ..config.defaults import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_CACHE_RELATIVE_PATH,
    DEFAULT_EMBEDDING_DEVICE,
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_MODEL_ID,
    DEFAULT_EMBEDDING_REVISION,
    DEFAULT_QUERY_INSTRUCTION,
    DEFAULT_VECTOR_INDEX_RELATIVE_PATH,
)
from ..evaluation.metrics import evaluate
from ..evaluation.recall_comparison import (
    ReciprocalRankFusionSearch,
    compare_recall,
    select_qwen_weight,
    validate_golden_catalog,
)
from ..infrastructure.catalog import (
    DEFAULT_CATALOG_PATH,
    catalog_snapshot_sha256,
    inspect_quality,
    load_golden_queries,
    load_items,
    resolve_catalog_path,
)
from ..infrastructure.kuaisearch import convert_kuaisearch_items
from ..infrastructure.embeddings.qwen3 import Qwen3Embedding
from ..infrastructure.search.bm25 import BM25Baseline
from ..infrastructure.search.semantic import InMemorySemanticIndex
from ..infrastructure.search.vector_index import EmbeddingSemanticIndex
from ..infrastructure.search.vector_index import synchronize_vector_index
from ..infrastructure.search.sqlite_vector_index import SQLiteVectorIndex


def sqlite_vector_index_path(value: str) -> str:
    """CLI 只接受语义明确的 SQLite 索引文件名。"""
    if Path(value).suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
        raise argparse.ArgumentTypeError(
            "vector index output must use .sqlite, .sqlite3, or .db"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seekora Agent development utilities")
    commands = parser.add_subparsers(dest="command", required=True)

    quality = commands.add_parser("quality", help="inspect catalog data quality")
    quality.add_argument("--catalog", default=str(DEFAULT_CATALOG_PATH))

    search = commands.add_parser("search", help="run a baseline search")
    search.add_argument("--catalog", default=str(DEFAULT_CATALOG_PATH))
    search.add_argument("--query", required=True)
    search.add_argument("--tenant", default="demo")
    search.add_argument("--permission", action="append", default=["public"])
    search.add_argument("--top-k", type=int, default=10)

    evaluation = commands.add_parser("evaluate", help="evaluate the baseline")
    evaluation.add_argument("--catalog", default=str(DEFAULT_CATALOG_PATH))
    evaluation.add_argument("--golden", required=True)
    evaluation.add_argument("--top-k", type=int, default=10)
    evaluation.add_argument("--output")

    comparison = commands.add_parser(
        "compare-recall",
        help="compare BM25+TF-IDF with BM25+Qwen on a fixed golden set",
    )
    comparison.add_argument("--catalog", default=str(DEFAULT_CATALOG_PATH))
    comparison.add_argument("--golden", required=True)
    comparison.add_argument(
        "--vector-index",
        type=sqlite_vector_index_path,
        default=DEFAULT_VECTOR_INDEX_RELATIVE_PATH,
    )
    comparison.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL_ID)
    comparison.add_argument("--revision", default=DEFAULT_EMBEDDING_REVISION)
    comparison.add_argument("--dimension", type=int, default=DEFAULT_EMBEDDING_DIMENSION)
    comparison.add_argument("--query-instruction", default=DEFAULT_QUERY_INSTRUCTION)
    comparison.add_argument("--device", default=DEFAULT_EMBEDDING_DEVICE)
    comparison.add_argument("--cache-dir", default=DEFAULT_EMBEDDING_CACHE_RELATIVE_PATH)
    comparison.add_argument("--top-k", type=int, default=10)
    comparison.add_argument("--runs", type=int, default=3)
    comparison.add_argument("--p95-limit-ms", type=float, default=2_000.0)
    comparison.add_argument("--min-query-count", type=int, default=10)
    comparison.add_argument("--qwen-weight", type=float, default=1.0)
    comparison.add_argument(
        "--development-golden",
        help="optional development set used only to select Qwen RRF weight",
    )
    comparison.add_argument(
        "--candidate-qwen-weight",
        type=float,
        action="append",
        dest="candidate_qwen_weights",
    )
    comparison.add_argument("--output")

    kuaisearch = commands.add_parser(
        "prepare-kuaisearch", help="filter and convert a KuaiSearch item catalog"
    )
    kuaisearch.add_argument("--source", required=True)
    kuaisearch.add_argument("--output", required=True)
    kuaisearch.add_argument("--report")
    kuaisearch.add_argument("--category-level1-id", type=int, default=30)
    kuaisearch.add_argument("--limit", type=int, default=50_000)
    kuaisearch.add_argument("--seed", type=int, default=20260819)
    kuaisearch.add_argument("--tenant", default="demo")
    kuaisearch.add_argument(
        "--exclude-product-type",
        action="append",
        dest="excluded_product_types",
        help="exclude a classified product type; defaults to phone_case",
    )

    vector_index = commands.add_parser(
        "build-vector-index",
        help="build or incrementally update a SQLite embedding index",
    )
    vector_index.add_argument("--catalog", default=str(DEFAULT_CATALOG_PATH))
    vector_index.add_argument(
        "--output",
        type=sqlite_vector_index_path,
        default=DEFAULT_VECTOR_INDEX_RELATIVE_PATH,
    )
    vector_index.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL_ID)
    vector_index.add_argument("--revision", default=DEFAULT_EMBEDDING_REVISION)
    vector_index.add_argument("--dimension", type=int, default=DEFAULT_EMBEDDING_DIMENSION)
    vector_index.add_argument("--query-instruction", default=DEFAULT_QUERY_INSTRUCTION)
    vector_index.add_argument("--device", default=DEFAULT_EMBEDDING_DEVICE)
    vector_index.add_argument(
        "--cache-dir", default=DEFAULT_EMBEDDING_CACHE_RELATIVE_PATH
    )
    vector_index.add_argument("--batch-size", type=int, default=DEFAULT_EMBEDDING_BATCH_SIZE)
    vector_index.add_argument("--rebuild", action="store_true")
    vector_index.add_argument(
        "--allow-download",
        action="store_true",
        help="allow sentence-transformers to download model files",
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if arguments.command == "prepare-kuaisearch":
        report = convert_kuaisearch_items(
            arguments.source,
            arguments.output,
            category_level1_id=arguments.category_level1_id,
            limit=arguments.limit,
            seed=arguments.seed,
            tenant_id=arguments.tenant,
            excluded_product_types=(
                tuple(arguments.excluded_product_types)
                if arguments.excluded_product_types
                else ("phone_case",)
            ),
        ).as_dict()
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        print(rendered)
        if arguments.report:
            path = Path(arguments.report)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered + "\n", encoding="utf-8")
        return 0
    # 索引
    if arguments.command == "build-vector-index":
        # 加载商品
        catalog_path = resolve_catalog_path(arguments.catalog)
        items = load_items(catalog_path)
        # 创建Qwen适配器
        embedding = Qwen3Embedding(
            arguments.model,
            revision=arguments.revision,
            dimension=arguments.dimension,
            query_instruction=arguments.query_instruction,
            device=arguments.device,
            cache_dir=arguments.cache_dir,
            # 默认只读取本地权重，防止开发命令意外访问外网或下载大文件。
            local_files_only=not arguments.allow_download,
        )
        output = Path(arguments.output)
        if output.exists() and not arguments.rebuild:
            index = SQLiteVectorIndex.load(
                output,
                expected_embedding_version=embedding.model_version,
                expected_dimension=embedding.dimension,
            )
        else:
            index = SQLiteVectorIndex.create(
                output,
                embedding_version=embedding.model_version,
                dimension=embedding.dimension,
                embedding_model_id=embedding.model_id,
                embedding_revision=embedding.model_revision,
                query_instruction=embedding.query_instruction,
                overwrite=arguments.rebuild,
            )
        last_reported = 0

        # 定义 report_progress 函数，每处理 1000 条或完成时输出一条 JSON 进度日志到 stderr。
        def report_progress(completed: int, total: int) -> None:
            nonlocal last_reported
            if completed == total or completed - last_reported >= 1_000:
                print(
                    json.dumps(
                        {"event": "vector_index.progress", "completed": completed, "total": total}
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                last_reported = completed
        # 同步索引
        try:
            report = synchronize_vector_index(
                index,
                items,
                embedding,
                batch_size=arguments.batch_size,
                catalog_snapshot_sha256=catalog_snapshot_sha256(catalog_path),
                progress=report_progress,
            )
            index.checkpoint()
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        finally:
            close = getattr(index, "close", None)
            if close is not None:
                close()
        return 0

    if arguments.command == "compare-recall":
        catalog_path = resolve_catalog_path(arguments.catalog)
        items = load_items(catalog_path)
        golden = load_golden_queries(arguments.golden)
        validate_golden_catalog(golden, {item.item_id for item in items})
        development = (
            load_golden_queries(arguments.development_golden)
            if arguments.development_golden
            else None
        )
        if development is not None:
            validate_golden_catalog(development, {item.item_id for item in items})
        elif arguments.candidate_qwen_weights:
            raise ValueError(
                "--candidate-qwen-weight requires --development-golden"
            )
        if arguments.runs < 2:
            raise ValueError("--runs must be at least 2")
        embedding = Qwen3Embedding(
            arguments.model,
            revision=arguments.revision,
            dimension=arguments.dimension,
            query_instruction=arguments.query_instruction,
            device=arguments.device,
            cache_dir=arguments.cache_dir,
            local_files_only=True,
        )
        vector_index = SQLiteVectorIndex.load(
            arguments.vector_index,
            expected_embedding_version=embedding.model_version,
            expected_dimension=embedding.dimension,
            expected_catalog_snapshot_sha256=catalog_snapshot_sha256(catalog_path),
            expected_item_count=len(items),
        )
        try:
            bm25 = BM25Baseline(items)
            baseline = ReciprocalRankFusionSearch((bm25, InMemorySemanticIndex(items)))
            embedding_search = EmbeddingSemanticIndex(items, embedding, vector_index)
            warmup_query = development[0].query if development else golden[0].query
            started = perf_counter()
            embedding_search.search(warmup_query, arguments.top_k)
            cold_start_ms = (perf_counter() - started) * 1_000
            weight_selection = None
            selected_weight = arguments.qwen_weight
            if development is not None:
                weight_selection = select_qwen_weight(
                    bm25,
                    embedding_search,
                    development,
                    arguments.candidate_qwen_weights or (0.25, 0.5, 0.75, 1.0),
                    top_k=arguments.top_k,
                )
                selected_weight = weight_selection.selected_weight
            active = ReciprocalRankFusionSearch(
                (bm25, embedding_search), weights=(1.0, selected_weight)
            )
            report = compare_recall(
                baseline,
                active,
                golden,
                top_k=arguments.top_k,
                runs=arguments.runs,
                p95_limit_ms=arguments.p95_limit_ms,
                min_query_count=arguments.min_query_count,
            ).as_dict()
            report.update({
                "catalog_snapshot_sha256": catalog_snapshot_sha256(catalog_path),
                "embedding_source_version": embedding_search.source_version,
                "qwen_rrf_weight": selected_weight,
                "cold_start_ms": round(cold_start_ms, 3),
            })
            if weight_selection is not None:
                report["development_weight_selection"] = weight_selection.as_dict()
        finally:
            vector_index.close()
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        print(rendered)
        if arguments.output:
            output_path = Path(arguments.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered + "\n", encoding="utf-8")
        return 0

    catalog_path = resolve_catalog_path(arguments.catalog)
    items = load_items(catalog_path)
    if arguments.command == "quality":
        report = inspect_quality(items).as_dict()
        report["catalog_path"] = str(catalog_path.resolve())
        report["snapshot_sha256"] = catalog_snapshot_sha256(catalog_path)
    elif arguments.command == "search":
        baseline = BM25Baseline(items)
        results = baseline.search(SearchQuery(
            text=arguments.query,
            tenant_id=arguments.tenant,
            allowed_permission_tags=tuple(arguments.permission),
        ), top_k=arguments.top_k)
        report = [{
            "item_id": result.item.item_id,
            "title": result.item.title,
            "score": round(result.score, 6),
            "reasons": list(result.reasons),
        } for result in results]
    else:
        baseline = BM25Baseline(items)
        golden = load_golden_queries(arguments.golden)
        report = evaluate(baseline, golden, arguments.top_k).as_dict()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    output = getattr(arguments, "output", None)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
