#!/usr/bin/env python3

import argparse
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common
import fetch
import rig as rigging
import suites
from common import ROOT

MC_HOME = fetch.HOME.parent
RUNS = MC_HOME / "runs"
WORLDS = MC_HOME / "worlds-2"
EXTRACTED = fetch.HOME / "extracted"
BOT = Path(__file__).with_name("Bot.java")

ENGINES = ("target", "hotspot-g1")
REFERENCES = ("hotspot-g1", "hotspot-zgc", "graal", "target-base")
BOTS = 20
BOT_RING = 160
BOT_HEIGHT = 100

AIKAR = [
    "-XX:+UseG1GC",
    "-XX:+ParallelRefProcEnabled",
    "-XX:MaxGCPauseMillis=200",
    "-XX:+UnlockExperimentalVMOptions",
    "-XX:+DisableExplicitGC",
    "-XX:+AlwaysPreTouch",
    "-XX:G1NewSizePercent=30",
    "-XX:G1MaxNewSizePercent=40",
    "-XX:G1HeapRegionSize=8M",
    "-XX:G1ReservePercent=20",
    "-XX:G1HeapWastePercent=5",
    "-XX:G1MixedGCCountTarget=4",
    "-XX:InitiatingHeapOccupancyPercent=15",
    "-XX:G1MixedGCLiveThresholdPercent=90",
    "-XX:G1RSetUpdatingPauseTimePercent=5",
    "-XX:SurvivorRatio=32",
    "-XX:+PerfDisableSharedMem",
    "-XX:MaxTenuringThreshold=1",
    "-Dusing.aikars.flags=https://mcflags.emc.gs",
    "-Daikars.new.flags=true",
]

PROPERTIES = {
    "online-mode": "false",
    "white-list": "false",
    "enforce-secure-profile": "false",
    "level-seed": "20261016",
    "level-name": "world",
    "view-distance": "10",
    "simulation-distance": "10",
    "max-tick-time": "-1",
    "function-permission-level": "4",
    "pause-when-empty-seconds": "-1",
    "spawn-protection": "0",
    "allow-flight": "true",
    "gamemode": "creative",
    "force-gamemode": "true",
    "max-players": "32",
    "motd": "VibeJVMBench",
}

STILL = {
    "advance_time": "false",
    "advance_weather": "false",
    "random_tick_speed": "0",
    "spawn_mobs": "false",
    "spawn_monsters": "false",
    "spawn_patrols": "false",
    "spawn_phantoms": "false",
    "spawn_wandering_traders": "false",
    "spawn_wardens": "false",
    "fire_spread_radius_around_player": "0",
    "spread_vines": "false",
    "water_source_conversion": "false",
    "lava_source_conversion": "false",
    "mob_griefing": "false",
}
DEFAULTS = {
    "advance_time": "true",
    "advance_weather": "true",
    "random_tick_speed": "3",
    "spawn_mobs": "true",
    "spawn_monsters": "true",
    "spawn_patrols": "true",
    "spawn_phantoms": "true",
    "spawn_wandering_traders": "true",
    "spawn_wardens": "true",
    "fire_spread_radius_around_player": "128",
    "spread_vines": "true",
    "water_source_conversion": "true",
    "lava_source_conversion": "false",
    "mob_griefing": "true",
}

ENDED = {name: True for name in (
    "rss_mb", "gc_pauses", "gc_pause_max_ms", "gc_pause_total_ms", "gc_full_pauses", "gc_full_pause_max_ms")}
METRICS = {
    "worldgen": {"done_s": True, "done_internal_s": True, "worldgen_s": True, "chunks_per_s": False, **ENDED},
    "tick": {
        "ticks_per_second": False, "ms_per_tick": True, "server_tick_p50_ms": True, "server_tick_p95_ms": True,
        "server_tick_p99_ms": True, "instructions_per_tick_m": True, "cycles_per_tick_m": True,
        "branches_per_tick_m": True, "bots_failed": True, **ENDED,
    },
    "datagen": {"datagen_s": True, **ENDED},
    "upgrade": {"upgrade_s": True, "done_internal_s": True, **ENDED},
}

DATA_FORMAT = 121

