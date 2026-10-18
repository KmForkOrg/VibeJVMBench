"""Shared infrastructure: paths, JDK discovery, process execution, javac and build caching (SPEC §1)."""

import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench"
BENCH_HOME = Path(os.environ.get("BENCH_HOME") or Path.home() / ".cache" / "vibejvmbench")
WORK = ROOT / os.environ.get("BENCH_WORK_DIR", "target/bench-work")

SANITIZED = ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "JDK_JAVAC_OPTIONS", "CLASSPATH")
ENV = {name: value for name, value in os.environ.items() if name not in SANITIZED}

KNOBS = {
    "max_heap": "BENCH_TARGET_MAX_HEAP",
    "code_cache": "BENCH_TARGET_CODE_CACHE",
    "log": "BENCH_TARGET_LOG",
}

BUILD_ARGS = ["{target}", "build", "--jdk", "{jdk}", "-o", "{output}"]

EXTRA_ARTIFACTS = []


def set_work(path):
    global WORK
    WORK = (ROOT / path).resolve()


def load_profile(path):
    """Applies a target bench profile: a JSON object with optional "knobs", "build_args" and
    "extra_artifacts" keys overriding the defaults above."""
    global BUILD_ARGS, EXTRA_ARTIFACTS
    profile = json.loads(Path(path).read_text())
    unknown = set(profile) - {"knobs", "build_args", "extra_artifacts"}
    if unknown:
        raise SystemExit(f"{path}: unknown profile keys {sorted(unknown)}")
    for knob, name in profile.get("knobs", {}).items():
        if knob not in KNOBS:
            raise SystemExit(f"{path}: unknown knob {knob!r}; expected one of {sorted(KNOBS)}")
        KNOBS[knob] = name
    BUILD_ARGS = list(profile.get("build_args", BUILD_ARGS))
    EXTRA_ARTIFACTS = list(profile.get("extra_artifacts", EXTRA_ARTIFACTS))


def knobs(**values):
    """Environment entries for the given knob values, under the current knob names."""
    return {KNOBS[knob]: value for knob, value in values.items() if KNOBS[knob] is not None}


_jdk = None


def jdk():
    """The reference JDK home: $JAVA_HOME, else an existing $BENCH_JDK_HOME, else an error."""
    global _jdk
    if _jdk is None:
        if os.environ.get("JAVA_HOME"):
            _jdk = Path(os.environ["JAVA_HOME"])
        elif os.environ.get("BENCH_JDK_HOME") and Path(os.environ["BENCH_JDK_HOME"]).is_dir():
            _jdk = Path(os.environ["BENCH_JDK_HOME"])
        else:
            raise SystemExit("set JAVA_HOME (or BENCH_JDK_HOME) to the reference JDK's home directory")
    return _jdk


def release(home):
    path = Path(home) / "release"
    return path.read_text() if path.exists() else ""


@dataclass
class Result:
    out: bytes
    err: bytes
    status: int | None
    seconds: float
    rss_mb: float


