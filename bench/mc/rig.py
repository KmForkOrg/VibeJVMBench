import os
import random
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import BENCH_HOME

CPU = Path("/sys/devices/system/cpu")


def hold_quiet():
    script = os.environ.get("BENCH_QUIET", str(BENCH_HOME / "quiet.sh"))
    if not script or os.environ.get("BENCH_QUIET_HELD") or not Path(script).exists():
        return
    os.environ["BENCH_QUIET_HELD"] = "1"
    os.execv(script, [script, sys.executable, *sys.argv])


class Rig:
    def __init__(self):
        self.samples = []
        self.alive = False

    def __enter__(self):
        self.alive = True
        threading.Thread(target=self.sample, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.alive = False
        return False

    def sample(self):
        hwmon = next(
            (path for path in Path("/sys/class/hwmon").glob("hwmon*") if (path / "name").exists()
             and (path / "name").read_text().strip() == "k10temp"),
            None,
        )
        while self.alive:
            frequencies = []
            for path in CPU.glob("cpu[0-9]*/cpufreq/scaling_cur_freq"):
                try:
                    frequencies.append(int(path.read_text()) / 1000)
                except (OSError, ValueError):
                    pass
            try:
                tctl = int((hwmon / "temp1_input").read_text()) / 1000 if hwmon else 0.0
            except (OSError, ValueError):
                tctl = 0.0
            load = float(Path("/proc/loadavg").read_text().split()[0])
            mhz = sum(frequencies) / len(frequencies) if frequencies else 0
            self.samples.append((time.monotonic(), mhz, tctl, load))
            time.sleep(1)

    def telemetry(self, since, until):
        window = [sample for sample in self.samples if since <= sample[0] <= until]
        if not window:
            return {}
        summary = {}
        for index, name in ((1, "mhz"), (2, "tctl"), (3, "load")):
            values = [sample[index] for sample in window]
            summary[name] = [round(min(values), 1), round(statistics.median(values), 1), round(max(values), 1)]
        return summary

    def launch(self, argv, cwd, env, name):
        process = subprocess.Popen(
            [str(arg) for arg in argv], cwd=cwd, env=os.environ | env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace",
        )
        return Engine(process)


class Engine:
    def __init__(self, process):
        self.process = process
        self.pid = process.pid
        self.started = time.monotonic()
        self.started_ns = time.monotonic_ns()
        self.lines = []
        self.hwm_kb = 0
        self.changed = threading.Condition()
        threading.Thread(target=self.read, daemon=True).start()
        threading.Thread(target=self.watch_memory, daemon=True).start()

    def read(self):
        for line in self.process.stdout:
            with self.changed:
                self.lines.append((time.monotonic(), line.rstrip("\n")))
                self.changed.notify_all()
        with self.changed:
            self.changed.notify_all()

    def watch_memory(self):
        while self.process.poll() is None:
            try:
                for line in Path(f"/proc/{self.pid}/status").read_text().splitlines():
                    if line.startswith("VmHWM:"):
                        self.hwm_kb = max(self.hwm_kb, int(line.split()[1]))
            except (OSError, ValueError):
                pass
            time.sleep(0.5)

    def send(self, line):
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def wait_for(self, predicate, timeout, start=0):
        deadline = time.monotonic() + timeout
        with self.changed:
            index = start
            while True:
                for index in range(index, len(self.lines)):
                    moment, line = self.lines[index]
                    if predicate(line):
                        return index, moment, line
                index = len(self.lines)
                if self.process.poll() is not None and index == len(self.lines):
                    self.changed.wait(0.2)
                    if index == len(self.lines):
                        raise RuntimeError(f"process exited with {self.process.returncode} before expected output")
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no expected output within {timeout}s")
                self.changed.wait(min(1, remaining))

    def stop(self, timeout):
        try:
            return self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait()

    def output(self):
        return "\n".join(line for _, line in self.lines)


def schedule(engines, runs, seed):
    rng = random.Random(seed)
    order = []
    for index in range(runs):
        round_engines = list(engines)
        rng.shuffle(round_engines)
        order += [(index, engine) for engine in round_engines]
    return order


def summarize(samples, resamples=10000, seed=0):
    if not samples:
        return None
    import numpy

    values = numpy.asarray(samples, dtype=float)
    rng = numpy.random.default_rng(seed)
    medians = numpy.median(values[rng.integers(0, len(values), size=(resamples, len(values)))], axis=1)
    low, high = numpy.percentile(medians, [2.5, 97.5])
    return {"median": float(numpy.median(values)), "ci": [float(low), float(high)], "n": len(samples)}


def verdict(stats, candidate, baselines, lower_is_better=True):
    mine = stats.get(candidate)
    others = {name: stats[name] for name in baselines if stats.get(name)}
    if not mine or not others:
        return None
    pick = min if lower_is_better else max
    best = pick(others, key=lambda name: others[name]["median"])
    reference = others[best]["median"]
    win = mine["ci"][1] < reference if lower_is_better else mine["ci"][0] > reference
    return {"best_baseline": best, "win": bool(win), "ratio": mine["median"] / reference if reference else None}
