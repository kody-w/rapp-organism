"""python3 -m rapp_catalog: dependency-free public catalog tooling."""

import argparse
import csv
import json
from pathlib import Path
import sqlite3
import sys

from . import __version__
from .common import CatalogError, read_config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Verify all public source locks, paths, coverage and history")
    build_parser = commands.add_parser("build", help="Build SQLite and static Pages artifacts offline")
    build_parser.add_argument("--output", default="site")
    build_parser.add_argument("--freshness", help="Optional untracked successful/failed poll receipt")
    query_parser = commands.add_parser("query", help="Run a bounded read-only local SQLite query")
    query_parser.add_argument("sql")
    query_parser.add_argument("--database", default="site/catalog.sqlite")
    query_parser.add_argument("--limit", type=int, default=1000)
    query_parser.add_argument("--format", choices=("json", "csv"), default="json")
    scrape_parser = commands.add_parser("scrape", help="Poll only public GitHub API endpoints")
    scrape_parser.add_argument("--cache", default=".cache/github.json")
    scrape_parser.add_argument("--receipt", default=".cache/last-run.json")
    scrape_parser.add_argument("--refresh-heads", action="store_true")
    scrape_parser.add_argument("--max-requests", type=int, default=1000)
    seed_parser = commands.add_parser("import-seed", help="Project capture metadata; not needed by clones/CI")
    for name in ("scope", "genome", "dependencies", "matrix", "receipt"):
        seed_parser.add_argument("--" + name, required=True)
    seed_parser.add_argument("--generation", default="g0002")
    seed_parser.add_argument("--parent", default="g0001")
    seed_parser.add_argument("--expect-matrix-sha256")
    verify_parser = commands.add_parser("verify-distribution", help="Stream and hash staged public release chunks")
    download_parser = commands.add_parser("download-distribution", help="Download/resume verified public release parts")
    assemble_parser = commands.add_parser("assemble-distribution", help="Reassemble verified parts without extraction")
    local_verify_parser = commands.add_parser("verify-carrier", help="Verify a local carrier's complete size and SHA-256")
    for transfer_parser in (verify_parser, download_parser, assemble_parser, local_verify_parser):
        transfer_parser.add_argument("--generation", default="g0002")
        transfer_parser.add_argument("--manifest", help="Optional local copy of the public distribution index")
        transfer_parser.add_argument("--max-bytes", type=int, default=32 * 1024 ** 3)
        transfer_parser.add_argument("--max-seconds", type=int, default=6 * 3600)
    for transfer_parser in (download_parser, assemble_parser):
        transfer_parser.add_argument("--directory", help="Managed cache below downloads/ (default downloads/GENERATION)")
        transfer_parser.add_argument("--repair", action="store_true", help="Repair corrupt owned files; never extract contents")
    local_verify_parser.add_argument("--file", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            from .build import validate
            bundle = validate(".")
            result = {"valid": True, "metadata_bytes": bundle["metadata_bytes"],
                      "generations": [manifest["counts"] for manifest, _ in bundle["generations"]],
                      "observation_events": len(bundle["events"])}
        elif args.command == "build":
            from .build import build
            result = build(".", args.output, args.freshness)
        elif args.command == "query":
            from .build import query, spreadsheet_cell
            result = query(args.database, args.sql, args.limit)
            if args.format == "csv":
                writer = csv.writer(sys.stdout, lineterminator="\n")
                writer.writerow(result["columns"])
                writer.writerows([spreadsheet_cell(cell) for cell in row] for row in result["rows"])
                return 0
        elif args.command == "scrape":
            from .scrape import scrape
            result = scrape(".", args.cache, args.receipt, refresh_heads=args.refresh_heads,
                            max_requests=args.max_requests)
        elif args.command == "import-seed":
            from .seed import import_seed
            result = import_seed(".", scope=args.scope, genome=args.genome, dependencies=args.dependencies,
                                 matrix=args.matrix, receipt=args.receipt, generation=args.generation,
                                 parent=None if args.parent == "none" else args.parent,
                                 expect_matrix_sha256=args.expect_matrix_sha256)
        elif args.command == "verify-distribution":
            from .distribution import verify_distribution
            result = verify_distribution(".", read_config("."), args.generation, args.manifest,
                                         max_bytes=args.max_bytes, max_seconds=args.max_seconds)
        elif args.command in {"download-distribution", "assemble-distribution"}:
            from .downloads import assemble_distribution, download_distribution
            operation = download_distribution if args.command == "download-distribution" else assemble_distribution
            result = operation(".", args.generation, args.directory, args.manifest, repair=args.repair,
                               max_bytes=args.max_bytes, max_seconds=args.max_seconds)
        elif args.command == "verify-carrier":
            from .downloads import verify_carrier
            result = verify_carrier(".", args.file, args.generation, args.manifest,
                                    max_bytes=args.max_bytes, max_seconds=args.max_seconds)
        else:
            raise CatalogError("unknown_command")
        print(json.dumps(result, sort_keys=True, ensure_ascii=True, indent=2))
        return 0
    except KeyboardInterrupt:
        print('{"error":"operation_interrupted"}', file=sys.stderr)
        return 130
    except (CatalogError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        code = error.code if isinstance(error, CatalogError) else "local_validation_error"
        print(json.dumps({"error": code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
