"""
Mechanical regression guard for this codebase's single most-repeated bug class:
comparing/bucketing a UTC-instant DateTime column (see models.py's docstring for the
full enumerated list -- Card.created_at/completed_at/archived_at/today_since,
Habit.created_at, HealthExperiment.created_at/dismissed_at, WithingsCredentials.
last_synced) against the client's local date/time without first converting via
deps.to_local_date(dt, utc_offset_minutes).

Found and fixed independently at least 5 times across gcal.py, correlations.py,
briefing/context.py, telegram/scheduler.py, and insights.py before this test existed --
see PRODUCT_NOTES.md's 2026-09-05 "timezone bug prevention" entry. This is a heuristic
regex scan, not a real type/dataflow checker: it flags a UTC-instant field name chained
directly into a date/time accessor (`.created_at.date()`), or appearing on the same
line as one with no nearby evidence of conversion (`timedelta`/`to_local_date`). It will
have false negatives (a conversion could happen many lines away) but is deliberately
tuned toward the exact shape every real instance of this bug has taken so far.

If this test ever fails on a legitimate, already-correct usage, add the file:line to
_ALLOWLIST below with a one-line reason -- do not loosen the regexes to work around it.
"""
import os
import re

BACKEND_ROOT = os.path.dirname(os.path.dirname(__file__))

# Directories to skip entirely: tests (this file and its siblings), the served
# qtask-bridge CLI (a separate stdlib-only script with its own conventions, already
# excluded from this exact bug class by the original audit), the venv, and migrations
# (which capture a point-in-time schema, not logic).
_SKIP_DIR_PARTS = {"tests", "venv", os.path.join("bridge", "scripts"), os.path.join("alembic", "versions")}

_UTC_INSTANT_FIELDS = (
    "created_at", "completed_at", "archived_at", "dismissed_at",
    "last_synced", "today_since",
)
_TIME_ACCESSORS = r"(date\(\)|hour\b|minute\b|weekday\(\)|strftime\()"

_DIRECT_CHAIN_RE = re.compile(
    r"\.(" + "|".join(_UTC_INSTANT_FIELDS) + r")\." + _TIME_ACCESSORS
)
_FIELD_RE = re.compile(r"\.(" + "|".join(_UTC_INSTANT_FIELDS) + r")\b")
_ACCESSOR_RE = re.compile(_TIME_ACCESSORS)
_CONVERSION_EVIDENCE_RE = re.compile(r"timedelta|to_local_date|-")

# file:line pairs (relative to backend/) that are confirmed-correct on inspection, with
# the reason inline. Keep this list short -- it should almost never need an entry.
_ALLOWLIST: set[tuple[str, int]] = {
    # EngineeringItemComment.created_at is a GitHub-API-sourced timestamp, not one of
    # models.py's UTC-instant app columns -- this regex can't tell columns apart by
    # model, only by name. Used only as a display label inside LLM prompt text, not for
    # day-bucketing/analysis. Confirmed during the original 2026-09-05 audit.
    (os.path.join("assist", "generate.py"), 244),
    (os.path.join("assist", "context.py"), 178),
}


def _iter_backend_py_files():
    for dirpath, dirnames, filenames in os.walk(BACKEND_ROOT):
        rel_dir = os.path.relpath(dirpath, BACKEND_ROOT)
        parts = set(rel_dir.split(os.sep))
        if rel_dir != "." and any(
            rel_dir == skip or rel_dir.startswith(skip + os.sep) or skip in parts
            for skip in _SKIP_DIR_PARTS
        ):
            dirnames[:] = []
            continue
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def _find_violations():
    violations = []
    for path in _iter_backend_py_files():
        rel_path = os.path.relpath(path, BACKEND_ROOT)
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if (rel_path, lineno) in _ALLOWLIST:
                    continue
                if _DIRECT_CHAIN_RE.search(line):
                    violations.append((rel_path, lineno, line.strip()))
                    continue
                if _FIELD_RE.search(line) and _ACCESSOR_RE.search(line) and not _CONVERSION_EVIDENCE_RE.search(line):
                    violations.append((rel_path, lineno, line.strip()))
    return violations


def test_no_unconverted_utc_instant_date_bucketing():
    violations = _find_violations()
    assert violations == [], (
        "Found UTC-instant column(s) chained directly into a date/time accessor with no "
        "nearby conversion evidence (timedelta/to_local_date). Convert via "
        "deps.to_local_date(dt, utc_offset_minutes) before comparing against a local date, "
        "or add an explicit, justified entry to _ALLOWLIST if this is a false positive:\n"
        + "\n".join(f"  {p}:{n}: {code}" for p, n, code in violations)
    )
