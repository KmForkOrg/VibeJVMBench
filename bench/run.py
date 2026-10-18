#!/usr/bin/env python3
"""Builds, runs, times, caches and gates every benchmark against the target under test (SPEC §6)."""

import argparse
import json
import math
import os
import platform
import re
import socket
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common
import suites
from common import BENCH, ROOT, download, key, sha256

GROUPS = {
    "suites": BENCH / "suites",
    "real": BENCH / "real",
    "main": BENCH,
    "vector": BENCH / "vector",
    "runtime": BENCH / "runtime",
}
OPTIONS = {"vector": ["--add-modules", "jdk.incubator.vector"]}

LUCENE = {
    "name": "lucene-core-10.5.2.jar",
    "sha256": "39210e1024da5c31d1ad7571d622ebb122a8c4e23ff42e9bc380124973d8c635",
    "urls": [
        "https://repo1.maven.org/maven2/org/apache/lucene/lucene-core/10.5.2/lucene-core-10.5.2.jar",
        "https://repo.maven.apache.org/maven2/org/apache/lucene/lucene-core/10.5.2/lucene-core-10.5.2.jar",
    ],
}

ROUNDS = {"LuceneBench": 40}
REFERENCES = ("g1", "zgc", "graal")
TIME = re.compile(rb"^time ([0-9.]+)$", re.M)


class ChecksumMismatch(RuntimeError):
    pass


