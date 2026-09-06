"""
Health × lifestyle correlation analysis + weekly experiment tracking.

GET  /api/health/correlations   – Pearson r + p-value between lifestyle factors
                                   and health outcomes (weekly-binned, 90 days)
GET  /api/health/experiment     – This week's active experiment (generates if needed)
DELETE /api/health/experiment   – Dismiss experiment, record outcome metrics
GET  /api/health/experiments    – Full history of past experiments with outcomes

Factors
───────
• avg_steps       – daily step count average (Withings)
• avg_hr          – resting heart rate average (Withings, sparse)
• avg_sleep_score – Withings sleep score (0–100)
• avg_sleep_hours – total sleep time in hours
• avg_spo2        – blood oxygen saturation %
• habit_rate      – fraction of habit-days completed (0–1)
• cards_done      – tasks marked complete in the window
"""

import json
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from scipy.stats import pearsonr, ttest_ind
from sqlalchemy.orm import Session

import models
from deps import get_db, llm_client, LLM_MODEL, local_date, reasoning_kwargs, to_local_date, utc_offset_minutes
from routers.habits import check_habit_row

router = APIRouter()

_SUMMARY_CACHE: dict[str, tuple[float, str]] = {}
_SUMMARY_TTL = 3600  # 1 hour


# ── ISO week helpers ──────────────────────────────────────────────────────────

def _isoweek(d: str) -> str:
    dt = date.fromisoformat(d)
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _week_start(wk: str) -> date:
    y, w = int(wk[:4]), int(wk[6:])
    jan4 = date(y, 1, 4)
    return jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=w - 1)


def _current_isoweek(today: date | None = None) -> str:
    """ISO week string for `today`. `today` should almost always be passed explicitly
    (the caller's client-local date, from deps.local_date(request)) -- the server runs
    UTC (Cloud Run), so the no-arg default (date.today(), server-UTC) silently computes
    the wrong week for part of the day for any non-UTC user, right at the Sunday/Monday
    boundary specifically. See check_habit_for_workout's fix for the bug this caused:
    it received a correct local `today` but ignored it for its own week lookup, so a
    workout logged in the hours where UTC has already rolled to Monday but the user's
    local day is still Sunday would silently fail to match that week's still-active
    experiment. The no-arg fallback is kept only for call sites that don't have a
    request/local date available (e.g. the legacy AppSetting migration below) and where
    day-level precision doesn't matter."""
    d = today or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def check_habit_for_workout(db: Session, workout_type: str, today: date) -> models.Habit | None:
    """If there's an active workout-routine experiment this week targeting
    workout_type, check off its linked habit for today. Matches on type only,
    not on hitting workout_target_value -- this is a lightweight "did you do
    the routine today" signal, not the statistical adherence check (that's
    computed separately from workout_experiment_avg/workout_p at outcome
    time). Returns the habit that got checked, or None if nothing matched."""
    exp = (
        db.query(models.HealthExperiment)
        .filter_by(week=_current_isoweek(today), status="active")
        .filter(models.HealthExperiment.workout_type == workout_type)
        .first()
    )
    if not exp or not exp.habit_id:
        return None
    habit = db.query(models.Habit).filter_by(id=exp.habit_id, archived=False).first()
    if not habit:
        return None
    check_habit_row(db, habit.id, today, from_workout=True)
    return habit


