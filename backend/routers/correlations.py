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
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from scipy.stats import pearsonr
from sqlalchemy.orm import Session

import models
from deps import get_db, llm_client, LLM_MODEL, local_date

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


def _current_isoweek() -> str:
    y, w, _ = date.today().isocalendar()
    return f"{y}-W{w:02d}"


# ── Shared data loader ────────────────────────────────────────────────────────

def _load_weekly_obs(db: Session, today: date, days: int = 90) -> tuple[list[dict], list[dict]]:
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
            models.Habit.withings_metric == None,  # noqa: E711
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
    ).all():
        d = (
            card.completed_at.strftime("%Y-%m-%d")
            if hasattr(card.completed_at, "strftime")
            else str(card.completed_at)[:10]
        )
        if d >= start_str:
            cards_done_by_date[d] = cards_done_by_date.get(d, 0) + 1

    # Food quality + calories: collect per-day scores to weekly-bin later
    food_quality_by_date: dict[str, list[float]] = {}
    food_calories_by_date: dict[str, list[float]] = {}
    for entry in db.query(models.FoodEntry).all():
        d_str = str(entry.consumed_at)[:10]
        if d_str >= start_str:
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
                "avg_food_quality": sum(fq) / len(fq) if fq else None,
                "avg_calories":     sum(fc) / len(fc) if (fc := week_food_calories.get(curr_wk)) else None,
            })
        return rows

    return build_obs("weight"), build_obs("fat_ratio")


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
        return {
            "factor":       flabel,
            "factor_key":   fkey,
            "outcome":      outcome_label,
            "outcome_unit": outcome_unit,
            "threshold":    round(threshold, 2),
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
            max_tokens=200,
        )
        text = resp.choices[0].message.content.strip()
    except Exception:
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

Respond with ONLY valid JSON (no markdown, no explanation):
{
  "text": "2-3 sentence description of the experiment and why it's worth trying",
  "hypothesis": "If I [specific action with concrete number], I expect to see Y by end of week",
  "action": "The specific daily action with a CONCRETE NUMBER, e.g. '8,000 steps every day'",
  "needs_habit": true or false,
  "withings_metric": "steps" | "fat_ratio" | "weight" | null,
  "withings_goal": numeric goal value or null
}

CRITICAL: "action" must ALWAYS contain a specific measurable target — never vague \
phrases like "increase my steps" or "walk more". Always include a concrete number: \
"8,000 steps every day", "45 minutes of walking daily", "7 hours of sleep". \
If the experiment is passive observation with no daily action, set action to null.

needs_habit should be true only when the experiment requires a specific daily \
effort to track (e.g. hitting a step target). Set false for passive observation.

withings_metric/withings_goal: ONLY set these when the experiment's primary \
measurable outcome is literally one of the three Withings-tracked metrics: step \
count, body fat percentage, or body weight. The withings_goal MUST match the \
number in the action field exactly. Examples: \
"Walk 8,000 steps every day" → withings_metric="steps", withings_goal=8000. \
"Reduce body fat to 18%" → withings_metric="fat_ratio", withings_goal=18. \
Examples where you must leave it null: "1 hour of screen-free time", "read \
before bed", "meditate 10 minutes", "no alcohol", "sleep by 10pm" — these are \
behavioral habits that cannot be verified by a Withings device, so \
withings_metric MUST be null. When in doubt, set null.\
"""


