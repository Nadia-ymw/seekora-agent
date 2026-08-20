from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..domain.models import SearchQuery
from ..evaluation.metrics import evaluate
from ..infrastructure.catalog import inspect_quality, load_golden_queries, load_items
from ..infrastructure.kuaisearch import convert_kuaisearch_items
from ..infrastructure.search.bm25 import BM25Baseline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seekora Agent development utilities")
    commands = parser.add_subparsers(dest="command", required=True)

    quality = commands.add_parser("quality", help="inspect catalog data quality")
    quality.add_argument("--catalog", required=True)

    search = commands.add_parser("search", help="run a baseline search")
    search.add_argument("--catalog", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--tenant", default="demo")
    search.add_argument("--permission", action="append", default=["public"])
    search.add_argument("--top-k", type=int, default=10)

    evaluation = commands.add_parser("evaluate", help="evaluate the baseline")
    evaluation.add_argument("--catalog", required=True)
    evaluation.add_argument("--golden", required=True)
    evaluation.add_argument("--top-k", type=int, default=10)
    evaluation.add_argument("--output")

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
        ).as_dict()
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        print(rendered)
        if arguments.report:
            path = Path(arguments.report)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered + "\n", encoding="utf-8")
        return 0

    items = load_items(arguments.catalog)
    if arguments.command == "quality":
        report = inspect_quality(items).as_dict()
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