def check_workout_for_habit(db: Session, habit_id: int, today: date) -> models.WorkoutEntry | None:
    """The mirror of check_habit_for_workout (workout -> habit): if habit_id is the linked
    habit of an active workout-routine experiment this week, auto-log a workout entry for
    today at the experiment's target value. Assumes the target was hit exactly rather than
    asking for the real distance, so checking the habit off stays a single tap -- see the
    "row 1.2 miles" bug report this was built for. Called from check_habit_row itself
    (from_workout=False path) rather than from each manual-checkoff call site individually,
    so any future caller of check_habit_row gets this for free without remembering to wire
    it up. No-ops (returns None) if habit_id isn't linked to an active workout-routine
    experiment, or if a workout of that type is already logged today (a real logged workout
    always takes precedence over a synthetic one -- never overwrites or duplicates it)."""
    exp = (
        db.query(models.HealthExperiment)
        .filter_by(week=_current_isoweek(today), status="active", habit_id=habit_id)
        .filter(models.HealthExperiment.workout_type.isnot(None))
        .first()
    )
    if not exp:
        return None

    day_start = datetime.combine(today, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    already_logged = (
        db.query(models.WorkoutEntry)
        .filter(
            models.WorkoutEntry.type == exp.workout_type,
            models.WorkoutEntry.logged_at >= day_start,
            models.WorkoutEntry.logged_at < day_end,
        )
        .first()
    )
    if already_logged:
        return None

    entry = models.WorkoutEntry(
        type=exp.workout_type,
        value=exp.workout_target_value,
        unit=exp.workout_unit,
        logged_at=day_start.replace(hour=12),
        raw_input=f"Auto-logged from completing \"{exp.action or exp.text}\"",
    )
    db.add(entry)
    db.flush()
    return entry


# ── Shared data loader ────────────────────────────────────────────────────────

def _load_weekly_obs(
    db: Session, today: date, days: int = 90, tz_offset_minutes: int = 0,
) -> tuple[list[dict], list[dict]]:
    """Return (weight_obs, fat_obs) — weekly-binned observations."""
    start_str = (today - timedelta(days=days)).isoformat()

    measurements = (
        db.query(models.WithingsMeasurement)
        .filter(models.WithingsMeasurement.date >= start_str)
        .order_by(models.WithingsMeasurement.date)
        .all()
    )
    by_date: dict[str, dict[str, float]] = {}
    for m in measurements:
        by_date.setdefault(m.date, {})[m.metric] = m.value

    active_habit_count = (
        db.query(models.Habit)
        .filter(
            models.Habit.archived == False,        # noqa: E712
            models.Habit.health_metric == None,  # noqa: E711
        )
        .count()
    )
    completions_by_date: dict[str, int] = {}
    for hc in db.query(models.HabitCompletion).filter(
        models.HabitCompletion.date >= start_str
    ).all():
        completions_by_date[hc.date] = completions_by_date.get(hc.date, 0) + 1

    cards_done_by_date: dict[str, int] = {}
    for card in db.query(models.Card).filter(
        models.Card.completed == True,           # noqa: E712
        models.Card.completed_at.isnot(None),
        models.Card.completed_at >= start_str,
    ).all():
        # completed_at is a UTC instant -- must convert to the client's local date
        # before bucketing, or completions near local midnight land on the wrong day.
        d = to_local_date(card.completed_at, tz_offset_minutes).isoformat()
        cards_done_by_date[d] = cards_done_by_date.get(d, 0) + 1

    # Food quality + calories: collect per-day scores to weekly-bin later
    food_quality_by_date: dict[str, list[float]] = {}
    food_calories_by_date: dict[str, list[float]] = {}
    for entry in db.query(models.FoodEntry).filter(
        models.FoodEntry.consumed_at >= start_str
    ).all():
        d_str = str(entry.consumed_at)[:10]
        if entry.quality is not None:
            food_quality_by_date.setdefault(d_str, []).append(float(entry.quality))
        if entry.calories is not None:
            food_calories_by_date.setdefault(d_str, []).append(float(entry.calories))

    # Weekly binning
    week_vals: dict[str, dict[str, list[float]]] = {}
    for d, metrics in by_date.items():
        wk = _isoweek(d)
        for metric, val in metrics.items():
            week_vals.setdefault(wk, {}).setdefault(metric, []).append(val)

    week_avgs: dict[str, dict[str, float]] = {
        wk: {m: sum(vs) / len(vs) for m, vs in mdict.items()}
        for wk, mdict in week_vals.items()
    }

    week_completions: dict[str, int] = {}
    for d, cnt in completions_by_date.items():
        wk = _isoweek(d)
        week_completions[wk] = week_completions.get(wk, 0) + cnt

    week_cards: dict[str, int] = {}
    for d, cnt in cards_done_by_date.items():
        wk = _isoweek(d)
        week_cards[wk] = week_cards.get(wk, 0) + cnt

    week_food_quality: dict[str, list[float]] = {}
    for d, scores in food_quality_by_date.items():
        wk = _isoweek(d)
        week_food_quality.setdefault(wk, []).extend(scores)

    week_food_calories: dict[str, list[float]] = {}
    for d, cals in food_calories_by_date.items():
        wk = _isoweek(d)
        week_food_calories.setdefault(wk, []).extend(cals)

    # Workout days: keyed by date, value is set of workout types logged that day
    workout_days_by_date: dict[str, set[str]] = {}
    for entry in db.query(models.WorkoutEntry).filter(
        models.WorkoutEntry.logged_at >= start_str
    ).all():
        d_str = str(entry.logged_at)[:10]
        workout_days_by_date.setdefault(d_str, set()).add(entry.type)

    CARDIO_TYPES = {"run", "cycle", "row", "swim", "sport"}

    week_workout_days: dict[str, int] = {}
    week_cardio_days:  dict[str, int] = {}
    week_strength_days: dict[str, int] = {}
    for d, types in workout_days_by_date.items():
        wk = _isoweek(d)
        week_workout_days[wk]  = week_workout_days.get(wk, 0) + 1
        if types & CARDIO_TYPES:
            week_cardio_days[wk]   = week_cardio_days.get(wk, 0) + 1
        if "strength" in types:
            week_strength_days[wk] = week_strength_days.get(wk, 0) + 1

    def build_obs(outcome_metric: str) -> list[dict]:
        weeks = sorted(wk for wk, avgs in week_avgs.items() if outcome_metric in avgs)
        rows = []
        for i in range(1, len(weeks)):
            curr_wk, prev_wk = weeks[i], weeks[i - 1]
            gap_days = (_week_start(curr_wk) - _week_start(prev_wk)).days
            if gap_days > 21:
                continue
            delta_per_day = (
                week_avgs[curr_wk][outcome_metric] - week_avgs[prev_wk][outcome_metric]
            ) / gap_days
            habit_rate = (
                week_completions.get(curr_wk, 0) / (7 * active_habit_count)
                if active_habit_count > 0 else None
            )
            fq = week_food_quality.get(curr_wk)
            rows.append({
                "date":             curr_wk,
                "delta_per_day":    delta_per_day,
                "avg_steps":        week_avgs[curr_wk].get("steps"),
                "avg_hr":           week_avgs[curr_wk].get("heart_rate"),
                "habit_rate":       habit_rate,
                "cards_done":       week_cards.get(curr_wk) or None,
                "avg_sleep_score":  week_avgs[curr_wk].get("sleep_score"),
                "avg_sleep_hours":  (
                    week_avgs[curr_wk]["sleep_minutes"] / 60
                    if week_avgs[curr_wk].get("sleep_minutes") is not None else None
                ),
                "avg_spo2":         week_avgs[curr_wk].get("spo2"),
                "avg_food_quality":  sum(fq) / len(fq) if fq else None,
                "avg_calories":      sum(fc) / len(fc) if (fc := week_food_calories.get(curr_wk)) else None,
                "workout_days":      week_workout_days.get(curr_wk) or None,
                "cardio_days":       week_cardio_days.get(curr_wk)  or None,
                "strength_days":     week_strength_days.get(curr_wk) or None,
            })
        return rows

    return build_obs("weight"), build_obs("fat_ratio")


# ── Existing-routine detection (for incremental experiment proposals) ─────────

def _established_habits(
    db: Session, today: date,
    min_age_days: int = 14, window_days: int = 21, min_rate: float = 0.6,
    tz_offset_minutes: int = 0,
) -> list[dict]:
    """Non-archived, non-health_metric-linked habits old enough and being
    kept up consistently enough to be candidates for an incremental routine
    experiment ("meditate 15 min instead of 10"). Names are free text, so the
    LLM infers the current numeric target from the name itself (e.g.
    "Meditate 10 min") the same way this file already infers step goals from
    prose elsewhere in _generate_experiment."""
    window_start = (today - timedelta(days=window_days)).isoformat()
    habits = (
        db.query(models.Habit)
        .filter(
            models.Habit.archived == False,    # noqa: E712
            models.Habit.health_metric == None,  # noqa: E711
        )
        .all()
    )
    results = []
    for h in habits:
        created = to_local_date(h.created_at, tz_offset_minutes) if h.created_at else today
        if (today - created).days < min_age_days:
            continue
        completed = (
            db.query(models.HabitCompletion)
            .filter(
                models.HabitCompletion.habit_id == h.id,
                models.HabitCompletion.date >= window_start,
            )
            .count()
        )
        rate = completed / window_days
        if rate >= min_rate:
            results.append({"name": h.name, "completion_rate": round(rate, 2)})
    return results


def _established_workouts(
    db: Session, today: date,
    window_days: int = 42, min_sessions: int = 3,
) -> list[dict]:
    """WorkoutEntry types logged consistently enough to be candidates for an
    incremental routine experiment ("row 2mi/day instead of 1mi"). `unit` is
    the most common one logged for that type -- WorkoutEntry.unit is free
    text/uninterpreted (per its own model comment), so this is best-effort,
    not a normalized unit system."""
    window_start = (today - timedelta(days=window_days)).isoformat()
    entries = (
        db.query(models.WorkoutEntry)
        .filter(
            models.WorkoutEntry.logged_at >= window_start,
            models.WorkoutEntry.value.isnot(None),
        )
        .all()
    )
    by_type: dict[str, list] = {}
    for e in entries:
        by_type.setdefault(e.type, []).append(e)

    results = []
    for wtype, rows in by_type.items():
        if len(rows) < min_sessions:
            continue
        values = [r.value for r in rows]
        units = [r.unit for r in rows if r.unit]
        unit = Counter(units).most_common(1)[0][0] if units else None
        results.append({
            "type": wtype,
            "sessions_per_week": round(len(rows) / (window_days / 7), 1),
            "avg_value": round(sum(values) / len(values), 2),
            "unit": unit,
        })
    return results


def _established_foods(
    db: Session, today: date,
    window_days: int = 21, min_days: int = 4,
) -> list[dict]:
    """Food names logged on enough distinct days recently to be candidates for
    an elimination/reduction experiment ("cut out coffee this week"). Matched
    by exact name (case-insensitive) -- FoodEntry.name is free text with no
    canonical grouping, so near-duplicate phrasings of the same food (e.g.
    "coffee" vs "black coffee") won't be merged. Counts distinct DAYS rather
    than raw entry count so multiple same-day mentions don't inflate the
    frequency. Capped to the 8 most frequent, mirroring the correlations
    list's own [:6] capping elsewhere in this file."""
    window_start = (today - timedelta(days=window_days)).isoformat()
    entries = (
        db.query(models.FoodEntry)
        .filter(models.FoodEntry.consumed_at >= window_start)
        .all()
    )
    days_by_name: dict[str, set[str]] = {}
    for e in entries:
        key = e.name.strip().lower()
        days_by_name.setdefault(key, set()).add(str(e.consumed_at)[:10])

    results = [
        {"name": name, "days_per_week": round(len(days) / (window_days / 7), 1)}
        for name, days in days_by_name.items()
        if len(days) >= min_days
    ]
    results.sort(key=lambda r: -r["days_per_week"])
    return results[:8]


def _recent_avg_steps(db: Session, today: date, days: int = 28) -> float | None:
    """Average daily step count over the trailing `days` window, queried straight from
    WithingsMeasurement -- independent of _load_weekly_obs's weekly weight/fat-metric
    binning, which only keeps a week's steps data if that week also had a weight (or fat)
    measurement. _generate_experiment's step-goal fallback below needs the user's real
    recent average regardless of whether weight was also logged, e.g. to compute what "10%
    more than your average" actually means in steps."""
    start = (today - timedelta(days=days)).isoformat()
    values = [
        m.value for m in db.query(models.WithingsMeasurement).filter(
            models.WithingsMeasurement.metric == "steps",
            models.WithingsMeasurement.date >= start,
        ).all()
    ]
    return round(sum(values) / len(values), 1) if values else None


def _recent_experiments(db: Session, limit: int = 4) -> list[models.HealthExperiment]:
    """Most recent experiments (any status), for duplicate-avoidance context."""
    return (
        db.query(models.HealthExperiment)
        .order_by(models.HealthExperiment.created_at.desc())
        .limit(limit)
        .all()
    )


def _format_recent_experiment(exp: models.HealthExperiment) -> str:
    if exp.workout_type and exp.workout_target_value is not None:
        unit = f" {exp.workout_unit}" if exp.workout_unit else ""
        return f"{exp.workout_type}: {exp.workout_target_value}{unit}"
    if exp.food_name and exp.food_target_frequency is not None:
        return f"food: {exp.food_name} → {exp.food_target_frequency}x/week"
    if exp.health_metric and exp.health_goal is not None:
        return f"{exp.health_metric}: {exp.health_goal}"
    return exp.action or exp.text or "(no specific target)"


def _nudge_if_near_duplicate(
    health_metric: str | None, health_goal: float | None,
    workout_type: str | None, workout_target_value: float | None,
    prev: models.HealthExperiment | None,
) -> tuple[float | None, float | None]:
    """Deterministic backstop: if the newly generated experiment matches the
    single most recent one on metric/routine with a near-identical target
    (within 5%), bump the target rather than silently repeating it. This is
    insurance regardless of whether the LLM actually followed the "avoid
    repeating" prompt instruction -- the exact bug this feature is fixing."""
    if prev is None:
        return health_goal, workout_target_value
    if (health_metric and prev.health_metric == health_metric
            and health_goal is not None and prev.health_goal is not None):
        if abs(health_goal - prev.health_goal) / max(abs(prev.health_goal), 1e-9) < 0.05:
            health_goal = round(prev.health_goal * 1.2)
    if (workout_type and prev.workout_type == workout_type
            and workout_target_value is not None and prev.workout_target_value is not None):
        if abs(workout_target_value - prev.workout_target_value) / max(abs(prev.workout_target_value), 1e-9) < 0.05:
            workout_target_value = round(prev.workout_target_value * 1.2, 2)
    return health_goal, workout_target_value


# ── Correlation + segment computation ────────────────────────────────────────

FACTORS = [
    ("avg_steps",        "Daily steps"),
    ("avg_hr",           "Resting heart rate"),
    ("avg_sleep_score",  "Sleep score"),
    ("avg_sleep_hours",  "Sleep duration"),
    ("avg_spo2",         "Blood oxygen (SpO2)"),
    ("avg_food_quality", "Diet quality"),
    ("avg_calories",     "Daily calories"),
    ("habit_rate",       "Habit completion rate"),
    ("cards_done",       "Tasks completed"),
    ("workout_days",     "Workout days"),
    ("cardio_days",      "Cardio days"),
    ("strength_days",    "Strength days"),
]


def _compute_correlations(weight_obs: list[dict], fat_obs: list[dict]) -> list[dict]:
    correlations = []
    for obs, outcome_label, outcome_unit in [
        (weight_obs, "Weight change",   "kg/day"),
        (fat_obs,    "Body fat change", "%/day"),
    ]:
        for fkey, flabel in FACTORS:
            pairs = [
                (row[fkey], row["delta_per_day"])
                for row in obs
                if row.get(fkey) is not None
            ]
            if len(pairs) < 3:
                continue
            xs, ys = zip(*pairs)
            try:
                result = pearsonr(list(xs), list(ys))
                r = round(float(result.statistic), 3)
                p = round(float(result.pvalue), 4)
            except Exception:
                continue
            scatter = [
                {
                    "date": row["date"],
                    "x": round(row[fkey], 3),
                    "y": round(row["delta_per_day"], 5),
                }
                for row in obs
                if row.get(fkey) is not None
            ]
            correlations.append({
                "factor":       flabel,
                "factor_key":   fkey,
                "outcome":      outcome_label,
                "outcome_unit": outcome_unit,
                "r":            r,
                "p":            p,
                "n":            len(pairs),
                "scatter":      scatter,
            })
    correlations.sort(key=lambda c: abs(c["r"]), reverse=True)
    return correlations


def _compute_segments(weight_obs: list[dict], fat_obs: list[dict]) -> list[dict]:
    def _segment(obs, fkey, flabel, outcome_label, outcome_unit):
        pairs = sorted(
            [(row[fkey], row["delta_per_day"], row["date"]) for row in obs if row.get(fkey) is not None],
            key=lambda p: p[0],
        )
        if len(pairs) < 4:
            return None
        mid = len(pairs) // 2
        lo, hi = pairs[:mid], pairs[mid:]
        threshold = (pairs[mid - 1][0] + pairs[mid][0]) / 2
        hi_deltas = [y for _, y, _ in hi]
        lo_deltas = [y for _, y, _ in lo]
        p_value = None
        try:
            if len(hi_deltas) >= 2 and len(lo_deltas) >= 2:
                result = ttest_ind(hi_deltas, lo_deltas, equal_var=False)
                p_value = round(float(result.pvalue), 4)
        except Exception:
            pass
        return {
            "factor":       flabel,
            "factor_key":   fkey,
            "outcome":      outcome_label,
            "outcome_unit": outcome_unit,
            "threshold":    round(threshold, 2),
            "p":            p_value,
            "high": {
                "n":           len(hi),
                "mean_factor": round(sum(x for x, _, _ in hi) / len(hi), 1),
                "mean_delta":  round(sum(y for _, y, _ in hi) / len(hi), 5),
            },
            "low": {
                "n":           len(lo),
                "mean_factor": round(sum(x for x, _, _ in lo) / len(lo), 1),
                "mean_delta":  round(sum(y for _, y, _ in lo) / len(lo), 5),
            },
        }

    segments = []
    for obs, outcome_label, outcome_unit in [
        (weight_obs, "Weight change",   "kg/day"),
        (fat_obs,    "Body fat change", "%/day"),
    ]:
        for fkey, flabel in FACTORS:
            s = _segment(obs, fkey, flabel, outcome_label, outcome_unit)
            if s:
                s["_abs_diff"] = abs(s["high"]["mean_delta"] - s["low"]["mean_delta"])
                segments.append(s)

    segments.sort(key=lambda s: s["_abs_diff"], reverse=True)
    for s in segments:
        del s["_abs_diff"]
    return segments


# ── LLM summary ───────────────────────────────────────────────────────────────

_SUMMARY_SYSTEM = (
    "You are interpreting personal health correlation data. "
    "Write 2-3 concise sentences summarising the strongest patterns. "
    "Be specific about direction and magnitude. "
    "Note where p-values suggest statistical significance (p < 0.05). "
    "Remind the user that correlation does not imply causation and that "
    "sample sizes are small. Be direct — avoid filler phrases."
)


def _llm_summary(correlations: list[dict]) -> str:
    if not correlations:
        return ""
    key = json.dumps([(c["r"], c["p"], c["factor"], c["outcome"]) for c in correlations[:6]])
    cached = _SUMMARY_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _SUMMARY_TTL:
        return cached[1]
    lines = [
        f"{c['factor']} vs {c['outcome']}: r={c['r']:+.2f}, p={c['p']:.3f} (n={c['n']})"
        for c in correlations[:6]
    ]
    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user",   "content": "\n".join(lines)},
            ],
            max_tokens=300,
            **reasoning_kwargs(),
        )
        text = resp.choices[0].message.content.strip()
    except Exception as e:
        # Falls back to "" (an honestly empty summary, not fabricated text) -- the frontend
        # already only renders this section when non-empty. Logged so a real, recurring
        # failure is visible instead of just invisibly losing the summary forever.
        print(f"[correlations] summary generation error: {e}")
        text = ""
    _SUMMARY_CACHE[key] = (time.monotonic(), text)
    return text