HOTSPOT_PAUSE = re.compile(r"Pause.*?([\d.]+)ms$")
GENERIC_PAUSE = re.compile(r"paused ([\d.]+)(ms|µs|us|ns|s)\b")
UNITS = {"ms": 1, "µs": 1e-3, "us": 1e-3, "ns": 1e-6, "s": 1e3}


def java(home):
    return str(Path(home) / "bin" / "java")


JAVA_TARGETS = set()


def engine(name, target_vm, directory):
    heap = ["-Xms4g", "-Xmx4g", f"-Xlog:gc:file={Path(directory) / 'gc.log'}"]
    if name.startswith("target"):
        vm = target_vm[name] if isinstance(target_vm, dict) else target_vm
        flags = heap if name in JAVA_TARGETS else []
        return [str(vm), *flags], common.knobs(max_heap="4g", code_cache="0", log="gc")
    if name == "hotspot-g1":
        return [java(common.jdk()), *AIKAR, *heap], {}
    if name == "hotspot-zgc":
        return [java(common.jdk()), "-XX:+UseZGC", *heap], {}
    if name == "graal":
        return [java(suites.graal()), *heap], {}
    raise SystemExit(f"unknown engine {name!r}")


def identity(name, target_vm):
    if name.startswith("target"):
        vm = Path(target_vm[name] if isinstance(target_vm, dict) else target_vm).resolve()
        git = ["git", "-C", str(vm.parent)]
        head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True)
        if head.returncode:
            return f"{vm} (not in a git repository)"
        dirty = subprocess.run([*git, "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        return head.stdout.strip() + (" (dirty)" if dirty else "")
    prefix, _ = engine(name, None, tempfile.gettempdir())
    return subprocess.run([prefix[0], "-version"], capture_output=True, text=True).stderr.strip()


def pauses(directory, output):
    log = Path(directory) / "gc.log"
    text = log.read_text(errors="replace") if log.exists() else output
    found = []
    for line in text.splitlines():
        match = HOTSPOT_PAUSE.search(line)
        if match:
            found.append((float(match.group(1)), "Pause Full" in line))
            continue
        match = GENERIC_PAUSE.search(line)
        if match:
            found.append((float(match.group(1)) * UNITS[match.group(2)], "full collection" in line))
    return found


def ended(proc, directory, timeout):
    code = proc.stop(timeout)
    output = proc.output()
    (Path(directory) / "console.log").write_text(output)
    found = pauses(directory, output)
    full = [ms for ms, is_full in found if is_full]
    return {
        "exit": code,
        "rss_mb": round(proc.hwm_kb / 1024, 1),
        "gc_pauses": len(found),
        "gc_pause_max_ms": max((ms for ms, _ in found), default=0),
        "gc_pause_total_ms": round(sum(ms for ms, _ in found), 1),
        "gc_full_pauses": len(full),
        "gc_full_pause_max_ms": max(full, default=0),
    }


def write_datapack(directory, functions, load=None, tick=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pack.mcmeta").write_text(json.dumps(
        {"pack": {"description": "bench", "min_format": DATA_FORMAT, "max_format": DATA_FORMAT}}
    ))
    tags = directory / "data" / "minecraft" / "tags" / "function"
    tags.mkdir(parents=True, exist_ok=True)
    for tag, function in (("load", load), ("tick", tick)):
        if function:
            (tags / f"{tag}.json").write_text(json.dumps({"values": [f"bench:{function}"]}))
    folder = directory / "data" / "bench" / "function"
    folder.mkdir(parents=True, exist_ok=True)
    for name, lines in functions.items():
        (folder / f"{name}.mcfunction").write_text("\n".join(lines) + "\n")


def extracted():
    marker = EXTRACTED / ".extracted"
    if not marker.exists():
        EXTRACTED.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [java(common.jdk()), "-DbundlerMainClass=net.minecraft.data.Main", "-jar", fetch.SERVER, "--help"],
            cwd=EXTRACTED, env=common.ENV, capture_output=True, check=True,
        )
        marker.write_text(fetch.SERVER_SHA1)
    return EXTRACTED


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def run_dir(name, world=None, properties=None):
    directory = RUNS / name
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    (directory / "eula.txt").write_text("eula=true\n")
    values = PROPERTIES | (properties or {}) | {"server-port": str(free_port())}
    (directory / "server.properties").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    shared = extracted()
    for folder in ("libraries", "versions"):
        (directory / folder).symlink_to(shared / folder)
    if world:
        shutil.copytree(WORLDS / world, directory / "world")
    return directory


