# Running VibeJVMBench for a user

You are an agent asked to benchmark a user's JVM / JVM-like runtime / AOT toolchain (the **target**) and
produce a submission file they can PR to https://github.com/KmForkOrg/VibeJVMBench. This file tells you
how to run it safely, what to ask, and exactly what to tell the user at the end.

## Ground rules (read first)

- **Do not edit the harness or the results** to make numbers look better, and do not hand-edit the export.
  If something fails, report it; don't patch around it in the measured code.
- **Do not use `--reuse-hotspot` for a submission**, and never drop `hotspot-g1` from `--engines`. The
  results site ranks targets only by their percentage against HotSpot G1 measured in the same run on the
  same machine; without HotSpot in the run the submission cannot be ranked.
- **Suites (DaCapo/Renaissance) are opt-in**: ~6.3 GB + 0.4 GB of downloads (DaCapo extracts to ~13 GB
  under `target/`) and hours of runtime. Only run them if the user explicitly wants them.

## What to ask the user before starting

1. **Path to the target.** Either a compiler/packager binary, or a ready-to-run `java`-style launcher.
2. **Is it a `java` launcher?** If yes, pass `--target-is-java`. If it is a compiler/packager, the harness
   runs `<target> build --jdk <JDK> -o <out>/java` to package a VM. If their tool uses a different CLI or
   different env-var names for heap/code-cache/GC-log, write a profile (see "Target profiles") — ask them
   for the right invocation rather than guessing.
3. **Target name** for the submission: lowercase letters, digits and hyphens, starting with a letter or
   digit (`^[a-z0-9][a-z0-9-]*$`), e.g. `my-aot-jvm`. This becomes the `jvms/<name>/` folder.
4. Optional: a version/commit string, a short description, a source URL.
5. **Which parts to run**: the synthetic benchmarks (`bench/run.py`, ~15–30 min), the Minecraft server
   scenarios (`bench/mc/run.py`, hours at defaults), or both. Default suggestion: synthetic first.

## Prerequisites (check, don't install system-wide without asking)

- Linux or macOS, Python 3.12+, network access for first-run downloads (Lucene jar ~3 MB; Minecraft
  server jar ~60 MB; GraalVM 333 MB only if `--graal`).
- **`JAVA_HOME`** (or `BENCH_JDK_HOME`) pointing at a JDK 25 home — the HotSpot reference. Both tools stop
  with an error if neither is set. On many distros it is unset; find a JDK under `/usr/lib/jvm/` and export
  it for your commands (`export JAVA_HOME=/usr/lib/jvm/<jdk>`), don't edit the user's shell profile.
- **numpy** — only for `bench/mc/run.py`. If missing, use a venv (e.g. `python3 -m venv .venv &&
  .venv/bin/pip install numpy`) and run mc with `.venv/bin/python`. `bench/run.py` and `bench/export.py`
  need only the standard library.
- RAM: synthetic benchmarks ~1 GB per JVM; Minecraft server runs take a full 4 GB heap immediately, and
  the `tick` scenario adds a ~1 GB bot JVM (~5 GB total).

Run everything from the repository root. Quick sanity check (seconds, no JVMs):

```sh
python3 bench/test_run.py && python3 bench/mc/test_mc.py
```

## 1. Synthetic benchmarks (`bench/run.py`)

Measures 14 microbenchmarks, a Lucene-based real program, Vector API and runtime-class-loading kernels,
and process startup, each against HotSpot G1, interleaved run by run. ~15–30 min at defaults, one JVM at
a time.

```sh
export JAVA_HOME=/usr/lib/jvm/<jdk-25>
python3 bench/run.py --target /path/to/target            # compiler/packager target
python3 bench/run.py --target /path/to/java --target-is-java   # plain java launcher
```

- Results (raw timings + `vs_hotspot` ratios) go to `target/bench-work/bench/results.json`, rewritten
  after every benchmark, so a killed run still leaves a valid partial file.
- Each line prints the target's time, HotSpot's, and the ratio; the end prints
  `geometric mean TARGET/g1: <r> (<pct>% vs HotSpot)` — positive % means the target is faster.
- Useful flags: positional filters (`main`, `real`, `vector`, `runtime`, `startup`, or a name substring
  such as `Escape`) to run a subset; `--runs N` / `--samples N` for more/fewer repetitions; `--timeout S`.
  Add `suites` (or `dacapo` / `renaissance` / `dacapo/avrora`) only if the user asked for suites.
- A checksum mismatch aborts the run: it means the target computed a different answer than HotSpot for
  the same program — a correctness bug in the target. Report it to the user verbatim; it is not flakiness.
- A target that crashes/times out on one benchmark is recorded as an `error` for that benchmark and the
  run continues.

## 2. Minecraft server scenarios (`bench/mc/run.py`) — optional, long

Runs a real Minecraft 26.3 dedicated server on the target vs. HotSpot G1. Server-side only.