# ── LLM experiment generator ──────────────────────────────────────────────────

_EXPERIMENT_SYSTEM = """\
You are a personal health coach. Based on correlation data between lifestyle \
factors and health outcomes (including p-values), suggest one specific \
experiment to try for the next 7 days. Prioritise factors with lower p-values \
(stronger evidence). The experiment should be actionable, measurable, and \
directly connected to the data.

The user message may also include a "Recently tried experiments" section and an \
"Established routines" section — see the rules below for how to use each.

Respond with ONLY valid JSON (no markdown, no explanation):
{
  "text": "2-3 sentence description of the experiment and why it's worth trying",
  "hypothesis": "If I [specific action with concrete number], I expect to see Y by end of week",
  "action": "The specific daily action with a CONCRETE NUMBER, e.g. '8,000 steps every day'",
  "needs_habit": true or false,
  "health_metric": "steps" | "fat_ratio" | "weight" | null,
  "health_goal": numeric goal value or null,
  "routine_type": "workout" | "habit" | "food" | null,
  "workout_type": one of the established workout types given, or null,
  "workout_target_value": numeric target or null,
  "workout_unit": the established unit for that workout type, or null,
  "food_name": one of the frequently eaten foods given, or null,
  "food_target_frequency": numeric target occurrences/week, 0 = eliminate entirely, or null
}

CRITICAL: "action" must ALWAYS contain a specific measurable target — never vague \
phrases like "increase my steps" or "walk more". Always include a concrete number: \
"8,000 steps every day", "45 minutes of walking daily", "7 hours of sleep". \
If the experiment is passive observation with no daily action, set action to null.

needs_habit should be true only when the experiment requires a specific daily \
effort to track (e.g. hitting a step target). Set false for passive observation.

health_metric/health_goal: ONLY set these when the experiment's primary \
measurable outcome is literally one of the three Withings-tracked metrics: step \
count, body fat percentage, or body weight. The health_goal MUST match the \
number in the action field exactly. Examples: \
"Walk 8,000 steps every day" → health_metric="steps", health_goal=8000. \
"Reduce body fat to 18%" → health_metric="fat_ratio", health_goal=18. \
Examples where you must leave it null: "1 hour of screen-free time", "read \
before bed", "meditate 10 minutes", "no alcohol", "sleep by 10pm" — these are \
behavioral habits that cannot be verified by a Withings device, so \
health_metric MUST be null. When in doubt, set null.

routine_type/workout_*/food_*: an ALTERNATIVE to health_metric/health_goal, never \
more than one of the three groups at once. If "Established routines" lists a \
workout type the user already does regularly (e.g. "row: 3x/week, avg 1.6 mi"), \
you may propose a specific incremental increase to it instead of a fresh \
Withings-metric goal — this is usually MORE interesting than a generic goal \
when a real established routine is available. Set routine_type="workout", \
workout_type to the EXACT type string given, workout_target_value to a \
meaningfully higher number than the established avg (not a trivial +1%), and \
workout_unit to the unit given for that type. The action field must still state \
the concrete target using the SAME numbers you just set, e.g. for \
workout_type="row"/workout_target_value=2/workout_unit="mi" against an \
established avg of 1.6: "Row 2 mi every day instead of your usual ~1.6" — the \
pattern is "<Activity> <workout_target_value> <workout_unit> every day instead \
of your usual ~<established avg>", not a fixed phrase to copy verbatim. If \
instead you want to propose increasing an established HABIT's target (e.g. \
"meditate 15 min instead of 10"), set routine_type="habit" and leave the \
workout_*/food_* fields null — habits are tracked by completion only, so just \
state the new target clearly in the action field; there's no structured field \
for it. If "Frequently eaten foods" lists something the user eats regularly \
(e.g. "coffee — ~4.5x/week"), you may instead propose reducing or eliminating \
it for the week to see whether it's linked to the outcome — this is especially \
interesting when diet quality or calories showed a notable correlation. Set \
routine_type="food", food_name to the EXACT food name given, and \
food_target_frequency to a number LOWER than its current frequency (0 for full \
elimination, or a reduced count for a cutback). The action field must still \
state the concrete target using the SAME food name and number, following the \
pattern "Cut out <food_name> entirely this week" (food_target_frequency=0) or \
"Limit <food_name> to <food_target_frequency>x this week instead of your usual \
~<current frequency>".

CONSISTENCY (important): whichever single group you populate above -- \
health_metric/health_goal, OR routine_type="workout" with workout_*, OR \
routine_type="food" with food_* -- the other two groups' fields must be left \
null, AND "action"/"text"/"hypothesis" must describe that exact same thing. \
Never write an action about a workout routine while health_metric is set to \
something else (or vice versa) -- that combination is invalid even if each \
field individually looks plausible.

Recently tried experiments: avoid proposing the same metric/routine/food with \
the same or a near-identical target as one just tried — either pick a different \
factor/routine/food, or a meaningfully different target (a genuinely bigger \
step, not a token +1%). Repeating the exact same experiment back-to-back \
provides no new information.\
"""