def kill(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run(argv, timeout, cwd=None, env=ENV):
    """Runs argv in its own session with a wall-clock timeout, returning output, status and peak RSS.
    status is None iff the process ran for at least `timeout` seconds."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        start = time.monotonic()
        process = subprocess.Popen(
            [str(arg) for arg in argv], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=out, stderr=err, start_new_session=True,
        )
        timer = threading.Timer(timeout, kill, [process.pid])
        timer.start()
        try:
            _, wait_status, usage = os.wait4(process.pid, 0)
        finally:
            timer.cancel()
            kill(process.pid)
        seconds = time.monotonic() - start
        process.returncode = os.waitstatus_to_exitcode(wait_status)
        out.seek(0)
        err.seek(0)
        scale = 2**20 if sys.platform == "darwin" else 2**10
        return Result(
            out=out.read(),
            err=err.read(),
            status=None if seconds >= timeout else process.returncode,
            seconds=seconds,
            rss_mb=usage.ru_maxrss / scale,
        )


def javac(options, classes, sources, home=None):
    classes = Path(classes)
    classes.mkdir(parents=True, exist_ok=True)
    argv = [Path(home or jdk()) / "bin" / "javac", "-encoding", "UTF-8", *options, "-d", classes, *sources]
    process = subprocess.run([str(arg) for arg in argv], env=ENV, capture_output=True)
    if process.returncode:
        raise RuntimeError(f"javac failed:\n{process.stderr.decode(errors='replace')}")


def cached(directory, build, rebuild=False):
    """Builds `directory` once via build(temporary_directory) and keeps the 2 newest siblings.
    Returns True iff the directory already existed and was reused."""
    directory = Path(directory)
    if directory.exists() and not rebuild:
        return True
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = directory.with_name(f"{directory.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir()
    try:
        build(temporary)
        replace_directory(temporary, directory)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    prefix = directory.name[: directory.name.rfind("-") + 1]
    siblings = [
        path for path in directory.parent.iterdir()
        if path.is_dir() and path.name.startswith(prefix) and ".tmp-" not in path.name
    ]
    for stale in sorted(siblings, key=lambda path: path.stat().st_mtime, reverse=True)[2:]:
        shutil.rmtree(stale, ignore_errors=True)
    return False


def replace_directory(source, destination):
    """Renames source over destination, moving any existing destination aside first."""
    destination = Path(destination)
    if destination.exists():
        aside = destination.with_name(f"{destination.name}.tmp-old-{os.getpid()}-{threading.get_ident()}")
        os.rename(destination, aside)
        os.rename(source, destination)
        shutil.rmtree(aside, ignore_errors=True)
    else:
        os.rename(source, destination)


def place(source, destination):
    """Atomically moves a freshly built tree into place, copying first if it lives on another filesystem."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(source, destination)
    except OSError as error:
        if error.errno != 18:
            raise
        staging = destination.with_name(f"{destination.name}.tmp-{os.getpid()}")
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(source, staging, symlinks=True)
        os.rename(staging, destination)
        shutil.rmtree(source, ignore_errors=True)


NOT_COMPARED = frozenset({"bots_failed"})


def relative(target, hotspot, lower_is_better=True):
    """Target performance relative to HotSpot on the same machine, or None if not computable.
    ratio > 1 (pct > 0) always means the target did better, whichever way the metric runs."""
    if not target or not hotspot or target <= 0 or hotspot <= 0:
        return None
    ratio = hotspot / target if lower_is_better else target / hotspot
    return {"ratio": ratio, "pct": (ratio - 1) * 100}


def geomean_relative(records):
    """Geometric mean of relative() records, with the count it covers."""
    ratios = [record["ratio"] for record in records if record]
    if not ratios:
        return None
    ratio = math.exp(sum(math.log(r) for r in ratios) / len(ratios))
    return {"ratio": ratio, "pct": (ratio - 1) * 100, "n": len(ratios)}


def key(*parts):
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, document):
    """Atomic JSON write: temp file in the same directory, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    os.replace(temporary, path)


def download(path, urls, digest):
    """Fetches path from the first working URL, verifying its SHA-256 every time it is used."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        for index, url in enumerate(urls):
            try:
                print(f"downloading {url}", flush=True)
                with urllib.request.urlopen(url, timeout=60) as response, open(temporary, "wb") as file:
                    shutil.copyfileobj(response, file, 1 << 20)
                break
            except Exception:
                if index == len(urls) - 1:
                    temporary.unlink(missing_ok=True)
                    raise
        actual = sha256(temporary)
        if actual != digest:
            temporary.unlink()
            raise RuntimeError(f"{path.name}: sha256 {actual}, expected {digest}")
        os.replace(temporary, path)
    actual = sha256(path)
    if actual != digest:
        raise RuntimeError(f"{path}: sha256 {actual}, expected {digest}; delete it to re-download")
    return path


def toolchain(target, is_java=False):
    """Everything a built image's identity depends on besides its own classes (§1.10)."""
    target = Path(target)
    if is_java:
        home = target.resolve().parent.parent
        return ["java", sha256(target), str(home), sha256(home / "release") if (home / "release").exists() else None]
    home = jdk()
    modules = (home / "lib" / "modules").stat()
    libraries = home / "lib" / "static"
    static = sorted((str(path.relative_to(libraries)), sha256(path)) for path in libraries.rglob("*.a"))
    return [
        sha256(target),
        *[sha256(target.parent / name) for name in EXTRA_ARTIFACTS],
        str(home),
        sha256(home / "release"),
        modules.st_size,
        modules.st_mtime_ns,
        static,
    ]


def generic_vm(target, is_java=False, rebuild=False):
    """A java-compatible launcher for target: target itself, or an image packaged by it (§1.9)."""
    target = Path(target)
    if is_java:
        return target
    directory = WORK / "vm" / f"vm-{key(toolchain(target))}"

    def build(workdir):
        substitutions = {"target": target, "jdk": jdk(), "output": workdir / "java"}
        argv = [str(arg).format(**substitutions) for arg in BUILD_ARGS]
        process = subprocess.run(argv, env=ENV, capture_output=True)
        (workdir / "build.log").write_bytes(process.stdout + process.stderr)
        if process.returncode:
            raise RuntimeError(f"{' '.join(argv)} failed:\n{process.stderr.decode(errors='replace')}")

    cached(directory, build, rebuild)
    return directory / "java"