```sh
python3 bench/mc/fetch.py                                   # once: server jar (~60 MB), verified
.venv/bin/python bench/mc/run.py prepare                    # once: builds reference worlds (~10–20 min)
.venv/bin/python bench/mc/run.py worldgen tick datagen upgrade --target /path/to/target [--target-is-java]
```

- Defaults: engines `target,hotspot-g1`, `--runs 10` per engine, worldgen radius 32 (4225 chunks), tick
  scenario with 20 bots, 90 s warmup and a 6000-tick sprint. A full run of all four scenarios is several
  hours. Give the user the estimate and offer fewer `--runs` (e.g. `--runs 3`) or a subset of scenarios.
- `prepare` builds `~/.cache/vibejvmbench/mc/worlds-2/{gen32,tick}` once and reuses them; don't delete them
  between runs (they make runs comparable). If they were built with a non-default `--radius`, delete
  `worlds-2/` and re-run `prepare` before a real submission.
- Report: `target/mc/results.json`, rewritten after every sample. Each scenario prints per-metric medians
  with 95% confidence intervals and `<pct>% vs HotSpot`.
- A failed sample is recorded with an `error` (including the server console's last lines) and the run
  continues; run directories are always cleaned up.

## 3. Export the submission (`bench/export.py`)

```sh
python3 bench/export.py --name <target-name> \
    --bench target/bench-work/bench/results.json \
    --mc target/mc/results.json \
    --version "<commit or tag>" --description "<one line>" --source-url "<url>" \
    --harness-commit "$(git rev-parse --short HEAD 2>/dev/null)"
```

- Pass only the parts that were run (`--bench` and/or `--mc`); omit optional fields you don't know.
- It validates against `schema/result.schema.json` and refuses to write an invalid file. It prints the
  path it wrote: `jvms/<target-name>/<UTC timestamp>.json` (e.g. `jvms/my-aot-jvm/2026-10-18T14-05-09Z.json`;
  colons are replaced by hyphens on purpose).
- If it warns `no HotSpot G1 comparison`, the run lacked HotSpot results — re-run properly; the site
  cannot rank that file.

## 4. What to tell the user

After exporting, give the user: the file path, a short summary of the headline percentages, and the
submission instructions. Use this shape (fill in the real values; do not invent numbers):

> Your submission is ready: `jvms/<target-name>/<timestamp>.json` (in this repository).
>
> Headline vs HotSpot G1 on your machine (positive = faster than HotSpot):
> microbenchmarks <pct>%, real programs <pct>%, startup <pct>%[, Minecraft tick <pct>% ms/tick, …].
> [Any benchmarks that failed or hit checksum mismatches: …]
>
> To submit it, open a pull request to https://github.com/KmForkOrg/VibeJVMBench adding that file at
> `jvms/<target-name>/<timestamp>.json` — same folder and filename. Don't rename it or edit its contents;
> the website combines every file under `jvms/` and ranks targets by these HotSpot-relative percentages.

Take the percentages from the export's `vs_hotspot` (top level: `groups.micro`, `groups.real`,
`startup`; per scenario: `scenarios.<name>.vs_hotspot`). Do not open the PR yourself unless the user asks
you to; it is published under their account.

## Target profiles (only if the default packaging/knobs don't fit)

A JSON file passed with `--target-profile FILE` (or `$BENCH_TARGET_PROFILE`) to `bench/run.py`:

```json
{
  "build_args": ["{target}", "package", "--java-home", "{jdk}", "--output", "{output}"],
  "knobs": {"max_heap": "MYVM_HEAP", "code_cache": null, "log": "MYVM_GC_LOG"},
  "extra_artifacts": ["libmyvm-runtime.so"]
}
```

- `build_args`: how to package a `java`-compatible launcher at `{output}` from JDK `{jdk}`.
- `knobs`: the target's env-var names for max heap / code-cache size / GC log (`null` = it has none).
  Defaults are `BENCH_TARGET_MAX_HEAP`, `BENCH_TARGET_CODE_CACHE`, `BENCH_TARGET_LOG`.
- `extra_artifacts`: files next to the target binary whose content affects the built VM (for caching).

Get these from the user or the target's own docs; don't guess.

## Troubleshooting

| Symptom                                        | Cause / action                                                              |
| ---------------------------------------------- | --------------------------------------------------------------------------- |
| `set JAVA_HOME (or BENCH_JDK_HOME)`            | Export `JAVA_HOME` for the command (see Prerequisites).                     |
| `--target is required unless --graal-only`     | Ask the user for the target path.                                           |
| `numpy is required`                            | Run mc with a venv that has numpy.                                          |
| `Minecraft assets missing`                     | Run `python3 bench/mc/fetch.py` first.                                      |
| Checksum mismatch abort                        | Target miscomputes that program; report to the user, don't retry endlessly. |
| `<target> build … failed`                      | Packaging CLI differs; ask the user and write a target profile.             |
| Numbers look wildly off for a plain JDK target | Check other load on the machine; re-run that subset.                        |
| `jvms/` file rejected by the exporter          | Read the printed schema errors; usually a bad `--name`.                     |
