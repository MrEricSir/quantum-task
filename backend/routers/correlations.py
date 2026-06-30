"""
Health × lifestyle correlation analysis + weekly experiment suggestions.

GET  /api/health/correlations  – Pearson r between lifestyle factors and health outcomes
GET  /api/health/experiment    – This week's LLM-generated experiment suggestion
DELETE /api/health/experiment  – Dismiss experiment (archives linked habit if any)

Factors
───────
• avg_steps      – daily step count average (Withings)
• avg_hr         – heart rate average (Withings, sparse)
• habit_rate     – fraction of habit-days completed (0–1)
• cards_done     – tasks marked complete in the window

Outputs (correlations)
──────────────────────
• correlations   – list sorted by |r| descending
• scatter        – per-interval data points for the top pairs
• summary        – LLM-generated plain-language interpretation

Outputs (experiment)
────────────────────
• week           – ISO week string the experiment was generated for
• text           – 2-3 sentence description for the user
• hypothesis     – what we expect to see if the experiment works
• action         – specific daily action (or null if passive observation)
• needs_habit    – whether a daily habit was auto-created
• habit_id       – id of the linked habit, if any
"""

import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

import models
from deps import get_db, llm_client, LLM_MODEL, local_date

router = APIRouter()

_SUMMARY_CACHE: dict[str, tuple[float, str]] = {}
_SUMMARY_TTL = 3600  # 1 hour


# ── Math helpers ──────────────────────────────────────────────────────────────

def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx  = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy  = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


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
    """Return (weight_obs, fat_obs) — weekly-binned observations for correlation analysis."""
    start_str = (today - timedelta(days=days)).isoformat()

    # Measurements
    measurements = (
        db.query(models.WithingsMeasurement)
        .filter(models.WithingsMeasurement.date >= start_str)
        .order_by(models.WithingsMeasurement.date)
        .all()
    )
    by_date: dict[str, dict[str, float]] = {}
    for m in measurements:
        by_date.setdefault(m.date, {})[m.metric] = m.value

    # Habit completions
    active_habit_count = (
        db.query(models.Habit)
        .filter(
            models.Habit.archived == False,       # noqa: E712
            models.Habit.withings_metric == None, # noqa: E711
        )
        .count()
    )
    completions_by_date: dict[str, int] = {}
    for hc in (
        db.query(models.HabitCompletion)
        .filter(models.HabitCompletion.date >= start_str)
        .all()
    ):
        completions_by_date[hc.date] = completions_by_date.get(hc.date, 0) + 1

    # Task completions
    cards_done_by_date: dict[str, int] = {}
    for card in (
        db.query(models.Card)
        .filter(
            models.Card.completed == True,          # noqa: E712
            models.Card.completed_at.isnot(None),
        )
        .all()
    ):
        d = card.completed_at.strftime("%Y-%m-%d") if hasattr(card.completed_at, "strftime") else str(card.completed_at)[:10]
        if d >= start_str:
            cards_done_by_date[d] = cards_done_by_date.get(d, 0) + 1

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
        week_completions[_isoweek(d)] = week_completions.get(_isoweek(d), 0) + cnt

    week_cards: dict[str, int] = {}
    for d, cnt in cards_done_by_date.items():
        week_cards[_isoweek(d)] = week_cards.get(_isoweek(d), 0) + cnt

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
            cards = week_cards.get(curr_wk) or None
            rows.append({
                "date":          curr_wk,
                "delta_per_day": delta_per_day,
                "avg_steps":     week_avgs[curr_wk].get("steps"),
                "avg_hr":        week_avgs[curr_wk].get("heart_rate"),
                "habit_rate":    habit_rate,
                "cards_done":    cards,
            })
        return rows

    return build_obs("weight"), build_obs("fat_ratio")


# ── Correlation computation ───────────────────────────────────────────────────

FACTORS = [
    ("avg_steps",  "Daily steps"),
    ("avg_hr",     "Resting heart rate"),
    ("habit_rate", "Habit completion rate"),
    ("cards_done", "Tasks completed"),
]