def _generate_experiment(
    correlations: list[dict], db: Session, today: date | None = None,
    tz_offset_minutes: int = 0,
) -> models.HealthExperiment:
    """Generate a new experiment via LLM, persist to health_experiments table.

    `today` should be the caller's client-local date (deps.local_date(request)) so the
    week this experiment is stamped with agrees with check_habit_for_workout's own
    week lookup -- see _current_isoweek's docstring. Optional/defaults to server-UTC
    date.today() only so existing callers/tests that don't care about day-level
    precision don't all need updating. `tz_offset_minutes` is likewise the caller's
    deps.utc_offset_minutes(request), threaded through to _established_habits so its
    Habit.created_at (a UTC instant) comparison against `today` (local) is correct."""
    today = today or date.today()
    week = _current_isoweek(today)

    # Guard against concurrent generation: re-check inside the session before inserting.
    existing = db.query(models.HealthExperiment).filter_by(week=week, status="active").first()
    if existing:
        return existing

    if not correlations:
        exp = models.HealthExperiment(
            week=week,
            text="Keep logging your health data to unlock personalised experiments. You need at least 3 weeks of data.",
            hypothesis=None,
            action=None,
            needs_habit=False,
            habit_id=None,
        )
        db.add(exp)
        db.flush()
        db.commit()
        return exp

    lines = [
        f"{c['factor']} vs {c['outcome']}: r={c['r']:+.2f}, p={c['p']:.3f} (n={c['n']})"
        for c in correlations[:6]
    ]

    recent = _recent_experiments(db)
    established_habits = _established_habits(db, today, tz_offset_minutes=tz_offset_minutes)
    established_workouts = _established_workouts(db, today)
    established_workouts_by_type = {w["type"]: w for w in established_workouts}
    established_foods = _established_foods(db, today)
    established_foods_by_name = {f["name"]: f for f in established_foods}

    user_parts = ["Correlation data:"] + lines
    if recent:
        user_parts.append(
            "\nRecently tried experiments (avoid repeating; propose something meaningfully different):"
        )
        user_parts.extend(f"- {_format_recent_experiment(r)}" for r in recent)
    if established_habits or established_workouts:
        user_parts.append(
            "\nEstablished routines you could propose an incremental change to instead of a fresh goal:"
        )
        for h in established_habits:
            user_parts.append(f'- Habit "{h["name"]}" — done {round(h["completion_rate"] * 100)}% of days recently')
        for w in established_workouts:
            unit_part = f" {w['unit']}" if w["unit"] else ""
            user_parts.append(
                f'- Workout type "{w["type"]}" — {w["sessions_per_week"]}x/week, avg {w["avg_value"]}{unit_part}'
            )
    if established_foods:
        user_parts.append(
            "\nFrequently eaten foods you could propose reducing or eliminating instead of a fresh goal:"
        )
        for f in established_foods:
            user_parts.append(f'- Food "{f["name"]}" — eaten ~{f["days_per_week"]}x/week')
    user_content = "\n".join(user_parts)

    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _EXPERIMENT_SYSTEM},
                {"role": "user",   "content": user_content},
            ],
            max_tokens=600,
            **reasoning_kwargs(),
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        llm_data = json.loads(raw)
        text           = llm_data.get("text", "")
        hypothesis     = llm_data.get("hypothesis")
        action         = llm_data.get("action")
        needs_habit    = bool(llm_data.get("needs_habit", False))
        health_metric = llm_data.get("health_metric") or None
        health_goal   = llm_data.get("health_goal")
        if health_goal is not None:
            try:
                health_goal = float(health_goal)
            except (TypeError, ValueError):
                health_goal = None
        if health_metric not in ("steps", "fat_ratio", "weight", None):
            health_metric = None
            health_goal = None

        # Routine-based experiment fields -- an alternative to health_metric/
        # health_goal, never both at once. routine_type explicitly signals
        # intent, so it wins the tie-break if the LLM (against instructions)
        # set both. workout_type/food_name must exactly match an established
        # routine/food -- anything else is discarded rather than trusted, same
        # defensive validation style already used for health_metric above.
        routine_type = llm_data.get("routine_type")
        workout_type = llm_data.get("workout_type") or None
        workout_target_value = llm_data.get("workout_target_value")
        if workout_target_value is not None:
            try:
                workout_target_value = float(workout_target_value)
            except (TypeError, ValueError):
                workout_target_value = None

        food_name = llm_data.get("food_name") or None
        if food_name:
            food_name = food_name.strip().lower()
        food_target_frequency = llm_data.get("food_target_frequency")
        if food_target_frequency is not None:
            try:
                food_target_frequency = float(food_target_frequency)
            except (TypeError, ValueError):
                food_target_frequency = None
        food_baseline_frequency = None

        if (routine_type == "workout" and workout_type in established_workouts_by_type
                and workout_target_value is not None):
            health_metric = None
            health_goal = None
            workout_unit = established_workouts_by_type[workout_type]["unit"]
            food_name = None
            food_target_frequency = None
        elif (routine_type == "food" and food_name in established_foods_by_name
                and food_target_frequency is not None):
            health_metric = None
            health_goal = None
            workout_type = None
            workout_target_value = None
            workout_unit = None
            food_baseline_frequency = established_foods_by_name[food_name]["days_per_week"]
        else:
            workout_type = None
            workout_target_value = None
            workout_unit = None
            food_name = None
            food_target_frequency = None
            if routine_type == "habit":
                # No structured field for habit-routine experiments -- the
                # established habit was just prompt context, not a hard link.
                health_metric = None
                health_goal = None

        # Don't repeat the same food target back-to-back. Unlike workout/
        # health-metric targets (nudged upward via _nudge_if_near_duplicate
        # below), there's no natural "nudge" direction for a reduction target
        # that's often already 0 -- so just drop it rather than silently
        # repeat, deterministically, regardless of whether the LLM actually
        # followed the "avoid repeating" prompt instruction.
        if food_name and recent and recent[0].food_name == food_name:
            food_name = None
            food_target_frequency = None
            food_baseline_frequency = None

        # Use prose text as authoritative source for step goals — the LLM's JSON
        # numbers are less reliable than the numbers it writes in action/hypothesis.
        # Skipped entirely for routine-based experiments: this fallback exists
        # only to recover a Withings step goal, and firing it here risks
        # misreading an unrelated number in a workout/habit/food action string.
        if routine_type not in ("workout", "habit", "food"):
            import re as _re

            def _extract_steps(text: str | None) -> float | None:
                """Extract a plausible daily step count from text (100–50,000). Accepts
                singular "step" too (e.g. "9,000 step target"), not just "steps" -- still
                requires the number immediately before it, same tight adjacency as before,
                just not hardcoded to the plural."""
                if not text:
                    return None
                m = _re.search(r'([\d,]+)\+?\s*(?:daily\s+)?steps?\b', text, _re.I)
                if m:
                    val = float(m.group(1).replace(",", ""))
                    return val if 100 <= val <= 50_000 else None
                return None

            def _extract_relative_step_increase(text: str | None) -> float | None:
                """Extract a percentage-based relative step target, e.g. "increase your
                daily step count by 10%" or "10% more steps than your recent average" --
                returns the percentage as a fraction (10% -> 0.10). Despite the system
                prompt's explicit "CRITICAL: always include a concrete number" instruction,
                the LLM sometimes proposes a step goal this way instead -- rather than
                discard the whole experiment, _resolve_step_goal below computes the actual
                number from the user's own recent average, same as it should have."""
                if not text:
                    return None
                m = _re.search(r'(\d+(?:\.\d+)?)\s*%', text)
                if not m:
                    return None
                pct = float(m.group(1))
                return pct / 100 if 0 < pct <= 100 else None

            def _resolve_step_goal(action_text: str | None, hypothesis_text: str | None) -> float | None:
                goal = _extract_steps(action_text) or _extract_steps(hypothesis_text)
                if goal is not None:
                    return goal
                pct = (_extract_relative_step_increase(action_text)
                       or _extract_relative_step_increase(hypothesis_text))
                if not pct:
                    return None
                recent_avg = _recent_avg_steps(db, today)
                return round(recent_avg * (1 + pct)) if recent_avg else None

            def _mentions_steps(text: str | None) -> bool:
                # Word-boundary match on "step"/"steps" -- a plain "steps" substring check
                # missed phrasing like "increase your daily step count", which uses the
                # singular.
                return bool(text) and bool(_re.search(r'\bsteps?\b', text, _re.I))

            if action and _mentions_steps(action):
                # Action is explicitly about steps — metric must be "steps" regardless of
                # what the LLM put in the JSON (catches fat_ratio/weight confusion).
                health_metric = "steps"
                health_goal = _resolve_step_goal(action, hypothesis)
            elif health_metric == "steps":
                # LLM said steps but no "steps" in action — correct goal from hypothesis
                if health_goal is None or health_goal > 50_000:
                    health_goal = _resolve_step_goal(None, hypothesis)
            elif health_metric is None and action:
                # Fallback: action didn't mention "steps" explicitly but hypothesis might
                _hypo_steps = _resolve_step_goal(None, hypothesis)
                if _hypo_steps:
                    health_metric = "steps"
                    health_goal = _hypo_steps

            # Final guard: clear any remaining implausible step goal
            if health_metric == "steps" and not health_goal:
                health_metric = None
                health_goal = None

        # If needs_habit is set but no concrete action was produced, the LLM was vague.
        # Manufacture a sensible default so the experiment still gets a tracking habit.
        if needs_habit and not action:
            if health_metric == "steps":
                action = "8,000 steps every day"
                health_goal = health_goal or 8000.0
            else:
                # Can't auto-infer a goal for other metrics/routines — leave needs_habit False
                needs_habit = False

        # Deterministic duplicate-avoidance backstop, regardless of whether the
        # LLM actually followed the "avoid repeating" prompt instruction above.
        health_goal, workout_target_value = _nudge_if_near_duplicate(
            health_metric, health_goal, workout_type, workout_target_value,
            recent[0] if recent else None,
        )
    except Exception as e:
        # Was a silent `except Exception: <canned fallback>` with no logging at all -- meant
        # a real, deterministically-repeating failure (see the reasoning_effort/max_tokens
        # comment above) produced this exact canned text on every single regeneration with
        # zero trace in the logs to explain why. Print, don't raise: a broken experiment
        # generation shouldn't break the page.
        print(f"[correlations] experiment generation error: {e}")
        text = "Try increasing your daily step count by 10% compared to your recent average."
        hypothesis = "More consistent movement should correlate with better weight outcomes."
        action = None
        needs_habit = False
        health_metric = None
        health_goal = None
        routine_type = None
        workout_type = None
        workout_target_value = None
        workout_unit = None
        food_name = None
        food_target_frequency = None
        food_baseline_frequency = None

    habit_id = None
    if action:
        # Always create a tracking habit — even for non-Withings experiments it
        # serves as a daily reminder and shows up in the Health & Habits section.
        # Workout-routine experiments get one too -- logging a matching WorkoutEntry
        # auto-checks it via check_habit_for_workout (matches on workout_type against
        # this experiment's row, not on hitting workout_target_value), in addition to
        # checking it off manually like any other habit.
        habit_name = f"🧪 {action[:60]}"
        habit = models.Habit(
            name=habit_name,
            health_metric=health_metric,
            health_goal=health_goal,
        )
        db.add(habit)
        db.flush()
        habit_id = habit.id

    exp = models.HealthExperiment(
        week=week,
        text=text,
        hypothesis=hypothesis,
        action=action,
        needs_habit=needs_habit,
        habit_id=habit_id,
        health_metric=health_metric,
        health_goal=health_goal,
        routine_type=routine_type,
        workout_type=workout_type,
        workout_target_value=workout_target_value,
        workout_unit=workout_unit,
        food_name=food_name,
        food_target_frequency=food_target_frequency,
        food_baseline_frequency=food_baseline_frequency,
    )
    db.add(exp)
    db.flush()
    db.commit()
    return exp