def steady(samples):
    return statistics.median(samples[len(samples) // 2 :])


def peak(samples):
    """First index whose 5-round window median lies within 5% of the process's steady value."""
    level = steady(samples)
    for i in range(len(samples) - 4):
        if abs(statistics.median(samples[i : i + 5]) - level) <= level * 0.05:
            return i
    return None


def result(samples, rounds=False):
    runs = samples if rounds else [samples]
    record = {
        "samples": samples,
        "steady_samples": [x for s in runs for x in s[len(s) // 2 :]] if rounds else list(samples),
        "steady_ms": statistics.median(steady(s) for s in runs) if rounds else statistics.median(samples),
    }
    if rounds:
        record["iter0_ms"] = statistics.median(s[0] for s in runs)
        peaks = [peak(s) for s in runs]
        if None in peaks:
            record["peak_iteration"] = None
        else:
            record["peak_iteration"] = statistics.median(peaks)
            record["peak_ms"] = statistics.median(sum(s[:i]) for s, i in zip(runs, peaks))
    return record


def real_result(samples):
    record = samples if isinstance(samples, dict) else result(samples, True)
    if record["peak_iteration"] is None:
        return f"time to peak not reached; steady {record['steady_ms']:.1f} ms"
    return (
        f"time to peak round {record['peak_iteration']:g}, {record['peak_ms']:.1f} ms; "
        f"steady {record['steady_ms']:.1f} ms"
    )


def noise(a, b):
    """True iff two sample ranges overlap."""
    return max(min(a), min(b)) <= min(max(a), max(b))


def geomean(values):
    return math.exp(sum(math.log(v) for v in values) / len(values))


MICRO = ("main", "vector", "runtime")


def vs_hotspot(doc):
    """Target performance relative to HotSpot G1 on this machine: per benchmark (stored on each entry),
    geometric means per group plus "micro" (main+vector+runtime), and startup."""
    groups = {}
    for entry in doc["benchmarks"].values():
        engines = entry["engines"]
        record = common.relative(
            engines.get("target", {}).get("steady_ms"), engines.get("g1", {}).get("steady_ms")
        )
        entry.pop("vs_hotspot", None)
        if record:
            entry["vs_hotspot"] = record
            groups.setdefault(entry["group"], []).append(record)
            if entry["group"] in MICRO:
                groups.setdefault("micro", []).append(record)
    summary = {"groups": {group: common.geomean_relative(records) for group, records in groups.items()}}
    startup = doc.get("startup", {})
    if startup.get("samples_ms") and startup.get("hotspot_samples_ms"):
        summary["startup"] = common.relative(
            statistics.median(startup["samples_ms"]), statistics.median(startup["hotspot_samples_ms"])
        )
    return summary


def gate(current, baseline):
    """True iff the current run fails the gate against a baseline results document."""
    failed = False
    compared = 0
    for name, old in baseline["benchmarks"].items():
        before = old.get("engines", {}).get("target", {})
        if "steady_ms" not in before:
            continue
        compared += 1
        new = current.get("benchmarks", {}).get(name)
        after = (new or {}).get("engines", {}).get("target", {})
        if new is None or new.get("workload") != old.get("workload") or "steady_ms" not in after:
            print(f"GATE FAIL {name}: missing, failed, or changed workload")
            failed = True
            continue
        if after["steady_ms"] > before["steady_ms"] * 1.03 and min(after["steady_samples"]) > max(
            before["steady_samples"]
        ):
            print(f"GATE FAIL {name}: {(after['steady_ms'] / before['steady_ms'] - 1) * 100:+.1f}%")
            failed = True
    if compared == 0:
        print("GATE FAIL: baseline has no successful TARGET measurements")
    return failed or compared == 0


def parse_rounds(out, rounds):
    """Round times and the checksum line from a round-protocol program's stdout."""
    text = out.decode("ascii", errors="replace")
    lines = text.split("\n")
    if len(lines) != rounds + 2 or lines[-1] != "":
        raise RuntimeError(f"expected {rounds} round lines and a checksum line, got:\n{text[-2000:]}")
    times = []
    for index, line in enumerate(lines[:rounds]):
        match = re.fullmatch(r"round (\d+) (\S+) ms", line)
        if not match or match.group(1) != str(index):
            raise RuntimeError(f"malformed round line {index}: {line!r}")
        try:
            value = float(match.group(2))
        except ValueError:
            raise RuntimeError(f"malformed round time: {line!r}") from None
        if not math.isfinite(value) or value <= 0:
            raise RuntimeError(f"non-positive round time: {line!r}")
        times.append(value)
    if not re.fullmatch(r"checksum -?\d+", lines[rounds]):
        raise RuntimeError(f"malformed checksum line: {lines[rounds]!r}")
    return times, (lines[rounds] + "\n").encode()


def times(command, args, checksum=None, rounds=None):
    samples = []
    env = common.ENV | common.knobs(max_heap="512m")
    for _ in range(args.runs):
        process = common.run([*command, str(rounds or args.samples)], args.timeout, env=env)
        out = process.out
        if rounds is not None and process.status == 0:
            round_times, out = parse_rounds(process.out, rounds)
            samples.append(round_times)
        if process.status != 0 or (checksum is not None and out != checksum):
            error = ChecksumMismatch if process.status == 0 else RuntimeError
            status = "timeout" if process.status is None else f"exit {process.status}"
            raise error(
                f"{Path(command[0]).name} {command[-1]}: {status}, output {out.decode(errors='replace')!r}, "
                f"expected {checksum.decode(errors='replace') if checksum else None!r}\n"
                f"{process.err.decode(errors='replace')[-500:]}"
            )
        checksum = out
        if rounds is None:
            samples.extend(float(value) for value in TIME.findall(process.err))
    return samples, checksum


def sources(group):
    found = sorted(GROUPS[group].glob("*.java"))
    if group in ("vector", "runtime"):
        found.append(BENCH / "Bench.java")
    return found


def payloads(group):
    return sorted((GROUPS[group] / "payload").glob("*.java"))


def dependencies(group):
    if group != "real":
        return []
    return [download(ROOT / "target" / LUCENE["name"], LUCENE["urls"], LUCENE["sha256"])]


def workload(group):
    return [sha256(path) for path in [*sources(group), *payloads(group), *dependencies(group)]]


def build(group, args):
    home = args.graal_home if args.graal_only else common.jdk()
    options = OPTIONS.get(group, [])
    jars = dependencies(group)
    directory = common.WORK / "bench" / f"{group}-{key(str(home), common.release(home), options, workload(group))}"

    def compile_group(temporary):
        classes = temporary / "classes"
        classpath = ["-classpath", os.pathsep.join(map(str, jars))] if jars else []
        common.javac([*options, *classpath], classes, sources(group), home)
        if payloads(group):
            common.javac([], classes / "payload", payloads(group), home)
            for compiled in (classes / "payload").glob("**/*.class"):
                compiled.rename(compiled.with_suffix(".bin"))

    common.cached(directory, compile_group, args.rebuild)
    return os.pathsep.join(map(str, [directory / "classes", *jars]))


def programs(group, filters):
    names = sorted(path.stem for path in GROUPS[group].glob("*.java") if path.stem not in ("Bench", "Startup"))
    if not filters:
        return names
    lowered = [f.lower() for f in filters]
    return [name for name in names if any(f == group or f in name.lower() for f in lowered)]


class State:
    def __init__(self, args):
        self.args = args
        self.cache_path = common.WORK / "bench" / "hotspot.json"
        self.cache = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        self.hostname = socket.gethostname()
        self.homes = {}
        if not args.graal_only:
            self.homes["g1"] = common.jdk()
            if args.zgc:
                self.homes["zgc"] = common.jdk()
        if args.graal or args.graal_only:
            self.homes["graal"] = args.graal_home
        self.releases = {engine: common.release(home) for engine, home in self.homes.items()}
        self.doc = {
            "machine": self.hostname,
            "os": platform.system().lower(),
            "arch": platform.machine(),
            "jdks": self.releases,
            "benchmarks": {},
        }
        self.vms = {}
        if not args.graal_only:
            self.vms["target"] = common.generic_vm(args.target, args.target_is_java, args.rebuild)
            self.doc["target"] = str(Path(args.target).resolve())
            self.doc["target_sha256"] = sha256(args.target)
            self.doc["target_toolchain"] = key(common.toolchain(args.target, args.target_is_java))
            self.doc["target_is_java"] = args.target_is_java
        if args.baseline:
            self.vms["baseline"] = common.generic_vm(args.baseline, False, args.rebuild)
        self.ratios = {engine: [] for engine in self.homes}

    def save(self):
        self.doc["vs_hotspot"] = vs_hotspot(self.doc)
        common.write_json(self.args.json, self.doc)

    def save_cache(self):
        common.write_json(self.cache_path, self.cache)

    def launcher(self, engine, heap):
        """argv prefix for target/baseline. A --target-is-java target is a plain java launcher that ignores
        the BENCH_TARGET_* knobs, so it gets the reference engines' GC and heap flags to stay comparable."""
        flags = ["-XX:+UseG1GC", *heap] if engine == "target" and self.args.target_is_java else []
        return [str(self.vms[engine]), *flags]

    def reference(self, engine, heap, options=()):
        gc = "-XX:+UseZGC" if engine == "zgc" else "-XX:+UseG1GC"
        return [str(self.homes[engine] / "bin" / "java"), gc, *heap, *options]


def run_groups(state, filters):
    args = state.args
    guards = False
    for group in ("real", "main", "vector", "runtime"):
        names = programs(group, filters)
        if not names:
            continue
        if group == "real":
            print("Real programs (gating benchmarks)")
        elif not guards:
            print("Microbenchmarks (regression guards)")
            guards = True
        classpath = build(group, args)
        options = OPTIONS.get(group, [])
        commands = {
            engine: [*state.launcher(engine, ["-Xms128m", "-Xmx512m"]), *options, "-cp", classpath]
            for engine in state.vms
        }
        for engine in state.homes:
            commands[engine] = [*state.reference(engine, ["-Xms128m", "-Xmx512m"], options), "-cp", classpath]
        for name in names:
            measure_program(state, group, name, commands)


def measure_program(state, group, name, commands):
    args = state.args
    rounds = ROUNDS.get(name)
    inputs = {
        engine: key(workload(group), {engine: state.releases[engine]}, state.hostname, args.runs, rounds, args.samples)
        for engine in state.homes
    }
    identity = key(workload(group), rounds, args.samples)
    old = state.cache.get(name, {})
    if not args.reuse_hotspot or args.hotspot or old.get("workload") != identity:
        old = {}
    entry = {
        e: v for e, v in old.items()
        if e in ("workload", "checksum") or v.get("key") == inputs.get(e, v.get("key"))
    }
    entry["workload"] = identity
    state.cache[name] = entry

    engines = {}
    fresh = [] if args.reuse_hotspot else list(state.homes)
    for engine in state.homes:
        if engine in fresh:
            continue
        if engine not in entry:
            try:
                checksum = entry["checksum"].encode() if "checksum" in entry else None
                samples, checksum = times([*commands[engine], name], args, checksum, rounds)
            except ChecksumMismatch:
                raise
            except RuntimeError as error:
                engines[engine] = {"error": str(error)}
                print(f"{name}: {engine} failed: {error}")
                continue
            entry[engine] = {"key": inputs[engine], "result": {"samples": samples}}
            entry["checksum"] = checksum.decode()
            state.save_cache()
        engines[engine] = result(entry[engine]["result"]["samples"], rounds is not None)

    active = [*fresh, *[engine for engine in ("target", "baseline") if engine in state.vms]]
    accumulated = {engine: [] for engine in active}
    failed = {}
    single = argparse.Namespace(**{**vars(args), "runs": 1})
    for index in range(args.runs):
        for engine in active if index % 2 == 0 else active[::-1]:
            if engine in failed:
                continue
            checksum = entry["checksum"].encode() if "checksum" in entry else None
            try:
                samples, checksum = times([*commands[engine], name], single, checksum, rounds)
            except ChecksumMismatch:
                raise
            except RuntimeError as error:
                failed[engine] = {"error": str(error)}
                print(f"{name}: {engine} failed: {error}")
                continue
            if "checksum" not in entry:
                entry["checksum"] = checksum.decode()
            accumulated[engine].extend(samples)
    for engine in active:
        engines[engine] = failed.get(engine) or result(accumulated[engine], rounds is not None)
    for engine in fresh:
        if engine not in failed:
            entry[engine] = {"key": inputs[engine], "result": {"samples": accumulated[engine]}}
    if fresh:
        state.save_cache()

    state.doc["benchmarks"][name] = {
        "group": group,
        "workload": key(workload(group), rounds, args.samples),
        "engines": engines,
    }
    state.save()
    report(state, name, engines, rounds is not None)


def report(state, name, engines, rounds):
    target = engines.get("target", {})
    baseline = engines.get("baseline", {})
    segments = []
    if rounds:
        if "steady_ms" in target:
            segments.append(f"target {real_result(target)}")
        for engine in state.homes:
            record = engines.get(engine, {})
            if "steady_ms" not in record:
                segments.append(f"{engine} error")
            elif "steady_ms" in target:
                segments.append(f"{engine} {real_result(record)}, ratio {target['steady_ms'] / record['steady_ms']:.2f}")
            else:
                segments.append(f"{engine} {real_result(record)}")
        if "steady_ms" in baseline and "steady_ms" in target:
            segments.append(f"baseline {real_result(baseline)}, {delta(target, baseline)}")
        print(f"{name}: " + " | ".join(segments))
        for engine, record in engines.items():
            if "samples" in record:
                firsts = [statistics.median(s[i] for s in record["samples"]) for i in range(min(5, *map(len, record["samples"])))]
                print(f"  {engine} rounds 0-4 (ms): " + ", ".join(f"{t:.1f}" for t in firsts))
        return
    if "steady_ms" in target:
        segments.append(f"target {target['steady_ms']:8.1f} ms")
    elif "error" in target:
        segments.append("target error")
    for engine in state.homes:
        record = engines.get(engine, {})
        if "steady_ms" not in record:
            segments.append(f"{engine} error")
        elif "steady_ms" in target:
            ratio = target["steady_ms"] / record["steady_ms"]
            state.ratios[engine].append(ratio)
            segments.append(f"{engine} {record['steady_ms']:8.1f} ms, ratio {ratio:.2f}")
        else:
            segments.append(f"{engine} {record['steady_ms']:8.1f} ms")
    if "steady_ms" in baseline and "steady_ms" in target:
        segments.append(f"baseline {baseline['steady_ms']:8.1f} ms, {delta(target, baseline)}")
    print(f"{name:<17} " + " | ".join(segments))


def delta(target, baseline):
    pct = (target["steady_ms"] / baseline["steady_ms"] - 1) * 100
    overlap = noise(target["steady_samples"], baseline["steady_samples"])
    return f"{pct:+.1f}%" + (" (noise)" if overlap else "")


def suite_selected(suite, name, filters):
    """Suites are opt-in (multi-GB downloads, hours of runs): only selected by an explicit filter."""
    if not filters:
        return False
    full = f"{suite}/{name}"
    return any(f.lower() in ("suites", suite) or f.lower() in full for f in filters)


def suite_results(state, filters):
    args = state.args
    pairs = [(suite, name) for suite, names in suites.BENCHMARKS.items() for name in names if suite_selected(suite, name, filters)]
    if not pairs:
        return
    print("Application suites")
    archives = {}
    ratios = {engine: [] for engine in state.homes}
    cache = state.cache.setdefault("suites", {})
    for suite, name in pairs:
        if suite not in archives:
            archives[suite] = suites.archive(suite, download)
        archive = archives[suite]
        identity = key(sha256(archive), name, suites.ITERATIONS, "default", "2g")
        engines = {engine: state.reference(engine, ["-Xms128m", "-Xmx2g"]) for engine in state.homes}
        engines.update({engine: state.launcher(engine, ["-Xms128m", "-Xmx2g"]) for engine in state.vms})
        print(f"{suite}/{name}")
        results = {}
        entry = cache.setdefault(f"{suite}/{name}", {})
        fingerprints = {
            engine: key(identity, engines[engine], state.releases[engine], state.hostname, platform.machine(), args.runs)
            for engine in state.homes
        }
        if args.reuse_hotspot and not args.hotspot:
            for engine, fingerprint in fingerprints.items():
                hit = entry.get(engine)
                if hit and hit["key"] == fingerprint:
                    results[engine] = hit["result"]
        pending = [engine for engine in engines if engine not in results]
        measured = {engine: {"samples": [], "wall_seconds": [], "log": None} for engine in pending}
        single = argparse.Namespace(**{**vars(args), "runs": 1})
        for index in range(args.runs):
            for engine in pending if index % 2 == 0 else pending[::-1]:
                record = measured[engine]
                if "error" in record:
                    continue
                log = common.WORK / "bench" / "suite-logs" / f"{suite}-{name}-{engine}-run{index}"
                one = suites.measure(suite, name, archive, engines[engine], single, log)
                record["log"] = record["log"] or one["log"]
                record["wall_seconds"] += one["wall_seconds"]
                if "error" in one:
                    record.update(error=one["error"], exit=one["exit"], log=one["log"])
                else:
                    record["samples"] += one["samples"]
        for engine in pending:
            record = measured[engine]
            if "error" not in record:
                record.update(result(record["samples"], True))
            results[engine] = record
            if engine in fingerprints:
                entry[engine] = {"key": fingerprints[engine], "result": record}
        if pending:
            state.save_cache()
        for engine in engines:
            record = results[engine]
            if "error" in record:
                print(f"  {engine}: {record['error']}")
            else:
                print(f"  {engine}: iter0 {record['iter0_ms']:.1f} ms; {real_result(record)}")
        state.doc["benchmarks"][f"{suite}/{name}"] = {"group": "suites", "workload": identity, "engines": results}
        state.save()
        target = results.get("target", {})
        for engine in state.homes:
            if "steady_ms" in target and "steady_ms" in results.get(engine, {}):
                ratio = target["steady_ms"] / results[engine]["steady_ms"]
                ratios[engine].append(ratio)
                print(f"  TARGET/{engine} {ratio:.2f}")
    if "target" in state.vms:
        for engine, values in ratios.items():
            if values:
                pct = f", {(1 / geomean(values) - 1) * 100:+.1f}% vs HotSpot" if engine == "g1" else ""
                print(f"suites geometric mean TARGET/{engine}: {geomean(values):.2f} ({len(values)}/{len(pairs)} completed pairs{pct})")
            else:
                print(f"suites geometric mean TARGET/{engine}: unavailable (0/{len(pairs)} completed pairs)")


def startup(state):
    args = state.args
    classpath = build("main", args)
    env = common.ENV | common.knobs(max_heap="512m")
    commands = {"target": [*state.launcher("target", ["-Xms128m", "-Xmx512m"]), "-cp", classpath, "Startup"]}
    if "g1" in state.homes:
        commands["hotspot"] = [*state.reference("g1", ["-Xms128m", "-Xmx512m"]), "-cp", classpath, "Startup"]
    walls = {engine: [] for engine in commands}
    for _ in range(5):
        for engine, command in commands.items():
            process = common.run(command, args.timeout, env=env)
            if process.status != 0:
                print(f"startup: {engine} error, exit {process.status}: {process.err.decode(errors='replace')[-500:]}")
                return
            walls[engine].append(process.seconds * 1000)
    state.doc["startup"] = {"samples_ms": walls["target"]}
    line = f"startup: {statistics.median(walls['target']):.0f} ms"
    if "hotspot" in walls:
        state.doc["startup"]["hotspot_samples_ms"] = walls["hotspot"]
        record = common.relative(statistics.median(walls["target"]), statistics.median(walls["hotspot"]))
        line += f" | hotspot {statistics.median(walls['hotspot']):.0f} ms, {record['pct']:+.1f}% vs HotSpot"
    state.save()
    print(line)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark a JVM/AOT target against reference JVMs.")
    parser.add_argument("filters", nargs="*", help="group names or case-insensitive benchmark-name substrings")
    parser.add_argument("--runs", type=int, default=2, help="processes per engine per workload")
    parser.add_argument("--samples", type=int, default=3, help="timed rounds per microbenchmark process")
    parser.add_argument("--baseline", type=Path, help="a second target binary to A/B against")
    parser.add_argument("--reuse-hotspot", action="store_true",
                        help="reuse cached reference-engine results from earlier runs on this machine instead of "
                             "measuring them fresh alongside the target (faster, but less fair)")
    parser.add_argument("--hotspot", action="store_true", help="with --reuse-hotspot: refresh the cache anyway")
    parser.add_argument("--graal", action="store_true", help="also measure the pinned GraalVM Community")
    parser.add_argument("--graal-only", action="store_true", help="measure only the pinned GraalVM")
    parser.add_argument("--json", type=Path, help="results file (default <work-dir>/bench/results.json)")
    parser.add_argument("--gate", type=Path, help="fail if target regressed against this results file")
    parser.add_argument("--zgc", action="store_true", help="also measure HotSpot with ZGC")
    parser.add_argument("--target", type=Path, help="the toolchain under test (required unless --graal-only)")
    parser.add_argument("--target-is-java", action="store_true", help="--target is already a java launcher")
    parser.add_argument("--target-profile", type=Path, default=os.environ.get("BENCH_TARGET_PROFILE"),
                        help="JSON profile remapping knobs, build_args and extra_artifacts for this target")
    parser.add_argument("--timeout", type=float, default=300, help="per-process timeout in seconds")
    parser.add_argument("--rebuild", action="store_true", help="rebuild cached classes and VM images")
    parser.add_argument("--work-dir", default=os.environ.get("BENCH_WORK_DIR", "target/bench-work"),
                        help="build/cache directory, relative to the repository root")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.samples < 1:
        parser.error("--samples must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.graal_only:
        for flag, value in (("--gate", args.gate), ("--baseline", args.baseline), ("--target-is-java", args.target_is_java)):
            if value:
                parser.error(f"--graal-only cannot be combined with {flag}")
    elif args.target is None:
        parser.error("--target is required unless --graal-only is given")
    return args


def main(argv=None):
    args = parse_args(argv)
    common.set_work(args.work_dir)
    if args.target_profile:
        common.load_profile(args.target_profile)
    args.json = args.json or common.WORK / "bench" / "results.json"
    filters = args.filters
    baseline = None
    if args.gate:
        baseline = json.loads(args.gate.read_text())
        filters = filters or sorted(baseline["benchmarks"])
    args.graal_home = suites.graal(download) if args.graal or args.graal_only else None

    state = State(args)
    suite_results(state, filters)
    run_groups(state, filters)
    if "target" in state.vms:
        for engine, values in state.ratios.items():
            if values:
                line = f"geometric mean TARGET/{engine}: {geomean(values):.2f}"
                if engine == "g1":
                    line += f" ({(1 / geomean(values) - 1) * 100:+.1f}% vs HotSpot)"
                print(line)
    if not args.graal_only and (not filters or "startup" in [f.lower() for f in filters]):
        startup(state)
    state.save()
    return int(gate(state.doc, baseline)) if baseline is not None else 0


if __name__ == "__main__":
    sys.exit(main())
