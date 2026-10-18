#!/usr/bin/env python3
"""Exports internal result documents into the public submission schema (SPEC §11).

bench/run.py's results.json and bench/mc/run.py's report stay the source of truth; this module only
transforms them into schema/result.schema.json's shape and writes jvms/<name>/<timestamp>.json.
"""

import argparse
import copy
import json
import platform
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common
import run
from common import ROOT, write_json

SCHEMA_VERSION = 1
SCHEMA = ROOT / "schema" / "result.schema.json"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float),
}


def validate(instance, schema=None, root=None, path="$"):
    """Every violation of schema by instance, as human-readable strings (empty iff valid)."""
    if schema is None:
        schema = json.loads(SCHEMA.read_text())
    root = root or schema
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        schema = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
    errors = []
    expected = schema.get("type")
    if expected:
        python = TYPES[expected]
        is_bool = isinstance(instance, bool)
        if not isinstance(instance, python) or (is_bool and expected in ("integer", "number")):
            return [f"{path}: expected {expected}, got {type(instance).__name__}"]
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")
    if isinstance(instance, str):
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match {schema['pattern']}")
        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(instance.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{path}: not an ISO-8601 date-time")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must exceed {schema['exclusiveMinimum']}")
    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing {name}")
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for name, value in instance.items():
            if name in properties:
                errors += validate(value, properties[name], root, f"{path}.{name}")
            elif extra is False:
                errors.append(f"{path}: unexpected property {name}")
            elif isinstance(extra, dict):
                errors += validate(value, extra, root, f"{path}.{name}")
    if isinstance(instance, list) and "items" in schema:
        for index, value in enumerate(instance):
            errors += validate(value, schema["items"], root, f"{path}[{index}]")
    return errors


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def filename(generated_at):
    """The colon-free submission filename for a generated_at timestamp."""
    return generated_at.replace(":", "-") + ".json"


def target_block(name, **details):
    if not NAME.match(name):
        raise ValueError(f"target name {name!r} must match {NAME.pattern}")
    return {"name": name, **{k: v for k, v in details.items() if v is not None}}


def metric(summary, lower_is_better, samples=None):
    """A schema metric from rig.summarize()'s {median, ci, n}."""
    record = {"median": summary["median"], "n": summary["n"], "lower_is_better": lower_is_better}
    if summary.get("ci"):
        record["ci_low"], record["ci_high"] = summary["ci"]
    if samples is not None:
        record["samples"] = list(samples)
    return record


def export_bench(results, name, **target):
    """A submission from bench/run.py's results.json (§6.7)."""
    machine = {
        "hostname": results["machine"],
        "os": results.get("os") or platform.system().lower(),
        "arch": results.get("arch") or platform.machine(),
    }
    if results.get("jdks"):
        machine["reference_jdks"] = results["jdks"]
    details = {"is_java_launcher": results.get("target_is_java"), "binary_sha256": results.get("target_sha256")}
    submission = {
        "schema_version": SCHEMA_VERSION,
        "target": target_block(name, **details, **target),
        "machine": machine,
        "generated_at": now(),
        "harness": {key: results[key] for key in ("target_toolchain", "benchmarks") if key in results},
    }
    computed = copy.deepcopy(results)
    summary = run.vs_hotspot(computed)
    benchmarks = {
        name: entry["vs_hotspot"] for name, entry in computed["benchmarks"].items() if entry.get("vs_hotspot")
    }
    relative = {key: value for key, value in summary.items() if value}
    if benchmarks:
        relative["benchmarks"] = benchmarks
    if relative.get("groups") or relative.get("startup"):
        submission["vs_hotspot"] = relative
    startup = (results.get("startup") or {}).get("samples_ms")
    if startup:
        submission["startup_ms"] = {
            "median": statistics.median(startup), "n": len(startup), "lower_is_better": True, "samples": startup,
        }
    return submission