def _exp_to_dict(exp: models.HealthExperiment) -> dict:
    return {
        "id":                   exp.id,
        "week":                 exp.week,
        "text":                 exp.text,
        "hypothesis":           exp.hypothesis,
        "action":               exp.action,
        "needs_habit":          exp.needs_habit,
        "habit_id":             exp.habit_id,
        "health_metric":      exp.health_metric,
        "health_goal":        exp.health_goal,
        "status":               exp.status,
        "created_at":           exp.created_at.isoformat() if exp.created_at else None,
        "dismissed_at":         exp.dismissed_at.isoformat() if exp.dismissed_at else None,
        "habit_completion_rate": exp.habit_completion_rate,
        "weight_delta":         exp.weight_delta,
        "fat_delta":            exp.fat_delta,
        "weight_baseline":      exp.weight_baseline,
        "fat_baseline":         exp.fat_baseline,
        "workout_type":            exp.workout_type,
        "workout_target_value":    exp.workout_target_value,
        "workout_unit":            exp.workout_unit,
        "workout_baseline_avg":    exp.workout_baseline_avg,
        "workout_experiment_avg":  exp.workout_experiment_avg,
        "workout_baseline_n":      exp.workout_baseline_n,
        "workout_experiment_n":    exp.workout_experiment_n,
        "workout_p":               exp.workout_p,
        "workout_baseline_weeks_n":       exp.workout_baseline_weeks_n,
        "food_name":               exp.food_name,
        "food_baseline_frequency": exp.food_baseline_frequency,
        "food_target_frequency":   exp.food_target_frequency,
        "food_experiment_count":   exp.food_experiment_count,
        "food_baseline_weeks_n":   exp.food_baseline_weeks_n,
        "confounds":               json.loads(exp.confounds) if exp.confounds else None,
    }


