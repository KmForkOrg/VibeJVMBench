#!/usr/bin/env python3
"""Behavioral contract for run.py's parsers, metrics and gate (SPEC §8). No JVMs, no network."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common
import export
import run
import suites


def dacapo(name="avrora"):
    lines = [f"===== DaCapo 23.11-MR2 {name} completed warmup {i} in {i} msec =====" for i in range(1, 10)]
    lines.append(f"===== DaCapo 23.11-MR2 {name} PASSED in 10 msec =====")
    return lines


def renaissance(name="scrabble", termination="normal"):
    return {
        "data": {name: {"termination": termination, "results": [{"duration_ns": 2_000_000}] * 10}},
        "environment": {"vm": {"termination": "normal"}},
    }


def row(steady_ms, steady_samples, workload="w"):
    return {"workload": workload, "engines": {"target": {"steady_ms": steady_ms, "steady_samples": steady_samples}}}


def doc(**rows):
    return {"benchmarks": rows}


class Cli(unittest.TestCase):
    def test_help(self):
        process = subprocess.run([sys.executable, str(Path(run.__file__)), "--help"], capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)


class Selection(unittest.TestCase):
    def test_suites_are_opt_in(self):
        self.assertFalse(run.suite_selected("dacapo", "avrora", []))
        self.assertFalse(run.suite_selected("dacapo", "avrora", ["Escape"]))
        for filters in (["suites"], ["dacapo"], ["DaCapo/avr"], ["avrora"]):
            with self.subTest(filters=filters):
                self.assertTrue(run.suite_selected("dacapo", "avrora", filters))

    def test_hotspot_is_measured_fresh_by_default(self):
        args = run.parse_args(["--target", "/usr/bin/java", "--target-is-java"])
        self.assertFalse(args.reuse_hotspot)


class DaCapo(unittest.TestCase):
    def parse(self, lines):
        return suites.parse("dacapo", "avrora", "\n".join(lines))

    def test_accepts_warmups_then_passed(self):
        self.assertEqual(self.parse(dacapo()), [float(i) for i in range(1, 11)])

    def test_rejects_truncated(self):
        self.assertRaises(ValueError, self.parse, dacapo()[:-1])

    def test_rejects_reversed(self):
        self.assertRaises(ValueError, self.parse, dacapo()[::-1])

    def test_rejects_failed(self):
        lines = dacapo()
        lines[-1] = lines[-1].replace("PASSED", "FAILED")
        self.assertRaises(ValueError, self.parse, lines)

    def test_rejects_uncaught_exception(self):
        lines = dacapo() + ['Exception in thread "main" java.lang.IllegalStateException']
        self.assertRaises(ValueError, self.parse, lines)


class Renaissance(unittest.TestCase):
    def test_accepts_report(self):
        self.assertEqual(suites.parse("renaissance", "scrabble", "", renaissance()), [2.0] * 10)

    def test_rejects_uncaught_exceptions(self):
        for output in ('Exception in thread "worker"', "Uncaught error from thread [pool-1]"):
            with self.subTest(output=output):
                self.assertRaises(ValueError, suites.parse, "renaissance", "scrabble", output, renaissance())

    def test_rejects_abnormal_termination(self):
        report = renaissance(termination="failure")
        self.assertRaises(ValueError, suites.parse, "renaissance", "scrabble", "", report)


class Metrics(unittest.TestCase):
    def test_peak_and_steady(self):
        record = run.result([[100, 50, 20, 11, 10, 10, 10, 10, 10, 10]], True)
        self.assertEqual(record["steady_ms"], 10)
        self.assertEqual(record["iter0_ms"], 100)
        self.assertEqual(record["peak_iteration"], 2)
        self.assertEqual(record["peak_ms"], 150)
        self.assertEqual(record["steady_samples"], [10] * 5)
        self.assertEqual(
            run.real_result(record["samples"]), "time to peak round 2, 150.0 ms; steady 10.0 ms"
        )

    def test_peak_not_reached(self):
        record = run.result([[100, 1] * 20], True)
        self.assertIsNone(record["peak_iteration"])
        self.assertTrue(run.real_result(record).startswith("time to peak not reached"))

    def test_flat_samples(self):
        self.assertEqual(run.result([1, 2, 3])["steady_ms"], 2)
        self.assertEqual(run.result([1, 2, 3])["steady_samples"], [1, 2, 3])

    def test_steady_takes_larger_half(self):
        self.assertEqual(run.steady([9, 1, 2, 3, 4]), 3)

    def test_rounds_protocol(self):
        times, checksum = run.parse_rounds(b"round 0 1.5 ms\nround 1 2.0 ms\nchecksum -7\n", 2)
        self.assertEqual((times, checksum), ([1.5, 2.0], b"checksum -7\n"))
        for bad in (
            b"round 1 1.5 ms\nround 0 2.0 ms\nchecksum 7\n",
            b"round 0 1.5 ms\nchecksum 7\n",
            b"round 0 1.5 ms\nround 1 0 ms\nchecksum 7\n",
            b"round 0 1.5 ms\nround 1 2.0 ms\nchecksum 7\nextra\n",
            b"round 0 1.5 ms\nround 1 2.0 ms\nchecksum 7",
        ):
            with self.subTest(bad=bad):
                self.assertRaises(RuntimeError, run.parse_rounds, bad, 2)


class Gate(unittest.TestCase):
    baseline = doc(a=row(100, [99, 100, 101]))

    def test_within_three_percent_passes(self):
        self.assertFalse(run.gate(doc(a=row(102, [102.5, 102, 103])), self.baseline))

    def test_slower_disjoint_fails(self):
        self.assertTrue(run.gate(doc(a=row(110, [109, 110, 111])), self.baseline))

    def test_slower_overlapping_passes(self):
        self.assertFalse(run.gate(doc(a=row(110, [100, 110, 111])), self.baseline))

    def test_faster_passes(self):
        self.assertFalse(run.gate(doc(a=row(90, [89, 90, 91])), self.baseline))

    def test_empty_current_fails(self):
        self.assertTrue(run.gate(doc(), self.baseline))

    def test_failed_target_fails(self):
        current = doc(a={"workload": "w", "engines": {"target": {"error": "exit 1"}}})
        self.assertTrue(run.gate(current, self.baseline))

    def test_all_failed_baseline_fails_against_itself(self):
        failed = doc(a={"workload": "w", "engines": {"target": {"error": "exit 1"}}})
        self.assertTrue(run.gate(failed, failed))

    def test_changed_workload_fails(self):
        self.assertTrue(run.gate(doc(a=row(100, [99, 100, 101], workload="v")), self.baseline))

    def test_failed_baseline_rows_are_skipped(self):
        baseline = doc(a=row(100, [99, 100, 101]), b={"workload": "w", "engines": {"target": {"error": "x"}}})
        self.assertFalse(run.gate(doc(a=row(100, [99, 100, 101])), baseline))


class Export(unittest.TestCase):
    results = {
        "machine": "host",
        "os": "linux",
        "arch": "x86_64",
        "jdks": {"g1": 'JAVA_VERSION="25"'},
        "target": "/opt/vm/java",
        "target_sha256": "0" * 64,
        "target_toolchain": "abc",
        "target_is_java": True,
        "benchmarks": {
            "Escape": {"group": "main", "workload": "w", "engines": {
                "target": {"steady_ms": 10, "steady_samples": [9, 10, 11]},
                "g1": {"steady_ms": 12, "steady_samples": [11, 12, 13]},
            }},
            "Dispatch": {"group": "main", "workload": "w", "engines": {
                "target": {"error": "exit 1"}, "g1": {"steady_ms": 5, "steady_samples": [5]},
            }},
        },
        "startup": {"samples_ms": [30.0, 20.0, 25.0, 22.0, 21.0], "hotspot_samples_ms": [44.0] * 5},
    }
    report = {
        "engines": {"target": "a1b2c3d", "hotspot-g1": "openjdk 25"},
        "runs": 2,
        "seed": 1,
        "generated_at": "2026-10-17T17:43:00Z",
        "machine": {"hostname": "host", "os": "linux", "arch": "x86_64", "reference_jdks": {}},
        "scenarios": {"worldgen": {
            "samples": [
                {"engine": "target", "index": 0, "worldgen_s": 10.0},
                {"engine": "target", "index": 1, "error": "RuntimeError()"},
                {"engine": "hotspot-g1", "index": 0, "worldgen_s": 9.0},
            ],
            "summary": {"worldgen_s": {
                "lower_is_better": True,
                "engines": {"target": {"median": 10.0, "ci": [10.0, 10.0], "n": 1},
                            "hotspot-g1": {"median": 9.0, "ci": [9.0, 9.0], "n": 1}},
                "verdict": {"best_baseline": "hotspot-g1", "win": False, "ratio": 10 / 9},
            }},
        }},
    }

    def test_example_is_valid(self):
        example = json.loads((export.SCHEMA.parent / "example.json").read_text())
        self.assertEqual(export.validate(example), [])

    def test_validator_rejects(self):
        self.assertTrue(export.validate({"schema_version": 2}))
        example = json.loads((export.SCHEMA.parent / "example.json").read_text())
        example["machine"]["gpu"] = "none"
        self.assertTrue(export.validate(example))

    def test_bench(self):
        submission = export.export_bench(self.results, "my-vm")
        self.assertEqual(export.validate(submission), [])
        self.assertEqual(submission["startup_ms"]["median"], 22.0)
        self.assertNotIn("measurement_grade", submission)
        self.assertTrue(submission["target"]["is_java_launcher"])
        relative = submission["vs_hotspot"]
        self.assertAlmostEqual(relative["benchmarks"]["Escape"]["ratio"], 1.2)
        self.assertNotIn("Dispatch", relative["benchmarks"])
        self.assertEqual(relative["groups"]["micro"]["n"], 1)
        self.assertAlmostEqual(relative["startup"]["pct"], 100.0)

    def test_mc(self):
        submission = export.export_mc(self.report, "my-vm")
        self.assertEqual(export.validate(submission), [])
        worldgen = submission["scenarios"]["worldgen"]
        self.assertEqual(worldgen["failed_runs"], 1)
        self.assertEqual(worldgen["target_metrics"]["worldgen_s"]["samples"], [10.0])
        self.assertEqual(worldgen["verdicts"]["worldgen_s"]["baseline"], "hotspot-g1")
        self.assertEqual(submission["target"]["version"], "a1b2c3d")
        self.assertAlmostEqual(worldgen["vs_hotspot"]["worldgen_s"]["pct"], -10.0)

    def test_merge(self):
        merged = export.merge(export.export_bench(self.results, "my-vm"), export.export_mc(self.report, "my-vm"))
        self.assertEqual(export.validate(merged), [])
        self.assertIn("harness", merged)
        self.assertIn("scenarios", merged)

    def test_relative_direction(self):
        self.assertEqual(common.relative(5, 10)["ratio"], 2.0)
        self.assertEqual(common.relative(120, 60, lower_is_better=False)["pct"], 100.0)
        self.assertIsNone(common.relative(0, 10))
        self.assertIsNone(common.relative(5, None))
        self.assertAlmostEqual(common.geomean_relative([{"ratio": 2.0}, {"ratio": 0.5}, None])["ratio"], 1.0)

    def test_filename_has_no_colons(self):
        self.assertEqual(export.filename("2026-10-17T17:43:00Z"), "2026-10-17T17-43-00Z.json")

    def test_rejects_bad_name(self):
        self.assertRaises(ValueError, export.export_bench, self.results, "My VM")


if __name__ == "__main__":
    unittest.main()