def export_mc(report, name, **target):
    """A submission from bench/mc/run.py's report (§9.4)."""
    machine = dict(report["machine"])
    if not machine.get("reference_jdks"):
        machine.pop("reference_jdks", None)
    identity = report.get("engines", {}).get("target")
    scenarios = {}
    for scenario, data in report.get("scenarios", {}).items():
        samples = data.get("samples", [])
        exported = {"target_metrics": {}, "failed_runs": sum(
            1 for s in samples if s["engine"] == "target" and "error" in s
        )}
        references, verdicts = {}, {}
        for metric_name, summary in data.get("summary", {}).items():
            lower = summary["lower_is_better"]
            for engine, stats in summary["engines"].items():
                values = [s[metric_name] for s in samples if s["engine"] == engine and "error" not in s and metric_name in s]
                record = metric(stats, lower, values)
                if engine == "target":
                    exported["target_metrics"][metric_name] = record
                else:
                    references.setdefault(engine, {})[metric_name] = record
            verdict = summary.get("verdict")
            if verdict and verdict.get("ratio"):
                verdicts[metric_name] = {
                    "baseline": verdict["best_baseline"], "win": verdict["win"], "ratio": verdict["ratio"],
                }
        relative = {}
        for metric_name, summary in data.get("summary", {}).items():
            stats = summary["engines"]
            if metric_name in common.NOT_COMPARED:
                continue
            record = summary.get("vs_hotspot") or (
                common.relative(stats["target"]["median"], stats["hotspot-g1"]["median"], summary["lower_is_better"])
                if "target" in stats and "hotspot-g1" in stats else None
            )
            if record:
                relative[metric_name] = record
        if relative:
            exported["vs_hotspot"] = relative
        if references:
            exported["reference_metrics"] = references
        if verdicts:
            exported["verdicts"] = verdicts
        scenarios[scenario] = exported
    return {
        "schema_version": SCHEMA_VERSION,
        "target": target_block(name, **({"version": identity} if identity else {}), **target),
        "machine": machine,
        "generated_at": report.get("generated_at") or now(),
        "run": {"runs": report["runs"], "seed": report["seed"]},
        "scenarios": scenarios,
    }


def merge(*submissions):
    """One submission from a bench export and/or an mc export of the same target and machine."""
    merged = dict(submissions[0])
    for other in submissions[1:]:
        if other["target"]["name"] != merged["target"]["name"]:
            raise ValueError("cannot merge submissions for different targets")
        merged["target"] = {**other["target"], **merged["target"]}
        merged["machine"] = {**other["machine"], **merged["machine"]}
        for key in ("run", "scenarios", "startup_ms", "harness", "vs_hotspot"):
            if key in other and key not in merged:
                merged[key] = other[key]
    merged["generated_at"] = now()
    return merged


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write a jvms/<name>/<timestamp>.json submission.")
    parser.add_argument("--name", required=True, help="target name, e.g. my-aot-jvm")
    parser.add_argument("--bench", type=Path, help="bench/run.py results.json")
    parser.add_argument("--mc", type=Path, help="bench/mc/run.py results.json")
    parser.add_argument("--version", help="target build identity (overrides the mc report's)")
    parser.add_argument("--description")
    parser.add_argument("--source-url")
    parser.add_argument("--harness-commit")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "jvms")
    args = parser.parse_args(argv)
    if not args.bench and not args.mc:
        parser.error("pass --bench and/or --mc")
    details = {"version": args.version, "description": args.description, "source_url": args.source_url}
    details = {k: v for k, v in details.items() if v is not None}
    parts = []
    if args.bench:
        parts.append(export_bench(json.loads(args.bench.read_text()), args.name, **details))
    if args.mc:
        exported = export_mc(json.loads(args.mc.read_text()), args.name)
        exported["target"].update(details)
        parts.append(exported)
    submission = merge(*parts) if len(parts) > 1 else parts[0]
    if args.harness_commit:
        submission["harness_commit"] = args.harness_commit
    if "vs_hotspot" not in submission and not any("vs_hotspot" in s for s in submission.get("scenarios", {}).values()):
        print("warning: no HotSpot G1 comparison in this submission; the results site cannot rank it", file=sys.stderr)
    errors = validate(submission)
    if errors:
        sys.exit("submission does not match the schema:\n" + "\n".join(errors))
    path = args.out_dir / args.name / filename(submission["generated_at"])
    write_json(path, submission)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