def _food_present_weeks(db: Session, food_name: str, candidate_weeks: set[str], min_days: int = 2) -> set[str]:
    """Of the given ISO weeks, which ones had `food_name` (exact,
    case-insensitive) logged on at least `min_days` distinct days -- i.e.
    weeks the food was actually part of the regular pattern, not just any
    week in the analysis window regardless of relevance. Used to build a
    food-elimination experiment's baseline from a matched comparison
    ("what does my trend look like on weeks I eat this") instead of the
    generic all-other-weeks average every experiment type uses by default."""
    if not candidate_weeks:
        return set()
    days_by_week: dict[str, set[str]] = {}
    for e in db.query(models.FoodEntry).all():
        if e.name.strip().lower() != food_name:
            continue
        d = str(e.consumed_at)[:10]
        wk = _isoweek(d)
        if wk in candidate_weeks:
            days_by_week.setdefault(wk, set()).add(d)
    return {wk for wk, days in days_by_week.items() if len(days) >= min_days}


def _workout_present_weeks(db: Session, workout_type: str, candidate_weeks: set[str], min_sessions: int = 1) -> set[str]:
    """Of the given ISO weeks, which ones had `workout_type` logged at least
    `min_sessions` times -- i.e. weeks the routine was actually part of the
    pattern. min_sessions defaults to 1 rather than _food_present_weeks'
    2-distinct-days bar: workout logging is typically sparser than food
    logging (established_workouts itself only requires ~0.5 sessions/week
    on average), so requiring 2+ in a single week would exclude most
    legitimately-established routines."""
    if not candidate_weeks:
        return set()
    sessions_by_week: dict[str, int] = {}
    for e in db.query(models.WorkoutEntry).filter(models.WorkoutEntry.type == workout_type).all():
        d = str(e.logged_at)[:10]
        wk = _isoweek(d)
        if wk in candidate_weeks:
            sessions_by_week[wk] = sessions_by_week.get(wk, 0) + 1
    return {wk for wk, n in sessions_by_week.items() if n >= min_sessions}


