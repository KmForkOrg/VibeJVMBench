#!/usr/bin/env python3
"""Unit tests for the Minecraft rig's pure logic. No servers, JVMs, sudo or network."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rig

_spec = importlib.util.spec_from_file_location("mc_run", Path(__file__).with_name("run.py"))
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)

HAS_NUMPY = importlib.util.find_spec("numpy") is not None


class Statistics(unittest.TestCase):
    def test_schedule_runs_every_engine_each_round(self):
        order = rig.schedule(["a", "b", "c"], 4, seed=1)
        self.assertEqual(len(order), 12)
        for index in range(4):
            self.assertEqual(sorted(e for i, e in order if i == index), ["a", "b", "c"])
        self.assertEqual(order, rig.schedule(["a", "b", "c"], 4, seed=1))

    def test_verdict(self):
        stats = {
            "target": {"median": 8, "ci": [7, 9], "n": 5},
            "hotspot-g1": {"median": 10, "ci": [9, 11], "n": 5},
            "graal": {"median": 12, "ci": [11, 13], "n": 5},
        }
        verdict = rig.verdict(stats, "target", ["hotspot-g1", "graal"])
        self.assertEqual(verdict, {"best_baseline": "hotspot-g1", "win": True, "ratio": 0.8})
        higher = rig.verdict(stats, "target", ["hotspot-g1", "graal"], lower_is_better=False)
        self.assertEqual((higher["best_baseline"], higher["win"]), ("graal", False))
        self.assertIsNone(rig.verdict(stats, "target", ["hotspot-zgc"]))

    def test_partial_clearance_is_not_a_win(self):
        stats = {"target": {"median": 8, "ci": [7, 10.5], "n": 5}, "hotspot-g1": {"median": 10, "ci": [9, 11], "n": 5}}
        self.assertFalse(rig.verdict(stats, "target", ["hotspot-g1"])["win"])

    @unittest.skipUnless(HAS_NUMPY, "numpy not installed")
    def test_summarize(self):
        self.assertIsNone(rig.summarize([]))
        summary = rig.summarize([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual((summary["median"], summary["n"]), (3.0, 5))
        self.assertLessEqual(summary["ci"][0], 3.0)
        self.assertGreaterEqual(summary["ci"][1], 3.0)
        self.assertEqual(summary, rig.summarize([1.0, 2.0, 3.0, 4.0, 5.0]))


class Processes(unittest.TestCase):
    def test_launch_wait_and_stop(self):
        with rig.Rig() as machine:
            engine = machine.launch(
                [sys.executable, "-u", "-c", "import sys; print('ready'); print(sys.stdin.readline().strip())"],
                tempfile.gettempdir(), {}, "probe",
            )
            index, _, line = engine.wait_for(lambda text: text == "ready", 30)
            engine.send("echoed")
            engine.wait_for(lambda text: text == "echoed", 30, index + 1)
            self.assertEqual(engine.stop(30), 0)
            self.assertEqual(engine.output(), "ready\necho" + "ed")


class GcPauses(unittest.TestCase):
    def test_hotspot_and_generic_forms(self):
        output = "\n".join([
            "[1.2s][info][gc] GC(3) Pause Young (Normal) (G1 Evacuation Pause) 100M->20M(4096M) 3.250ms",
            "[9.9s][info][gc] GC(4) Pause Full (System.gc()) 100M->10M(4096M) 120.5ms",
            "gc: paused 250us (young)",
            "gc: full collection paused 1.5s",
            "unrelated line 5ms",
        ])
        with tempfile.TemporaryDirectory() as directory:
            found = run.pauses(directory, output)
        self.assertEqual(found, [(3.25, False), (120.5, True), (0.25, False), (1500.0, True)])


class Layout(unittest.TestCase):
    def test_bot_ring(self):
        points = [run.ring(i) for i in range(run.BOTS)]
        self.assertEqual(len(set(points)), run.BOTS)
        for x, _, z in points:
            self.assertAlmostEqual((x * x + z * z) ** 0.5, run.BOT_RING, places=1)

    def test_farm(self):
        commands = run.farm()
        self.assertEqual(sum(c.startswith("summon chicken") for c in commands), 48)
        self.assertEqual(sum(c.startswith("summon cow") for c in commands), 96)
        self.assertEqual(sum(c.startswith("setblock") and "hopper" in c for c in commands), 16)
        self.assertEqual(commands[-1], "say BENCH FARM BUILT")

    def test_java_targets_get_reference_heap_flags(self):
        plain, _ = run.engine("target", "/opt/vm", "/tmp/run")
        self.assertEqual(plain, ["/opt/vm"])
        run.JAVA_TARGETS.add("target")
        try:
            java, _ = run.engine("target", "/usr/bin/java", "/tmp/run")
        finally:
            run.JAVA_TARGETS.discard("target")
        self.assertEqual(java, ["/usr/bin/java", "-Xms4g", "-Xmx4g", "-Xlog:gc:file=/tmp/run/gc.log"])

    def test_default_engines_are_light(self):
        self.assertEqual(run.parse_args(["tick"]).engines, ["target", "hotspot-g1"])

    def test_bots_can_join(self):
        self.assertEqual(run.PROPERTIES["white-list"], "false")
        self.assertEqual(run.PROPERTIES["online-mode"], "false")

    def test_gamerules_cover_the_same_rules(self):
        self.assertEqual(set(run.STILL), set(run.DEFAULTS))


if __name__ == "__main__":
    unittest.main()
