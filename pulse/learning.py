"""Prospective calibration from Luma's own completed forecasts."""
from math import exp, log
from statistics import median

from .journal import forecast_age_band
from .operations import quantile


def finish_prediction(estimate):
    if estimate.get("finish_at") is None:
        return None
    lo, hi = estimate["finish_range"]
    return {"low": lo, "finish": estimate["finish_at"], "high": hi}


def replace_time(estimate, forecast, now, reason):
    estimate.update(
        finish_at=forecast["finish"], finish_range=[forecast["low"], forecast["high"]],
        eta_median=max(1, round(forecast["finish"]-now)),
        eta=[max(1, round(forecast[key]-now)) for key in ("low", "high")],
        eta_basis=reason,
    )


def bias(rows):
    ratios = []
    for row in rows:
        raw_remaining = max(10, row["raw_local"]["finish"] - row["at"])
        actual_remaining = max(10, row["actual"] - row["at"])
        ratios.append(log(actual_remaining / raw_remaining))
    return max(.55, min(1.8, exp(median(ratios))))


def abs_errors(rows, scale):
    return [abs(row["at"] + (row["raw_local"]["finish"]-row["at"])*scale-row["actual"]) for row in rows]


def calibrate(records, raw, kind, method, elapsed, now):
    """Recent residuals are tested on later turns before changing the ETA.

    Returns the forecast and a user-inspectable description. At most one
    observation from each completed turn contributes within an elapsed band.
    """
    band = forecast_age_band(elapsed)
    matched = [row for row in records if row["band"] == band and row["local_method"] == method]
    similar = [row for row in matched if row["kind"] == kind]
    rows = (similar if len(similar) >= 24 else matched)[:60]
    info = {"count": len(rows), "applied": False, "minimum": 24}
    if raw is None or len(rows) < 24:
        return raw, info

    # The latest third has no role in fitting this provisional scale.
    split = max(8, len(rows)//3)
    calibration = rows[split:]
    test = rows[:split]
    scale = bias(calibration)
    baseline = abs_errors(test, 1)
    learned = abs_errors(test, scale)
    score = lambda errors: median(errors) + .35*sum(errors)/len(errors)
    info.update(test_count=split, baseline_error=round(median(baseline), 1),
                learned_error=round(median(learned), 1))
    if score(learned) >= .95*score(baseline):
        return raw, info

    # Translate the interval with the point while allowing observed large
    # misses to widen it. The raw interval remains inspectable in the journal.
    shift = (raw["finish"]-now)*(scale-1)
    radius = quantile(sorted(abs_errors(calibration, scale)), .8)
    point = max(now+1, raw["finish"]+shift)
    result = {"finish": point, "low": max(now+1, min(raw["low"]+shift, point-radius)),
              "high": max(raw["high"]+shift, point+radius)}
    info.update(applied=True, scale=round(scale, 2))
    return result, info


def blend_eta(local, advisor, weight=.25):
    """Retain local interval width and expand when the advisor disagrees."""
    point = local["finish"]*(1-weight) + advisor["finish"]*weight
    shift = point-local["finish"]
    return {"finish": point, "low": min(local["low"]+shift, advisor["low"]),
            "high": max(local["high"]+shift, advisor["high"])}


def trust_advisor(scores):
    """The fixed blend is evaluated prospectively on matched observations."""
    if not scores or not scores.get("local") or not scores.get("blend"):
        return False
    local, blend = scores["local"], scores["blend"]
    return (local["count"] >= 12 and blend["median"] <= .9*local["median"]
            and blend["mean"] < .95*local["mean"])
