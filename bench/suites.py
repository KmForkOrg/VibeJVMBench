"""DaCapo / Renaissance / GraalVM pinning, download, and per-suite output parsing (SPEC §7)."""

import json
import math
import platform
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

import common
from common import BENCH_HOME, ROOT

ITERATIONS = 10

DACAPO = {
    "name": "dacapo-23.11-MR2-chopin.zip",
    "sha256": "70a1fd4ed959f09053bb7ef8b2745a19d6f28dd8a7d0b350362039d89b49eb26",
    "urls": [
        "https://download.dacapobench.org/chopin/{name}",
        "https://lafo.ssw.uni-linz.ac.at/pub/graal-external-deps/{name}",
    ],
}
RENAISSANCE = {
    "name": "renaissance-gpl-0.16.2.jar",
    "sha256": "03718ba2c62b497456e5f034ce6a23797bbd4a713b63bcf6ada8c7b935d2c10d",
    "urls": ["https://github.com/renaissance-benchmarks/renaissance/releases/download/v0.16.2/{name}"],
}
GRAAL_VERSION = "25.4.4.1.1"
GRAAL_ARCHIVE = "25i4-25.0.4.1.1"
GRAAL_SHA256 = {
    "linux-x64": "05ccbbe783210b6886ff7b08fcd0b061c5dce4852b05db87284fc0e24abb08e2",
    "linux-aarch64": "e5f5e2f59643cf96765c741dc00b206f86c69c8c1bf843fe26050c871e0e2dbc",
    "macos-aarch64": "4ec9932aa4aab1afca0ea31982d32d6f956615c61fe21a227b49756b25adda4f",
}
GRAAL_URL = (
    "https://github.com/graalvm/graalvm-ce-builds/releases/download/graal-{version}/"
    "graalvm-community-jdk-{archive}_{platform}_bin.tar.gz"
)

BENCHMARKS = {
    "dacapo": "avrora batik fop xalan zxing biojava jme jython luindex lusearch pmd sunflow eclipse graphchi h2".split(),
    "renaissance": (
        "fj-kmeans future-genetic mnemonics par-mnemonics rx-scrabble scrabble dotty philosophers scala-doku "
        "scala-kmeans scala-stm-bench7 neo4j-analytics akka-uct db-shootout reactors"
    ).split(),
}

UNCAUGHT = ('Exception in thread "', "Uncaught error from thread [")
FAILURE_LINE = re.compile(r"Exception|Error|error:|FAILED|panicked|fatal|unsupported", re.I)


def platform_name():
    system = "macos" if platform.system() == "Darwin" else platform.system().lower()
    machine = {"x86_64": "x64", "arm64": "aarch64"}.get(platform.machine(), platform.machine())
    return f"{system}-{machine}"


def tmp():
    directory = BENCH_HOME / "tmp"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def archive(suite, download=common.download):
    """The runnable jar for a suite, downloading, verifying and (for DaCapo) extracting it as needed."""
    pin = {"dacapo": DACAPO, "renaissance": RENAISSANCE}[suite]
    path = download(ROOT / "target" / pin["name"], [url.format(name=pin["name"]) for url in pin["urls"]], pin["sha256"])
    if suite == "renaissance":
        return path
    extracted = ROOT / "target" / "dacapo-23.11-MR2-chopin"
    if not extracted.exists():
        temporary = Path(tempfile.mkdtemp(prefix="dacapo-", dir=tmp()))
        try:
            with zipfile.ZipFile(path) as zip_file:
                zip_file.extractall(temporary)
            entries = list(temporary.iterdir())
            top = entries[0] if len(entries) == 1 and entries[0].is_dir() else temporary
            common.place(top, extracted)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    return extracted / "dacapo-23.11-MR2-chopin.jar"