def _matched_baseline(weight_obs: list[dict], fat_obs: list[dict], present_weeks: set[str], exp_week: str):
    """Weight/fat baseline (mean delta_per_day) computed from `present_weeks` only -- the
    shared "matched comparison instead of an unmatched all-other-weeks average" logic behind
    both the food-elimination and workout-routine outcomes, which only differ in how
    "present" is defined (food name vs. workout type). Returns (weight_baseline,
    fat_baseline, weeks_n) -- all None if fewer than 2 present weeks exist (one week isn't a
    baseline, it's a single data point); the caller keeps whatever generic baseline it
    already had in that case.

    The confound check that used to live in this function is now _confound_summary below --
    pulled out because it applies the same way regardless of whether a matched baseline was
    even found (habit experiments never have one, but still get a confound check against the
    generic all-other-weeks set)."""
    if len(present_weeks) < 2:
        return None, None, None

    present_weight = [r["delta_per_day"] for r in weight_obs if r["date"] in present_weeks]
    present_fat = [r["delta_per_day"] for r in fat_obs if r["date"] in present_weeks]
    weight_baseline = round(sum(present_weight) / len(present_weight), 6) if present_weight else None
    fat_baseline = round(sum(present_fat) / len(present_fat), 6) if present_fat else None

    return weight_baseline, fat_baseline, len(present_weeks)


_CONFOUND_VARS = ("avg_calories", "avg_steps")


def _confound_summary(weight_obs: list[dict], fat_obs: list[dict], baseline_weeks: set[str],
                       exp_week: str) -> dict:
    """One-variable-at-a-time confound check against every variable in _CONFOUND_VARS:
    average value across baseline_weeks vs. the experiment week, for whichever variables
    have data in both. Not a substitute for a real multivariate model -- the ~12-13 weeks of
    paired data this app typically has doesn't support one (see PRODUCT_NOTES.md) -- this is
    the cheap, honest middle step: surface the most obvious rival explanations rather than
    silently crediting whatever the experiment was actually about.

    baseline_weeks is whatever comparison set the caller already has -- the food/workout
    matched present-weeks when one was found, or the same generic all-other-weeks set used
    for weight_baseline/fat_baseline otherwise (the only option for a habit experiment, whose
    tracking habit is always freshly created for that one week with no prior history of its
    own). Returns {} if nothing is computable; a variable is only included when both a
    baseline and an experiment-week value exist for it."""
    result = {}
    for var in _CONFOUND_VARS:
        by_week = {
            r["date"]: r[var] for r in weight_obs + fat_obs
            if r.get(var) is not None
        }
        baseline_values = [by_week[wk] for wk in baseline_weeks if wk in by_week]
        if not baseline_values or exp_week not in by_week:
            continue
        result[var] = {
            "baseline": round(sum(baseline_values) / len(baseline_values), 1),
            "experiment": round(by_week[exp_week], 1),
        }
    return result


def _record_outcome(
    exp: models.HealthExperiment, db: Session, today: date, tz_offset_minutes: int = 0,
) -> None:
    """Fill outcome fields on exp using the experiment week's health data."""
    weight_obs, fat_obs = _load_weekly_obs(db, today, tz_offset_minutes=tz_offset_minutes)

    # Experiment week delta
    def find_week(obs, wk):
        for row in obs:
            if row["date"] == wk:
                return row["delta_per_day"]
        return None

    exp.weight_delta = find_week(weight_obs, exp.week)
    exp.fat_delta    = find_week(fat_obs,    exp.week)

    # Baseline: mean of all OTHER weeks
    other_weeks = {r["date"] for r in weight_obs if r["date"] != exp.week} | \
                  {r["date"] for r in fat_obs if r["date"] != exp.week}
    other_weight = [r["delta_per_day"] for r in weight_obs if r["date"] in other_weeks]
    other_fat    = [r["delta_per_day"] for r in fat_obs    if r["date"] in other_weeks]
    exp.weight_baseline = round(sum(other_weight) / len(other_weight), 6) if other_weight else None
    exp.fat_baseline    = round(sum(other_fat)    / len(other_fat),    6) if other_fat    else None

    # Confound check (avg_calories, avg_steps): defaults to the generic all-other-weeks set
    # above -- the only option for a habit experiment, whose tracking habit is always freshly
    # created for that one week with no prior history to match against. Overridden below to
    # the matched present-weeks set for food/workout experiments when one was found, same as
    # weight_baseline/fat_baseline are.
    confound_baseline_weeks = other_weeks

    # Habit completion rate during experiment week
    if exp.habit_id:
        ws = _week_start(exp.week)
        dates = [(ws + timedelta(days=i)).isoformat() for i in range(7)]
        completed_days = (
            db.query(models.HabitCompletion)
            .filter(
                models.HabitCompletion.habit_id == exp.habit_id,
                models.HabitCompletion.date.in_(dates),
            )
            .count()
        )
        exp.habit_completion_rate = round(completed_days / 7, 3)

    # Workout-routine outcome: a genuine before/after comparison with a real
    # p-value, using the exact same ttest_ind/unequal-variance pattern
    # _compute_segments already uses for the weight/fat correlation segments
    # -- not a new statistical concept, just applied to a new data source.
    if exp.workout_type:
        ws = _week_start(exp.week)
        exp_start = ws.isoformat()
        exp_end = (ws + timedelta(days=7)).isoformat()          # exclusive
        baseline_start = (ws - timedelta(days=56)).isoformat()  # 8 prior weeks

        exp_samples = [
            e.value for e in db.query(models.WorkoutEntry).filter(
                models.WorkoutEntry.type == exp.workout_type,
                models.WorkoutEntry.value.isnot(None),
                models.WorkoutEntry.logged_at >= exp_start,
                models.WorkoutEntry.logged_at < exp_end,
            ).all()
        ]
        baseline_samples = [
            e.value for e in db.query(models.WorkoutEntry).filter(
                models.WorkoutEntry.type == exp.workout_type,
                models.WorkoutEntry.value.isnot(None),
                models.WorkoutEntry.logged_at >= baseline_start,
                models.WorkoutEntry.logged_at < exp_start,
            ).all()
        ]

        exp.workout_experiment_n = len(exp_samples)
        exp.workout_baseline_n = len(baseline_samples)
        exp.workout_experiment_avg = round(sum(exp_samples) / len(exp_samples), 3) if exp_samples else None
        exp.workout_baseline_avg = round(sum(baseline_samples) / len(baseline_samples), 3) if baseline_samples else None

        exp.workout_p = None
        if len(exp_samples) >= 2 and len(baseline_samples) >= 2:
            try:
                result = ttest_ind(exp_samples, baseline_samples, equal_var=False)
                exp.workout_p = round(float(result.pvalue), 4)
            except Exception:
                pass

        # Matched weight/fat baseline -- same concept as the food-elimination override
        # below, applied to weeks this workout type was actually being logged instead of
        # weeks this food was actually being eaten. Also becomes the confound-check
        # baseline (set below) when found, in place of the generic all-other-weeks set.
        workout_present_weeks = _workout_present_weeks(db, exp.workout_type, other_weeks)
        w_base, f_base, weeks_n = _matched_baseline(weight_obs, fat_obs, workout_present_weeks, exp.week)
        if w_base is not None:
            exp.weight_baseline = w_base
        if f_base is not None:
            exp.fat_baseline = f_base
        exp.workout_baseline_weeks_n = weeks_n
        if weeks_n:
            confound_baseline_weeks = workout_present_weeks

    # Food-elimination outcome: adherence is a plain count, not a t-test --
    # there's no natural per-week sample the way individual WorkoutEntry
    # sessions provide one for the workout case above (this is a single
    # experiment week, not a series of sessions within it).
    if exp.food_name:
        ws = _week_start(exp.week)
        exp_start = ws.isoformat()
        exp_end = (ws + timedelta(days=7)).isoformat()  # exclusive

        matching = [
            e for e in db.query(models.FoodEntry).filter(
                models.FoodEntry.consumed_at >= exp_start,
                models.FoodEntry.consumed_at < exp_end,
            ).all()
            if e.name.strip().lower() == exp.food_name
        ]
        exp.food_experiment_count = len(matching)

        # Override the generic all-other-weeks weight/fat baseline (set
        # above) with a matched comparison: weeks this food was ACTUALLY
        # present at a regular clip, vs. the week it was cut. A comparison
        # against weeks that had nothing to do with this food is weaker
        # evidence than comparing against weeks the food was actually part
        # of the pattern. Falls back to the generic baseline (already set)
        # when fewer than 2 such weeks exist -- one week isn't a baseline,
        # it's a single data point.
        present_weeks = _food_present_weeks(db, exp.food_name, other_weeks)
        w_base, f_base, weeks_n = _matched_baseline(weight_obs, fat_obs, present_weeks, exp.week)
        if w_base is not None:
            exp.weight_baseline = w_base
        if f_base is not None:
            exp.fat_baseline = f_base
        exp.food_baseline_weeks_n = weeks_n
        if weeks_n:
            confound_baseline_weeks = present_weeks

    exp.confounds = json.dumps(confounds) if (confounds := _confound_summary(
        weight_obs, fat_obs, confound_baseline_weeks, exp.week
    )) else None


