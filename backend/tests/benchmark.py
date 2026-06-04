#!/usr/bin/env python3
"""
Benchmark the Quick Add parse endpoint across Ollama models.

Runs the full test_parse.py suite against each model, records per-test
timing and pass/fail, then writes benchmark_report.md.

Usage (from backend directory):
    python benchmark.py
    python benchmark.py --models phi4-mini,llama3.2   # subset
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── Model catalogue ──────────────────────────────────────────────────────────

MODELS = [
    {
        "name":    "phi4-mini",
        "label":   "phi4-mini",
        "vendor":  "Microsoft",
        "params":  "3.8B",
        "context": "4K",
    },
    {
        "name":    "llama3.2",
        "label":   "llama3.2 (3B)",
        "vendor":  "Meta",
        "params":  "3.2B",
        "context": "128K",
    },
    {
        "name":    "mistral-nemo",
        "label":   "Mistral NeMo",
        "vendor":  "Mistral AI",
        "params":  "12B",
        "context": "128K",
    },
]

VENV_PYTHON = Path(__file__).parent.parent / "venv" / "bin" / "python3.14"
PYTEST = str(VENV_PYTHON.parent / "pytest")


# ── Helpers ──────────────────────────────────────────────────────────────────

def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def ollama_model_size(name: str) -> str:
    """Read disk size from `ollama list`."""
    result = run(["ollama", "list"])
    for line in result.stdout.splitlines():
        if name in line:
            # columns: NAME  ID  SIZE  MODIFIED
            m = re.search(r"(\d+\.?\d*\s*(?:GB|MB))", line)
            if m:
                return m.group(1).strip()
    return "—"


def pull_model(name: str) -> bool:
    print(f"  Pulling {name} ...", flush=True)
    result = run(["ollama", "pull", name])
    if result.returncode != 0:
        print(f"    ERROR: {result.stderr[:300]}")
        return False
    print(f"  ✓ {name} ready")
    return True


def run_pytest(model_name: str):
    """Run test_parse.py for the given model; return raw stdout+stderr."""
    env = {**os.environ, "OLLAMA_MODEL": model_name}
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "test_parse.py",
            "-v",
            "--tb=no",        # suppress tracebacks — we just need PASSED/FAILED
            "--durations=0",  # emit per-test wall times
            "--no-header",
        ],
        capture_output=True, text=True, env=env,
        timeout=900,
        cwd=Path(__file__).parent,
    )
    return result.stdout + result.stderr


# ── Output parser ─────────────────────────────────────────────────────────────

def parse_output(raw: str) -> dict:
    """Extract per-test status, per-test timing, and suite totals."""
    test_status: dict[str, str] = {}   # short_name -> passed|failed|skipped
    test_times:  dict[str, float] = {} # short_name -> seconds

    for line in raw.splitlines():
        # PASSED / FAILED / SKIPPED lines
        m = re.match(
            r"test_parse\.py::(\w+)::(\w+)\s+(PASSED|FAILED|SKIPPED)",
            line,
        )
        if m:
            key = f"{m.group(1)}::{m.group(2)}"
            test_status[key] = m.group(3).lower()
            continue

        # Duration lines emitted by --durations=0
        # "  2.83s call     test_parse.py::ClassName::method_name"
        m = re.match(
            r"\s*([\d.]+)s\s+call\s+test_parse\.py::(\w+)::(\w+)",
            line,
        )
        if m:
            key = f"{m.group(2)}::{m.group(3)}"
            test_times[key] = float(m.group(1))

    passed  = sum(1 for v in test_status.values() if v == "passed")
    failed  = sum(1 for v in test_status.values() if v == "failed")
    skipped = sum(1 for v in test_status.values() if v == "skipped")

    # Total wall time from summary line
    m = re.search(r"in ([\d.]+)s", raw.split("\n")[-4] if len(raw.split("\n")) > 4 else raw)
    suite_time = float(m.group(1)) if m else sum(test_times.values())

    avg_time = (sum(test_times.values()) / len(test_times)) if test_times else 0.0

    return {
        "test_status": test_status,
        "test_times":  test_times,
        "passed":      passed,
        "failed":      failed,
        "skipped":     skipped,
        "total":       passed + failed + skipped,
        "suite_time":  suite_time,
        "avg_time":    avg_time,
    }


# ── Report generator ──────────────────────────────────────────────────────────

ICON = {"passed": "✅", "failed": "❌", "skipped": "⏭"}

def _pct(n, total):
    return f"{100 * n // total}%" if total else "—"

def generate_report(model_results: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total_tests = max((r["data"]["total"] for r in model_results), default=0)

    lines = [
        "# Quick Add Parse — Model Benchmark Report",
        "",
        f"**Generated:** {now}  ",
        f"**Test suite:** `test_parse.py` — {total_tests} tests  ",
        f"**Ollama version:** {run(['ollama', '--version']).stdout.strip()}",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Model | Vendor | Params | Disk Size | Pass | Fail | Skip | Pass Rate | Avg Parse | Suite Time |",
        "|-------|--------|--------|-----------|------|------|------|-----------|-----------|------------|",
    ]

    for r in model_results:
        m = r["model"]
        d = r["data"]
        lines.append(
            f"| **{m['label']}** | {m['vendor']} | {m['params']} | {r['size']} "
            f"| {d['passed']} | {d['failed']} | {d['skipped']} "
            f"| {_pct(d['passed'], d['total'])} "
            f"| {d['avg_time']:.1f}s "
            f"| {d['suite_time']:.0f}s |"
        )

    # Per-test breakdown
    all_tests = sorted(
        set(k for r in model_results for k in r["data"]["test_status"])
    )

    lines += [
        "",
        "---",
        "",
        "## Per-Test Results",
        "",
    ]

    # Header
    header_models = " | ".join(
        f"{r['model']['label']} | {r['model']['label']} time"
        for r in model_results
    )
    sep = " | ".join("--- | ---" for _ in model_results)
    lines.append(f"| Test | {header_models} |")
    lines.append(f"| ---- | {sep} |")

    for test in all_tests:
        class_name, method = test.split("::")
        # Prettify: TestSection::test_no_date_defaults_to_later -> no_date_defaults_to_later
        label = method.replace("test_", "").replace("_", " ")
        group = class_name.replace("Test", "")

        cells = []
        for r in model_results:
            status = r["data"]["test_status"].get(test, "—")
            t      = r["data"]["test_times"].get(test)
            icon   = ICON.get(status, "—")
            time_s = f"{t:.1f}s" if t is not None else "—"
            cells.append(f"{icon} | {time_s}")

        lines.append(f"| **{group}** — {label} | {' | '.join(cells)} |")

    # Failures detail
    lines += ["", "---", "", "## Failing Tests Detail", ""]
    any_failures = False
    for r in model_results:
        failures = [t for t, s in r["data"]["test_status"].items() if s == "failed"]
        if failures:
            any_failures = True
            lines.append(f"### {r['model']['label']}")
            lines.append("")
            for t in failures:
                lines.append(f"- `{t}`")
            lines.append("")

    if not any_failures:
        lines.append("No failures across all models. 🎉")
        lines.append("")

    # Recommendation
    lines += ["---", "", "## Analysis & Recommendation", ""]

    for r in model_results:
        d = r["data"]
        m = r["model"]
        pass_rate = _pct(d["passed"], d["total"])
        lines += [
            f"### {m['label']} ({m['params']}, {r['size']})",
            f"- Pass rate: **{pass_rate}** ({d['passed']}/{d['total']})",
            f"- Average parse latency: **{d['avg_time']:.1f}s**",
            f"- Full suite: **{d['suite_time']:.0f}s**",
            "",
        ]

    lines += [
        "### Cloud deployment trade-offs",
        "",
        "| Factor | Small (3-4B) | Large (12B) |",
        "|--------|-------------|------------|",
        "| Cold-start RAM | ~4 GB | ~12 GB |",
        "| GPU VRAM needed | 4 GB (fits A10G) | 16 GB (needs A100/H100) |",
        "| Hourly cost (Modal/Replicate) | ~$0.10–0.20 | ~$0.80–1.50 |",
        "| Parse latency | 2–5s | 6–15s |",
        "| Instruction following | Adequate with examples | Strong |",
        "",
        "**Recommendation:** If accuracy of the larger model justifies the cost, use it for",
        "a hosted deployment. For a self-hosted / local scenario the best 3B model is the",
        "pragmatic choice — acceptable accuracy at a fraction of the resource requirement.",
        "",
    ]

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        default=",".join(m["name"] for m in MODELS),
        help="Comma-separated list of model names to benchmark",
    )
    parser.add_argument(
        "--skip-pull",
        action="store_true",
        help="Skip ollama pull (assume models are already downloaded)",
    )
    args = parser.parse_args()

    selected_names = {n.strip() for n in args.models.split(",")}
    selected = [m for m in MODELS if m["name"] in selected_names]

    if not selected:
        print("No matching models found.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Quick Add Benchmark — {len(selected)} model(s)")
    print(f"{'='*60}\n")

    # Pull models
    if not args.skip_pull:
        print("Pulling models (skipped if already present):")
        for m in selected:
            pull_model(m["name"])
        print()

    # Run tests
    model_results = []
    for m in selected:
        print(f"▶ {m['label']} ({m['params']})")
        size = ollama_model_size(m["name"])
        print(f"  Disk size: {size}")

        raw = run_pytest(m["name"])
        data = parse_output(raw)
        model_results.append({"model": m, "size": size, "data": data, "raw": raw})

        status = "✓" if data["failed"] == 0 else f"✗ {data['failed']} failed"
        print(
            f"  {status} — {data['passed']}/{data['total']} passed  "
            f"avg {data['avg_time']:.1f}s/call  "
            f"suite {data['suite_time']:.0f}s"
        )
        print()

    # Write report
    report = generate_report(model_results)
    out_path = Path(__file__).parent.parent / "benchmark_report.md"
    out_path.write_text(report)
    print(f"Report written to {out_path}")

    # Also print summary table to stdout
    print("\nSummary:")
    print(f"{'Model':<20} {'Pass':>5} {'Fail':>5} {'Avg':>7} {'Suite':>7} {'Size':>8}")
    print("-" * 60)
    for r in model_results:
        d, m = r["data"], r["model"]
        print(
            f"{m['label']:<20} {d['passed']:>5}/{d['total']:<3} "
            f"{d['failed']:>5} {d['avg_time']:>6.1f}s "
            f"{d['suite_time']:>6.0f}s {r['size']:>8}"
        )


if __name__ == "__main__":
    main()