def _compute_correlations(weight_obs: list[dict], fat_obs: list[dict]) -> list[dict]:
    correlations = []
    for obs, outcome_label, outcome_unit in [
        (weight_obs, "Weight change",    "kg/day"),
        (fat_obs,    "Body fat change",  "%/day"),
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
            r = _pearson(list(xs), list(ys))
            if r is None:
                continue
            scatter = [
                {"date": row["date"], "x": round(row[fkey], 3), "y": round(row["delta_per_day"], 5)}
                for row in obs
                if row.get(fkey) is not None
            ]
            correlations.append({
                "factor":       flabel,
                "factor_key":   fkey,
                "outcome":      outcome_label,
                "outcome_unit": outcome_unit,
                "r":            round(r, 3),
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
    "Remind the user that correlation does not imply causation and that "
    "sample sizes are small. Be direct — avoid filler phrases."
)


def _llm_summary(correlations: list[dict]) -> str:
    if not correlations:
        return ""
    key = json.dumps([(c["r"], c["factor"], c["outcome"]) for c in correlations[:6]])
    cached = _SUMMARY_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _SUMMARY_TTL:
        return cached[1]
    lines = [
        f"{c['factor']} vs {c['outcome']}: r={c['r']:+.2f} (n={c['n']})"
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
factors and health outcomes, suggest one specific experiment to try for the \
next 7 days. The experiment should be actionable, measurable, and directly \
connected to the strongest signal in the data.

Respond with ONLY valid JSON (no markdown, no explanation):
{
  "text": "2-3 sentence description of the experiment and why it's worth trying",
  "hypothesis": "If I do X, I expect to see Y by end of week",
  "action": "The specific daily action to take, e.g. '8,000 steps every day' — or null if passive",
  "needs_habit": true or false,
  "withings_metric": "steps" | "fat_ratio" | "weight" | null,
  "withings_goal": numeric goal value or null
}

needs_habit should be true only when the experiment requires a specific daily \
effort that the user should track (e.g. hitting a step target, drinking water, \
meditating). Set to false for observation-only or diet experiments.

withings_metric + withings_goal: set these when the experiment goal maps \
directly to a Withings measurement. For example, if the experiment is \
"walk at least 8,000 steps daily", set withings_metric="steps" and \
withings_goal=8000 — this auto-checks the habit off when the target is \
synced from the device. Use null for both if the experiment is not \
measurable via Withings.\
"""


def _generate_experiment(correlations: list[dict], db: Session) -> dict:
    """Generate a new experiment using LLM. Stores result in AppSetting."""
    week = _current_isoweek()

    if not correlations:
        experiment = {
            "week": week,
            "text": "Keep logging your health data to unlock personalised experiments. You need at least 3 weeks of data.",
            "hypothesis": None,
            "action": None,
            "needs_habit": False,
            "habit_id": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        db.merge(models.AppSetting(key="health_experiment", value=json.dumps(experiment)))
        db.commit()
        return experiment

    lines = [
        f"{c['factor']} vs {c['outcome']}: r={c['r']:+.2f} (n={c['n']})"
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
    except Exception:
        text = "Try increasing your daily step count by 10% compared to your recent average."
        hypothesis = "More consistent movement should correlate with better weight outcomes."
        action = None
        needs_habit = False
        withings_metric = None
        withings_goal = None

    # Auto-create a tracking habit if the experiment requires daily effort
    habit_id = None
    if needs_habit and action:
        habit_name = f"🧪 {action[:60]}"
        habit = models.Habit(
            name=habit_name,
            withings_metric=withings_metric,
            withings_goal=withings_goal,
        )
        db.add(habit)
        db.flush()
        habit_id = habit.id

    experiment = {
        "week":            week,
        "text":            text,
        "hypothesis":      hypothesis,
        "action":          action,
        "needs_habit":     needs_habit,
        "habit_id":        habit_id,
        "withings_metric": withings_metric,
        "withings_goal":   withings_goal,
        "created_at":      datetime.now(timezone.utc).isoformat(),
    }
    db.merge(models.AppSetting(key="health_experiment", value=json.dumps(experiment)))
    db.commit()
    return experiment


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


@router.get("/api/health/experiment")
def get_health_experiment(request: Request, db: Session = Depends(get_db)):
    """Return the current week's experiment, generating one if needed."""
    current_week = _current_isoweek()

    # Check for existing experiment for this week
    row = db.query(models.AppSetting).filter_by(key="health_experiment").first()
    if row:
        try:
            stored = json.loads(row.value)
            if stored.get("week") == current_week:
                return stored
        except Exception:
            pass

    # Generate a new one based on current correlations
    today = local_date(request)
    weight_obs, fat_obs = _load_weekly_obs(db, today)
    correlations = _compute_correlations(weight_obs, fat_obs) if (weight_obs or fat_obs) else []
    return _generate_experiment(correlations, db)


@router.delete("/api/health/experiment")
def dismiss_health_experiment(db: Session = Depends(get_db)):
    """Dismiss the current experiment. Archives linked habit if any."""
    row = db.query(models.AppSetting).filter_by(key="health_experiment").first()
    if row:
        try:
            stored = json.loads(row.value)
            habit_id = stored.get("habit_id")
            if habit_id:
                habit = db.query(models.Habit).filter_by(id=habit_id).first()
                if habit and not habit.archived:
                    habit.archived = True
                    habit.archived_at = datetime.now(timezone.utc)
        except Exception:
            pass
        db.delete(row)
        db.commit()
    return {"ok": True}