# ── Migration: AppSetting → table ────────────────────────────────────────────

def _migrate_appsetting(db: Session) -> Optional[models.HealthExperiment]:
    """One-time migration of the old AppSetting-backed experiment to the table."""
    row = db.query(models.AppSetting).filter_by(key="health_experiment").first()
    if not row:
        return None
    try:
        data = json.loads(row.value)
        # This JSON blob was written by code that predates the health_metric/
        # health_goal rename -- it's a frozen historical format, so it must be
        # read back with its original key names regardless of what the live
        # model calls them now.
        exp = models.HealthExperiment(
            week=data.get("week", _current_isoweek()),
            text=data.get("text", ""),
            hypothesis=data.get("hypothesis"),
            action=data.get("action"),
            needs_habit=data.get("needs_habit", False),
            habit_id=data.get("habit_id"),
            health_metric=data.get("withings_metric"),
            health_goal=data.get("withings_goal"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
        )
        db.add(exp)
        db.delete(row)
        db.flush()
        db.commit()
        return exp
    except Exception:
        return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/api/health/correlations")
def get_health_correlations(request: Request, db: Session = Depends(get_db)):
    today = local_date(request)
    weight_obs, fat_obs = _load_weekly_obs(db, today, tz_offset_minutes=utc_offset_minutes(request))

    if not weight_obs and not fat_obs:
        return {
            "correlations": [],
            "segments": [],
            "summary": "Not enough weekly data yet — keep logging to enable correlation analysis.",
            "weight_n": 0,
            "fat_n": 0,
        }

    correlations = _compute_correlations(weight_obs, fat_obs)
    segments     = _compute_segments(weight_obs, fat_obs)

    return {
        "correlations": correlations,
        "segments":     segments,
        "summary":      _llm_summary(correlations),
        "weight_n":     len(weight_obs),
        "fat_n":        len(fat_obs),
    }


def auto_expire_stale_experiments(
    db: Session, current_week: str, today: date, tz_offset_minutes: int = 0,
) -> None:
    """Archive habits and dismiss any active experiments from previous weeks.

    Called automatically when the frontend fetches the current experiment so
    that week-rollover cleanup happens even when the user never clicks
    'Dismiss & generate new'.
    """
    stale = (
        db.query(models.HealthExperiment)
        .filter(
            models.HealthExperiment.status == "active",
            models.HealthExperiment.week != current_week,
        )
        .all()
    )
    for exp in stale:
        _record_outcome(exp, db, today, tz_offset_minutes)
        exp.status       = "dismissed"
        exp.dismissed_at = datetime.now(timezone.utc)
        if exp.habit_id:
            habit = db.query(models.Habit).filter_by(id=exp.habit_id).first()
            if habit and not habit.archived:
                habit.archived    = True
                habit.archived_at = datetime.now(timezone.utc)
    if stale:
        db.commit()


@router.get("/api/health/experiment")
def get_health_experiment(request: Request, db: Session = Depends(get_db)):
    """Return the active experiment for the current week, generating one if needed."""
    today = local_date(request)
    current_week = _current_isoweek(today)
    tz_offset = utc_offset_minutes(request)

    # Archive habits and expire any active experiments from previous weeks so
    # week-rollover cleanup happens automatically (not just on explicit dismiss).
    auto_expire_stale_experiments(db, current_week, today, tz_offset)

    # Check table for an active experiment this week
    exp = (
        db.query(models.HealthExperiment)
        .filter_by(week=current_week, status="active")
        .first()
    )
    if exp:
        return _exp_to_dict(exp)

    # Generate a new experiment
    weight_obs, fat_obs = _load_weekly_obs(db, today, tz_offset_minutes=tz_offset)
    correlations = _compute_correlations(weight_obs, fat_obs) if (weight_obs or fat_obs) else []
    exp = _generate_experiment(correlations, db, today, tz_offset)
    return _exp_to_dict(exp)


@router.delete("/api/health/experiment")
def dismiss_health_experiment(request: Request, db: Session = Depends(get_db)):
    """Dismiss the active experiment, recording outcome metrics."""
    today = local_date(request)
    current_week = _current_isoweek(today)
    exp = (
        db.query(models.HealthExperiment)
        .filter_by(week=current_week, status="active")
        .first()
    )
    if exp:
        _record_outcome(exp, db, today, utc_offset_minutes(request))
        exp.status       = "dismissed"
        exp.dismissed_at = datetime.now(timezone.utc)

        # Archive linked habit
        if exp.habit_id:
            habit = db.query(models.Habit).filter_by(id=exp.habit_id).first()
            if habit and not habit.archived:
                habit.archived    = True
                habit.archived_at = datetime.now(timezone.utc)

        db.commit()
    return {"ok": True}


@router.get("/api/health/experiments")
def get_health_experiments(db: Session = Depends(get_db)):
    """Return all past experiments ordered newest first."""
    exps = (
        db.query(models.HealthExperiment)
        .order_by(models.HealthExperiment.created_at.desc())
        .all()
    )
    return [_exp_to_dict(e) for e in exps]


@router.post("/api/health/experiments/recompute")
def recompute_experiment_outcomes(db: Session = Depends(get_db)):
    """One-off maintenance: re-run _record_outcome for every already-completed experiment,
    so analysis improvements (e.g. the habit-experiment + avg_steps confound check added
    after some experiments already ran) apply retroactively instead of only to future ones.

    Anchors each experiment's own 90-day lookback window to shortly after ITS OWN week, not
    today -- _load_weekly_obs is a 90-day trailing window ending at whatever "today" it's
    given, so recomputing an old experiment against date.today() would push its own week
    outside that window and silently blank it out instead of fixing it."""
    experiments = (
        db.query(models.HealthExperiment)
        .filter(models.HealthExperiment.status != "active")
        .all()
    )
    for exp in experiments:
        anchor = _week_start(exp.week) + timedelta(days=7)
        _record_outcome(exp, db, anchor)
    db.commit()
    return {"recomputed": len(experiments)}