def port(directory):
    for line in (Path(directory) / "server.properties").read_text().splitlines():
        if line.startswith("server-port="):
            return int(line.split("=", 1)[1])
    raise RuntimeError("no server-port")


def server(rig, name, directory, target_vm, extra=()):
    prefix, env = engine(name, target_vm, directory)
    return rig.launch([*prefix, "-jar", fetch.SERVER, "nogui", *extra], directory, env, name)


def done(proc, timeout=1800):
    _, moment, line = proc.wait_for(lambda text: "Done (" in text and "For help" in text, timeout)
    return {"done_s": moment - proc.started, "done_internal_s": float(re.search(r"Done \(([\d.]+)s\)", line).group(1))}


def console(proc, command, reply, timeout=600):
    start = len(proc.lines)
    proc.send(command)
    return proc.wait_for(lambda line: reply in line, timeout, start)[2]


def play(proc, directory, body):
    result = {}
    try:
        body(result)
    except Exception as error:
        proc.stop(0)
        tail = "\n".join(line for _, line in proc.lines[-20:])
        raise RuntimeError(f"{error!r}; last console lines:\n{tail}") from error
    except BaseException:
        proc.stop(0)
        raise
    proc.send("stop")
    result.update(ended(proc, directory, 300))
    return result


def worldgen(rig, name, directory, target_vm, args):
    radius = args.radius
    side = 2 * radius + 1
    count = [
        f"execute if loaded {x * 16} 0 {z * 16} run scoreboard players add #loaded bench 1"
        for x in range(-radius, radius + 1) for z in range(-radius, radius + 1)
    ]
    write_datapack(
        Path(directory) / "world" / "datapacks" / "bench",
        {
            "load": ["tick freeze", *[f"gamerule {k} {v}" for k, v in STILL.items()], "scoreboard objectives add bench dummy"],
            "count": ["scoreboard players set #loaded bench 0", *count],
        },
        load="load",
    )
    proc = server(rig, name, directory, target_vm)

    def body(result):
        result.update(done(proc))
        begin = time.monotonic()
        rows = max(1, 256 // side)
        for z0 in range(-radius, radius + 1, rows):
            z1 = min(z0 + rows - 1, radius)
            console(proc, f"forceload add {-radius * 16} {z0 * 16} {radius * 16 + 15} {z1 * 16 + 15}", "to be force loaded")
        while True:
            proc.send("function bench:count")
            start = len(proc.lines)
            proc.send("scoreboard players get #loaded bench")
            _, _, line = proc.wait_for(lambda text: "#loaded has" in text or "Unknown scoreboard" in text, 600, start)
            if "Unknown scoreboard" in line:
                raise RuntimeError("bench datapack did not load")
            if int(re.search(r"#loaded has (\d+)", line).group(1)) == side * side:
                break
            time.sleep(0.2)
        result["worldgen_s"] = time.monotonic() - begin
        result["chunks_per_s"] = side * side / result["worldgen_s"]
        console(proc, "forceload remove all", "Unmarked all force loaded chunks")
        console(proc, "save-all flush", "Saved the game")

    return play(proc, directory, body)


def ring(index):
    angle = 2 * math.pi * index / BOTS
    return round(BOT_RING * math.cos(angle), 2), BOT_HEIGHT, round(BOT_RING * math.sin(angle), 2)


def server_thread(pid):
    for task in Path(f"/proc/{pid}/task").iterdir():
        try:
            if (task / "comm").read_text().strip() == "Server thread":
                return int(task.name)
        except OSError:
            pass
    raise RuntimeError("no 'Server thread' task")


def tick(rig, name, directory, target_vm, args):
    proc = server(rig, name, directory, target_vm)
    bot_log = Path(directory) / "bots.log"
    bots = None

    def body(result):
        nonlocal bots
        result.update(done(proc))
        start = len(proc.lines)
        with open(bot_log, "w") as log:
            bots = subprocess.Popen(
                [java(common.jdk()), BOT, "127.0.0.1", str(port(directory)), str(BOTS), "held" if args.held else "fly"],
                stdout=log, stderr=subprocess.STDOUT, env=common.ENV, start_new_session=True,
            )
        for index in range(BOTS):
            proc.wait_for(lambda line, n=f"bot{index:02d} joined the game": n in line, 300, start)
        for index in range(BOTS):
            x, y, z = ring(index)
            console(proc, f"tp bot{index:02d} {x} {y} {z}", f"Teleported bot{index:02d}")
        time.sleep(args.warmup)
        perf = None
        if args.counters:
            counters = Path(directory) / "perf.csv"
            command = ["perf", "stat", "-x", ",", "-e", "instructions:u,cycles:u,branches:u",
                       "-t", str(server_thread(proc.pid)), "-o", str(counters)]
            perf = subprocess.Popen(command, start_new_session=True)
        line = console(proc, f"tick sprint {args.sprint}", "Sprint completed with", timeout=7200)
        if perf:
            os.killpg(perf.pid, signal.SIGINT)
            perf.wait(30)
            values = {}
            for row in counters.read_text().splitlines():
                fields = row.split(",")
                if len(fields) > 2 and fields[0].replace(".", "").isdigit():
                    values[fields[2].split(":")[0]] = float(fields[0])
            for event in ("instructions", "cycles", "branches"):
                if event in values:
                    result[f"{event}_per_tick_m"] = values[event] / args.sprint / 1e6
        match = re.search(r"Sprint completed with ([\d.,]+) ticks per second, or ([\d.,]+) ms per tick", line)
        result["ticks_per_second"] = float(match.group(1).replace(",", ""))
        result["ms_per_tick"] = float(match.group(2).replace(",", ""))
        line = console(proc, "tick query", "Percentiles:")
        match = re.search(r"P50: ([\d.]+)ms P95: ([\d.]+)ms P99: ([\d.]+)ms. Sample: (\d+)", line)
        result["server_tick_p50_ms"] = float(match.group(1))
        result["server_tick_p95_ms"] = float(match.group(2))
        result["server_tick_p99_ms"] = float(match.group(3))
        result["server_tick_samples"] = int(match.group(4))
        result["bots_failed"] = bot_log.read_text(errors="replace").count("BOT FAIL")

    try:
        result = play(proc, directory, body)
    finally:
        if bots:
            common.kill(bots.pid)
            bots.wait()
    return result


def datagen(rig, name, directory, target_vm, args):
    prefix, env = engine(name, target_vm, directory)
    argv = [*prefix, "-DbundlerMainClass=net.minecraft.data.Main", "-jar", fetch.SERVER, "--all", "--output", "generated"]
    proc = rig.launch(argv, directory, env, name)
    try:
        proc.process.wait(7200)
    except subprocess.TimeoutExpired:
        pass
    seconds = time.monotonic() - proc.started
    result = ended(proc, directory, 0)
    result["datagen_s"] = seconds
    return result


def upgrade(rig, name, directory, target_vm, args):
    proc = server(rig, name, directory, target_vm, ["--forceUpgrade", "--recreateRegionFiles"])

    def body(result):
        result.update(done(proc, 7200))
        result["upgrade_s"] = result["done_s"]

    return play(proc, directory, body)


SCENARIOS = {"worldgen": worldgen, "tick": tick, "datagen": datagen, "upgrade": upgrade}
SCENARIO_WORLDS = {"tick": "tick", "upgrade": "gen32"}


def farm():
    y = 200
    commands = [
        f"fill -16 {y} -16 79 {y} 63 stone",
        f"fill -16 {y + 1} -16 79 {y + 8} 63 air",
    ]
    for unit in range(8):
        x = 4 * unit
        commands += [
            f"fill {x} {y + 1} 0 {x} {y + 1} 15 redstone_lamp",
            f"fill {x + 1} {y + 1} 0 {x + 1} {y + 1} 15 observer[facing=east]",
            f"fill {x + 2} {y + 1} 0 {x + 2} {y + 1} 15 observer[facing=west]",
            f"fill {x + 3} {y + 1} 0 {x + 3} {y + 1} 15 redstone_lamp",
        ]
    ring_cells = (
        [(x, 0, "east") for x in range(40, 44)] + [(44, z, "south") for z in range(0, 4)]
        + [(x, 4, "west") for x in range(44, 40, -1)] + [(40, z, "north") for z in range(4, 0, -1)]
    )
    for x, z, facing in ring_cells:
        commands.append(f"setblock {x} {y + 1} {z} hopper[facing={facing}]")
    for x, z, _ in ring_cells[::4]:
        commands.append(f"item replace block {x} {y + 1} {z} container.0 with cobblestone 64")
    commands += [
        f"fill -1 {y - 1} 19 8 {y + 3} 28 glass",
        f"fill 0 {y - 1} 20 7 {y - 1} 27 chest[type=single]",
        f"fill 0 {y} 20 7 {y} 27 hopper[facing=down]",
        f"fill 0 {y + 1} 20 7 {y + 2} 27 air",
    ]
    for index in range(48):
        commands.append(f"summon chicken {index % 8 + 0.5} {y + 1} {20 + index // 8 + 0.5}")
    commands += [
        f"fill 40 {y} 20 57 {y} 37 grass_block",
        f"fill 40 {y + 1} 20 57 {y + 1} 37 oak_fence",
        f"fill 41 {y + 1} 21 56 {y + 1} 36 air",
    ]
    for index in range(96):
        commands.append(f"summon cow {41.5 + index % 16} {y + 1} {21.5 + index // 16 * 2}")
    commands.append("say BENCH FARM BUILT")
    return commands


def save_world(directory, destination):
    temporary = destination.with_name(destination.name + f".tmp-{os.getpid()}")
    shutil.rmtree(temporary, ignore_errors=True)
    shutil.copytree(Path(directory) / "world", temporary)
    shutil.rmtree(temporary / "datapacks", ignore_errors=True)
    os.replace(temporary, destination)


def prepare(args):
    rig = rigging.Rig()
    with rig:
        gen32 = WORLDS / "gen32"
        if not gen32.exists():
            print("preparing gen32", flush=True)
            directory = run_dir("prepare-gen32")
            try:
                worldgen(rig, "hotspot-g1", directory, None, args)
                save_world(directory, gen32)
            finally:
                shutil.rmtree(directory, ignore_errors=True)
        tick_world = WORLDS / "tick"
        if not tick_world.exists():
            print("preparing tick", flush=True)
            directory = run_dir("prepare-tick", "gen32")
            write_datapack(
                directory / "world" / "datapacks" / "bench",
                {"load": [f"gamerule {k} {v}" for k, v in DEFAULTS.items()]},
                load="load",
            )
            proc = server(rig, "hotspot-g1", directory, None)

            def body(result):
                done(proc)
                console(proc, "forceload add -16 -16 79 63", "force loaded")
                for command in farm():
                    proc.send(command)
                proc.wait_for(lambda line: "BENCH FARM BUILT" in line, 600)
                console(proc, "tick sprint 200", "Sprint completed with")
                console(proc, "save-all flush", "Saved the game")

            try:
                result = play(proc, directory, body)
                if result["exit"] != 0:
                    raise RuntimeError(f"preparing the tick world: exit {result['exit']}")
                save_world(directory, tick_world)
            finally:
                shutil.rmtree(directory, ignore_errors=True)

def machine():
    info = {"hostname": socket.gethostname(), "os": platform.system().lower(), "arch": platform.machine()}
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                info["cpu"] = line.split(":", 1)[1].strip()
                break
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                info["memory_gb"] = round(int(line.split()[1]) / 2**20, 1)
    except OSError:
        pass
    return info


def summarize(scenario, samples, engines):
    summary = {}
    references = [name for name in REFERENCES if name in engines]
    for metric, lower in METRICS[scenario].items():
        stats = {}
        for name in engines:
            values = [s[metric] for s in samples if s["engine"] == name and "error" not in s and metric in s]
            if values:
                stats[name] = rigging.summarize(values)
        if not stats:
            continue
        verdict = rigging.verdict(stats, "target", references, lower) if "target" in engines else None
        target, hotspot = stats.get("target"), stats.get("hotspot-g1")
        comparable = target and hotspot and metric not in common.NOT_COMPARED
        relative = common.relative(target["median"], hotspot["median"], lower) if comparable else None
        summary[metric] = {"lower_is_better": lower, "engines": stats, "verdict": verdict, "vs_hotspot": relative}
        line = " | ".join(
            f"{name} {s['median']:.3f} [{s['ci'][0]:.3f}, {s['ci'][1]:.3f}]" for name, s in stats.items()
        )
        if relative:
            line += f" | {relative['pct']:+.1f}% vs HotSpot"
        if verdict:
            ratio = "n/a" if verdict["ratio"] is None else f"{verdict['ratio']:.3f}"
            line += f" -> {'WIN' if verdict['win'] else 'no win'} vs {verdict['best_baseline']} (ratio {ratio})"
        print(f"{scenario} {metric}: {line}", flush=True)
    return summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Minecraft 26.3 scenarios: TARGET vs. reference JVMs.")
    parser.add_argument("scenarios", nargs="+", choices=["prepare", *SCENARIOS])
    parser.add_argument("--engines", default=",".join(ENGINES), help="comma-separated engine names")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--target", type=Path, help="target toolchain to package into a VM (see bench/run.py)")
    parser.add_argument("--target-is-java", action="store_true", help="--target is already a java launcher")
    parser.add_argument("--target-vm", type=Path, help="pre-built target VM, bypassing packaging")
    parser.add_argument("--target-base", type=Path, help="second target VM, measured as engine target-base")
    parser.add_argument("--json", type=Path, default=ROOT / "target" / "mc" / "results.json")
    parser.add_argument("--radius", type=int, default=32, help="worldgen square half-width in chunks")
    parser.add_argument("--sprint", type=int, default=6000, help="tick sprint length in ticks")
    parser.add_argument("--warmup", type=float, default=90, help="seconds of bot play before sprinting")
    parser.add_argument("--counters", action="store_true", help="perf-stat the server thread per sprint tick")
    parser.add_argument("--held", action="store_true", help="bots hold fixed points instead of flying circles")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    args.engines = [name for name in args.engines.split(",") if name]
    if args.target_base and "target-base" not in args.engines:
        args.engines.append("target-base")
    return args


def main(argv=None):
    args = parse_args(argv)
    rigging.hold_quiet()
    if importlib.util.find_spec("numpy") is None and args.scenarios != ["prepare"]:
        sys.exit("numpy is required for the confidence intervals: pip install numpy")
    if not fetch.SERVER.exists():
        sys.exit("Minecraft assets missing: run bench/mc/fetch.py first")
    scenarios = list(dict.fromkeys(args.scenarios))
    target_vm = None
    if any(name.startswith("target") for name in args.engines) and scenarios != ["prepare"]:
        if args.target_vm:
            target_vm = args.target_vm
        elif args.target:
            target_vm = common.generic_vm(args.target, args.target_is_java)
            if args.target_is_java:
                JAVA_TARGETS.add("target")
        else:
            sys.exit("a target engine is selected: pass --target or --target-vm")
        if args.target_base:
            target_vm = {"target": target_vm, "target-base": args.target_base}
    if "prepare" in scenarios:
        prepare(args)
        scenarios.remove("prepare")
    if not scenarios:
        return 0

    report = {
        "engines": {name: identity(name, target_vm) for name in args.engines},
        "runs": args.runs,
        "seed": args.seed,
        "machine": machine() | {"reference_jdks": {
            name: common.release(Path(engine(name, None, ".")[0][0]).parent.parent)
            for name in args.engines if not name.startswith("target")
        }},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scenarios": {},
    }
    with rigging.Rig() as rig:
        for scenario in scenarios:
            samples = []
            report["scenarios"][scenario] = {"samples": samples}
            for index, name in rigging.schedule(args.engines, args.runs, args.seed):
                directory = run_dir(f"{scenario}-{name}-{index}", SCENARIO_WORLDS.get(scenario))
                since = time.monotonic()
                try:
                    sample = SCENARIOS[scenario](rig, name, directory, target_vm, args)
                    if sample.get("exit") not in (0, None):
                        sample["error"] = f"exit {sample['exit']}"
                except Exception as error:
                    sample = {"error": repr(error)}
                sample = {"engine": name, "index": index, **sample}
                print(json.dumps(sample), flush=True)
                sample["telemetry"] = rig.telemetry(since, time.monotonic())
                samples.append(sample)
                common.write_json(args.json, report)
                shutil.rmtree(directory, ignore_errors=True)
            report["scenarios"][scenario]["summary"] = summarize(scenario, samples, args.engines)
            common.write_json(args.json, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