def graal(download=common.download):
    """The pinned GraalVM Community home under $BENCH_HOME/graal25, installing it if absent."""
    name = platform_name()
    if name not in GRAAL_SHA256:
        raise SystemExit(f"no pinned GraalVM build for {name}")
    digest = GRAAL_SHA256[name]
    home = BENCH_HOME / "graal25"
    marker = home / ".bench-sha256"
    if marker.exists() and marker.read_text().strip() == digest:
        return home
    if home.exists():
        raise SystemExit(f"{home} exists but is not the pinned GraalVM {GRAAL_VERSION}; move it aside")
    url = GRAAL_URL.format(version=GRAAL_VERSION, archive=GRAAL_ARCHIVE, platform=name)
    tarball = download(BENCH_HOME / "downloads" / url.rsplit("/", 1)[1], [url], digest)
    temporary = Path(tempfile.mkdtemp(prefix="graal-", dir=tmp()))
    try:
        with tarfile.open(tarball) as tar:
            tar.extractall(temporary, filter="tar")
        (top,) = temporary.iterdir()
        if platform.system() == "Darwin":
            top = top / "Contents" / "Home"
        (top / ".bench-sha256").write_text(digest)
        common.place(top, home)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return home


def command(suite, name, archive, scratch):
    if suite == "dacapo":
        return ["-jar", archive, name, "-n", str(ITERATIONS), "--scratch-directory", f"{scratch}/scratch"]
    return ["-jar", archive, name, "-r", str(ITERATIONS), "--scratch-base", scratch, "--json", f"{scratch}/iterations.json"]


def parse(suite, name, output, report=None):
    """Per-iteration milliseconds from one suite run, or ValueError if the run was not a clean pass."""
    if any(marker in output for marker in UNCAUGHT):
        raise ValueError("uncaught exception in benchmark thread")
    if suite == "dacapo":
        pattern = re.compile(
            rf"===== DaCapo .* {re.escape(name)} (completed warmup (\d+)|PASSED) in (\d+) msec ====="
        )
        matches = [pattern.search(line) for line in output.splitlines()]
        matches = [match for match in matches if match]
        warmups = [match.group(2) or "" for match in matches]
        if warmups != [str(i) for i in range(1, ITERATIONS)] + [""]:
            raise ValueError(f"unexpected DaCapo iteration sequence {warmups}")
        samples = [float(match.group(3)) for match in matches]
    else:
        if report is None:
            raise ValueError("missing Renaissance report")
        benchmark = report["data"][name]
        if benchmark["termination"] != "normal":
            raise ValueError(f"benchmark termination {benchmark['termination']!r}")
        if report["environment"]["vm"]["termination"] != "normal":
            raise ValueError(f"VM termination {report['environment']['vm']['termination']!r}")
        samples = [row["duration_ns"] / 1e6 for row in benchmark["results"]]
    if len(samples) != ITERATIONS:
        raise ValueError(f"{len(samples)} iterations, expected {ITERATIONS}")
    if not all(isinstance(sample, (int, float)) and math.isfinite(sample) and sample > 0 for sample in samples):
        raise ValueError(f"non-positive or non-finite iteration time in {samples}")
    return samples


def measure(suite, name, archive, engine, args, log):
    """Runs one engine on one suite benchmark args.runs times; a failed run ends the measurement."""
    env = common.ENV | common.knobs(max_heap="2g", code_cache="0")
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    samples, walls, logs = [], [], []
    for index in range(args.runs):
        scratch = Path(tempfile.mkdtemp(prefix="suites-", dir=tmp()))
        path = log.with_name(f"{log.name}.{index}.log")
        logs.append(path)
        try:
            argv = [*engine, f"-Djava.io.tmpdir={scratch}", *command(suite, name, archive, scratch)]
            result = common.run(argv, args.timeout, cwd=scratch, env=env)
            output = (result.out + result.err).decode(errors="replace")
            path.write_text(output)
            walls.append(result.seconds)
            try:
                if result.status != 0:
                    raise ValueError("timeout" if result.status is None else f"exit {result.status}")
                report_path = scratch / "iterations.json"
                report = json.loads(report_path.read_text()) if report_path.exists() else None
                samples.append(parse(suite, name, output, report))
            except (ValueError, KeyError, TypeError) as error:
                line = next((line for line in output.splitlines() if FAILURE_LINE.search(line)), None)
                return {
                    "samples": samples,
                    "error": line.strip() if line else str(error),
                    "exit": result.status,
                    "log": str(path),
                    "wall_seconds": walls,
                }
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    return {"samples": samples, "wall_seconds": walls, "log": str(logs[0])}