def _generate_experiment(correlations: list[dict], db: Session) -> models.HealthExperiment:
    """Generate a new experiment via LLM, persist to health_experiments table."""
    week = _current_isoweek()

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
    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _EXPERIMENT_SYSTEM},
                {"role": "user",   "content": "\n".join(lines)},
            ],
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        llm_data = json.loads(raw)
        text           = llm_data.get("text", "")
        hypothesis     = llm_data.get("hypothesis")
        action         = llm_data.get("action")
        needs_habit    = bool(llm_data.get("needs_habit", False))
        withings_metric = llm_data.get("withings_metric") or None
        withings_goal   = llm_data.get("withings_goal")
        if withings_goal is not None:
            try:
                withings_goal = float(withings_goal)
            except (TypeError, ValueError):
                withings_goal = None
        if withings_metric not in ("steps", "fat_ratio", "weight", None):
            withings_metric = None
            withings_goal = None

        # Use prose text as authoritative source for step goals — the LLM's JSON
        # numbers are less reliable than the numbers it writes in action/hypothesis.
        import re as _re

        def _extract_steps(text: str | None) -> float | None:
            """Extract a plausible daily step count from text (100–50,000)."""
            if not text:
                return None
            m = _re.search(r'([\d,]+)\+?\s*(?:daily\s+)?steps', text, _re.I)
            if m:
                val = float(m.group(1).replace(",", ""))
                return val if 100 <= val <= 50_000 else None
            return None

        if action and "steps" in action.lower():
            # Action is explicitly about steps — metric must be "steps" regardless of
            # what the LLM put in the JSON (catches fat_ratio/weight confusion).
            withings_metric = "steps"
            # Prefer action text; fall back to hypothesis if action value is implausible
            withings_goal = _extract_steps(action) or _extract_steps(hypothesis)
        elif withings_metric == "steps":
            # LLM said steps but no "steps" in action — correct goal from hypothesis
            if withings_goal is None or withings_goal > 50_000:
                withings_goal = _extract_steps(hypothesis)
        elif withings_metric is None and action:
            # Fallback: action didn't mention "steps" explicitly but hypothesis might
            _hypo_steps = _extract_steps(hypothesis)
            if _hypo_steps:
                withings_metric = "steps"
                withings_goal = _hypo_steps

        # Final guard: clear any remaining implausible step goal
        if withings_metric == "steps" and not withings_goal:
            withings_metric = None
            withings_goal = None

        # If needs_habit is set but no concrete action was produced, the LLM was vague.
        # Manufacture a sensible default so the experiment still gets a tracking habit.
        if needs_habit and not action:
            if withings_metric == "steps":
                action = "8,000 steps every day"
                withings_goal = withings_goal or 8000.0
            else:
                # Can't auto-infer a goal for other metrics — leave needs_habit False
                needs_habit = False
    except Exception:
        text = "Try increasing your daily step count by 10% compared to your recent average."
        hypothesis = "More consistent movement should correlate with better weight outcomes."
        action = None
        needs_habit = False
        withings_metric = None
        withings_goal = None

    habit_id = None
    if action:
        # Always create a tracking habit — even for non-Withings experiments it
        # serves as a daily reminder and shows up in the Health & Habits section.
        habit_name = f"🧪 {action[:60]}"
        habit = models.Habit(
            name=habit_name,
            withings_metric=withings_metric,
            withings_goal=withings_goal,
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
        withings_metric=withings_metric,
        withings_goal=withings_goal,
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
        "withings_metric":      exp.withings_metric,
        "withings_goal":        exp.withings_goal,
        "status":               exp.status,
        "created_at":           exp.created_at.isoformat() if exp.created_at else None,
        "dismissed_at":         exp.dismissed_at.isoformat() if exp.dismissed_at else None,
        "habit_completion_rate": exp.habit_completion_rate,
        "weight_delta":         exp.weight_delta,
        "fat_delta":            exp.fat_delta,
        "weight_baseline":      exp.weight_baseline,
        "fat_baseline":         exp.fat_baseline,
    }


def _record_outcome(exp: models.HealthExperiment, db: Session, today: date) -> None:
    """Fill outcome fields on exp using the experiment week's health data."""
    weight_obs, fat_obs = _load_weekly_obs(db, today)

    # Experiment week delta
    def find_week(obs, wk):
        for row in obs:
            if row["date"] == wk:
                return row["delta_per_day"]
        return None

    exp.weight_delta = find_week(weight_obs, exp.week)
    exp.fat_delta    = find_week(fat_obs,    exp.week)

    # Baseline: mean of all OTHER weeks
    other_weight = [r["delta_per_day"] for r in weight_obs if r["date"] != exp.week]
    other_fat    = [r["delta_per_day"] for r in fat_obs    if r["date"] != exp.week]
    exp.weight_baseline = round(sum(other_weight) / len(other_weight), 6) if other_weight else None
    exp.fat_baseline    = round(sum(other_fat)    / len(other_fat),    6) if other_fat    else None

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


# ── Migration: AppSetting → table ────────────────────────────────────────────

def _migrate_appsetting(db: Session) -> Optional[models.HealthExperiment]:
    """One-time migration of the old AppSetting-backed experiment to the table."""
    row = db.query(models.AppSetting).filter_by(key="health_experiment").first()
    if not row:
        return None
    try:
        data = json.loads(row.value)
        exp = models.HealthExperiment(
            week=data.get("week", _current_isoweek()),
            text=data.get("text", ""),
            hypothesis=data.get("hypothesis"),
            action=data.get("action"),
            needs_habit=data.get("needs_habit", False),
            habit_id=data.get("habit_id"),
            withings_metric=data.get("withings_metric"),
            withings_goal=data.get("withings_goal"),
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
    weight_obs, fat_obs = _load_weekly_obs(db, today)

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


def auto_expire_stale_experiments(db: Session, current_week: str, today: date) -> None:
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
        _record_outcome(exp, db, today)
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
    current_week = _current_isoweek()
    today = local_date(request)

    # Archive habits and expire any active experiments from previous weeks so
    # week-rollover cleanup happens automatically (not just on explicit dismiss).
    auto_expire_stale_experiments(db, current_week, today)

    # Check table for an active experiment this week
    exp = (
        db.query(models.HealthExperiment)
        .filter_by(week=current_week, status="active")
        .first()
    )
    if exp:
        return _exp_to_dict(exp)

    # One-time migration from legacy AppSetting storage
    exp = _migrate_appsetting(db)
    if exp and exp.week == current_week:
        return _exp_to_dict(exp)

    # Generate a new experiment
    weight_obs, fat_obs = _load_weekly_obs(db, today)
    correlations = _compute_correlations(weight_obs, fat_obs) if (weight_obs or fat_obs) else []
    exp = _generate_experiment(correlations, db)
    return _exp_to_dict(exp)


@router.delete("/api/health/experiment")
def dismiss_health_experiment(request: Request, db: Session = Depends(get_db)):
    """Dismiss the active experiment, recording outcome metrics."""
    current_week = _current_isoweek()
    exp = (
        db.query(models.HealthExperiment)
        .filter_by(week=current_week, status="active")
        .first()
    )
    if exp:
        today = local_date(request)
        _record_outcome(exp, db, today)
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
