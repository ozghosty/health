"""Fetch Withings and Google Health (Fitbit) data and merge it into data/health.json.

Run by the scheduled GitHub Action. Needs these environment variables (Actions secrets):
    WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, WITHINGS_REFRESH_TOKEN
    GOOGLE_CLIENT_ID,   GOOGLE_CLIENT_SECRET,   GOOGLE_REFRESH_TOKEN
    TOKENS_DIR   where rotated refresh tokens are written, one file per secret name,
                 so the workflow can save them back as secrets

Only daily summaries are stored: no clock times, so the public file doesn't reveal
your routine. Each metric is fetched on its own; one that fails is logged and left
as it was, rather than wiping existing days or stopping the others.
"""

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "health.json"
INTRADAY_BACKFILL_DAYS = 30  # first run only: minute-level HR is large, so don't pull a year of it

errors = []


def log(msg):
    print(msg, flush=True)


def request(url, data=None, headers=None, method=None):
    body = None
    if isinstance(data, dict) and headers and headers.get("Content-Type") == "application/json":
        body = json.dumps(data).encode()
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code} from {url.split('?')[0]}: {e.read().decode(errors='replace')[:500]}")


def save_token(name, value, old):
    """Refresh tokens rotate; write the new one where the workflow saves it as a secret."""
    if not value or value == old:
        return
    out = Path(os.environ["TOKENS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(value)
    log(f"  {name} rotated")


def env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def attempt(label, fn, *args):
    """Run one metric fetch; log and carry on if it fails."""
    try:
        result = fn(*args)
        log(f"  {label}: {len(result)} day(s)")
        return result
    except Exception as e:  # noqa: BLE001 - every failure is reported at the end
        errors.append(f"{label}: {e}")
        log(f"  {label}: FAILED {e}")
        return {}


# ---------------------------------------------------------------- Withings

def withings_token():
    old = env("WITHINGS_REFRESH_TOKEN")
    # Withings reports failure inside an HTTP 200; `status` is the real result
    resp = request("https://wbsapi.withings.net/v2/oauth2", {
        "action": "requesttoken", "grant_type": "refresh_token", "client_id": env("WITHINGS_CLIENT_ID"),
        "client_secret": env("WITHINGS_CLIENT_SECRET"), "refresh_token": old,
    })
    if resp.get("status") != 0:
        raise RuntimeError(f"Withings token refresh failed (status {resp.get('status')}): {resp.get('error')}. "
                           "Run scripts/setup_auth.py --only withings again.")
    save_token("WITHINGS_REFRESH_TOKEN", resp["body"]["refresh_token"], old)
    return resp["body"]["access_token"]


def withings_measures(token, start, end):
    """Every real (category 1) measurement group between two datetimes."""
    groups, offset = [], 0
    while True:
        params = {"action": "getmeas", "meastypes": "1,6,9,10,11", "category": 1,
                  "startdate": int(start.timestamp()), "enddate": int(end.timestamp())}
        if offset:
            params["offset"] = offset
        resp = request("https://wbsapi.withings.net/measure", params, {"Authorization": f"Bearer {token}"})
        if resp.get("status") != 0:
            raise RuntimeError(f"getmeas status {resp.get('status')}: {resp.get('error')}")
        body = resp["body"]
        groups += body.get("measuregrps", [])
        if not body.get("more"):
            return groups
        offset = body["offset"]


def withings_days(groups, cfg, tz):
    """Split measurement groups into weight/fat per day and BP sessions per day."""
    weights, bp = {}, []
    for g in sorted(groups, key=lambda g: g["date"]):
        if g.get("category") != 1:
            continue
        m = {x["type"]: x["value"] * 10 ** x["unit"] for x in g["measures"]}
        when = dt.datetime.fromtimestamp(g["date"], tz)
        if 1 in m:
            # first weigh-in of the day, which is normally the morning one
            day = weights.setdefault(when.date().isoformat(), {})
            day.setdefault("weightKg", round(m[1], 1))
            if 6 in m:
                day.setdefault("fatPct", round(m[6], 1))
        if 9 in m and 10 in m:
            bp.append({"when": when, "sys": round(m[10]), "dia": round(m[9]),
                       "pulse": round(m[11]) if 11 in m else None})

    days = {}
    for date, slot, session in bp_sessions(bp, cfg["blood_pressure"]):
        days.setdefault(date, {})[slot] = session
    return weights, days


def bp_sessions(readings, rules):
    """Group readings into sessions, place each in AM or PM, and summarise it.
    Yields (date, "bpAm"|"bpPm", summary). Clock times are dropped here."""
    gap = dt.timedelta(minutes=rules.get("session_gap_minutes", 30))
    sessions = []
    for r in readings:
        if sessions and r["when"] - sessions[-1][-1]["when"] <= gap:
            sessions[-1].append(r)
        else:
            sessions.append([r])

    def in_window(t, window):
        lo, hi = (dt.time.fromisoformat(x) for x in window)
        return lo <= t <= hi

    best = {}
    for s in sessions:
        start = s[0]["when"]
        slot = ("bpAm" if in_window(start.time(), rules["am_window"])
                else "bpPm" if in_window(start.time(), rules["pm_window"]) else None)
        if not slot:
            continue
        kept = s[rules.get("discard_first", 0):]
        if len(kept) < rules.get("min_readings", 1):
            continue
        key = (start.date().isoformat(), slot)
        # if there are two sessions in one slot, the one with more readings wins
        if key not in best or len(kept) > len(best[key]):
            best[key] = kept

    rank = (lambda r: r["sys"]) if rules.get("rank_by", "systolic") == "systolic" \
        else (lambda r: (r["sys"] + 2 * r["dia"]) / 3)  # mean arterial pressure
    for (date, slot), kept in sorted(best.items()):
        if rules.get("method", "lowest") == "lowest":
            used = sorted(kept, key=rank)[: rules.get("keep", 2)]
        else:
            used = kept
        avg = lambda f: int(sum(r[f] for r in used) / len(used) + 0.5) if all(r[f] is not None for r in used) else None
        yield date, slot, {
            "sys": avg("sys"), "dia": avg("dia"), "pulse": avg("pulse"),
            "readings": [{"sys": r["sys"], "dia": r["dia"], "pulse": r["pulse"], **({"used": True} if any(r is u for u in used) else {})}
                         for r in kept],
        }


def fetch_withings(cfg, tz, start, end):
    log("Withings")
    token = withings_token()
    groups = withings_measures(token, start, end)
    log(f"  {len(groups)} measurement group(s)")
    weights, bp = withings_days(groups, cfg, tz)
    log(f"  weight: {len(weights)} day(s), BP: {len(bp)} day(s)")
    return weights, bp


# ---------------------------------------------------------------- Google Health

GH = "https://health.googleapis.com/v4/users/me/dataTypes"


def google_token():
    old = env("GOOGLE_REFRESH_TOKEN")
    resp = request("https://oauth2.googleapis.com/token", {
        "grant_type": "refresh_token", "client_id": env("GOOGLE_CLIENT_ID"),
        "client_secret": env("GOOGLE_CLIENT_SECRET"), "refresh_token": old,
    })
    save_token("GOOGLE_REFRESH_TOKEN", resp.get("refresh_token"), old)  # Google rarely rotates
    return resp["access_token"]


def camel(data_type):
    head, *rest = data_type.split("-")
    return head + "".join(w.capitalize() for w in rest)


def gh_list(token, data_type, filt, page_size=None):
    """All data points of one type matching an AIP-160 filter (newest first)."""
    points, page = [], None
    while True:
        q = {"filter": filt}
        if page_size:
            q["pageSize"] = page_size
        if page:
            q["pageToken"] = page
        resp = request(f"{GH}/{data_type}/dataPoints?" + urllib.parse.urlencode(q),
                       headers={"Authorization": f"Bearer {token}"})
        points += [p[camel(data_type)] for p in resp.get("dataPoints", []) if camel(data_type) in p]
        page = resp.get("nextPageToken")
        if not page:
            return points


def gh_daily(token, data_type, start, end, value):
    """A daily-summary type as {date: value(point)}; start/end are dates, end exclusive."""
    field = data_type.replace("-", "_")
    filt = f'{field}.date >= "{start}" AND {field}.date < "{end}"'
    out = {}
    for p in gh_list(token, data_type, filt, 10000):
        v = value(p)
        if v is not None:
            out[civil_date(p["date"])] = v
    return out


def civil_date(d):
    return dt.date(d["year"], d["month"], d["day"]).isoformat()


def first_number(obj):
    """Rollup value objects name their field after the aggregation (e.g. countSum); take the number."""
    for v in (obj or {}).values():
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.replace(".", "", 1).isdigit()):
            return float(v)
    return None


def gh_steps(token, start, end):
    out, chunk_start = {}, start
    while chunk_start < end:
        chunk_end = min(end, chunk_start + dt.timedelta(days=90))  # API maximum per request
        civil = lambda d: {"date": {"year": d.year, "month": d.month, "day": d.day}}
        resp = request(f"{GH}/steps/dataPoints:dailyRollUp",
                       {"range": {"start": civil(chunk_start), "end": civil(chunk_end)}, "windowSizeDays": 1},
                       {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, "POST")
        for p in resp.get("rollupDataPoints", []):
            n = first_number(p.get("steps"))
            if n is not None:
                out[civil_date(p["civilStartTime"]["date"])] = int(n)
        chunk_start = chunk_end
    return out


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def gh_sleep(token, start, end, tz):
    """Main sleep of each night, keyed by the date you woke up. Also returns the sleep
    intervals (in memory only) so the nightly low HR can be taken from them."""
    utc = lambda d: dt.datetime.combine(d, dt.time(), tz).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    filt = f'sleep.interval.end_time >= "{utc(start)}" AND sleep.interval.end_time < "{utc(end)}"'
    nights, intervals = {}, {}
    points = gh_list(token, "sleep", filt, 25)
    # Field names only (no values), to spot a sleep score if Google ever adds one
    log("  sleep fields: " + ", ".join(sorted({k for s in points for k in s}))
        + " | summary: " + ", ".join(sorted({k for s in points for k in s.get("summary", {})})))
    for s in points:
        meta = s.get("metadata", {})
        if meta.get("nap") or meta.get("mainSleep") is False:
            continue
        sm = s.get("summary", {})
        asleep = int(sm.get("minutesAsleep", 0))
        if not asleep:
            continue
        stages = {x["type"]: int(x.get("minutes", 0)) for x in sm.get("stagesSummary", [])}
        begin, finish = parse_ts(s["interval"]["startTime"]), parse_ts(s["interval"]["endTime"])
        date = finish.astimezone(tz).date().isoformat()
        if date in nights and nights[date]["sleepHrs"] * 60 >= asleep:
            continue  # keep the longest if there are several
        nights[date] = {
            "sleepHrs": round(asleep / 60, 1),
            "deepMin": stages.get("DEEP"), "remMin": stages.get("REM"),
            "lightMin": stages.get("LIGHT"), "awakeMin": int(sm.get("minutesAwake", 0)),
            "sleepScore": estimate_sleep_score(asleep, stages, int(sm.get("minutesAwake", 0))),
        }
        intervals[date] = (begin, finish)
    return nights, intervals


def estimate_sleep_score(asleep, stages, awake):
    """Google Health doesn't expose the Fitbit app's sleep score, so approximate it with
    the same three parts: duration (50), deep+REM share (25) and restfulness (25)."""
    duration = min(asleep / 480, 1) * 50
    deep_rem = (stages.get("DEEP", 0) + stages.get("REM", 0)) / asleep if asleep else 0
    quality = min(deep_rem / 0.4, 1) * 25
    restful = max(0, 1 - awake / max(asleep + awake, 1) / 0.25) * 25
    return round(duration + quality + restful)


def gh_heart_rate(token, start, end, tz, sleep_intervals, smoothing, low_from):
    """Daily lowest and highest HR from intraday samples, after a rolling average so a
    single bad sample doesn't become the day's extreme."""
    t0 = dt.datetime.combine(start, dt.time(), tz).astimezone(dt.timezone.utc)
    t1 = dt.datetime.combine(end, dt.time(), tz).astimezone(dt.timezone.utc)
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    filt = (f'heart_rate.sample_time.physical_time >= "{iso(t0)}" AND '
            f'heart_rate.sample_time.physical_time < "{iso(t1)}"')
    minutes = {}  # minute (epoch // 60) -> [bpm, ...]
    for p in gh_list(token, "heart-rate", filt, 10000):
        m = int(parse_ts(p["sampleTime"]["physicalTime"]).timestamp() // 60)
        minutes.setdefault(m, []).append(int(p["beatsPerMinute"]))
    per_min = {m: sum(v) / len(v) for m, v in minutes.items()}

    smoothed = {}
    need = max(1, (smoothing + 1) // 2)  # at least half the window must have data
    for m in per_min:
        window = [per_min[k] for k in range(m - smoothing + 1, m + 1) if k in per_min]
        if len(window) >= need:
            smoothed[m] = sum(window) / len(window)

    out = {}
    for m, bpm in smoothed.items():
        date = dt.datetime.fromtimestamp(m * 60, tz).date().isoformat()
        day = out.setdefault(date, {})
        day["maxHr"] = max(day.get("maxHr", 0), round(bpm))
        if low_from == "all_day":
            day["minHr"] = min(day.get("minHr", 999), round(bpm))
    if low_from == "sleep":
        for date, (begin, finish) in sleep_intervals.items():
            lo, hi = int(begin.timestamp() // 60), int(finish.timestamp() // 60)
            vals = [v for m, v in smoothed.items() if lo <= m <= hi]
            if vals:
                out.setdefault(date, {})["minHr"] = round(min(vals))
    return out


def fetch_google(cfg, tz, start, end, intraday_start):
    log("Google Health (Fitbit)")
    token = google_token()
    measures = set(cfg["sources"]["fitbit"].get("measures", []))
    hr_cfg = cfg.get("heart_rate", {})
    results = []
    num = lambda f: (lambda p: float(p[f]) if p.get(f) is not None else None)

    def add(field, data):
        results.append({d: {field: v} for d, v in data.items()})

    if "resting_hr" in measures:
        add("restingHr", attempt("resting HR", gh_daily, token, "daily-resting-heart-rate", start, end,
                                 lambda p: int(p["beatsPerMinute"]) if p.get("beatsPerMinute") else None))
    if "hrv" in measures:
        add("hrv", attempt("HRV", gh_daily, token, "daily-heart-rate-variability", start, end,
                           lambda p: round(p["averageHeartRateVariabilityMilliseconds"])
                           if p.get("averageHeartRateVariabilityMilliseconds") else None))
    if "spo2" in measures:
        add("spo2", attempt("SpO2", gh_daily, token, "daily-oxygen-saturation", start, end,
                            lambda p: round(p["averagePercentage"], 1) if p.get("averagePercentage") else None))
    if "breathing_rate" in measures:
        add("breathingRate", attempt("breathing rate", gh_daily, token, "daily-respiratory-rate", start, end,
                                     lambda p: round(p["breathsPerMinute"], 1) if p.get("breathsPerMinute") else None))
    if "vo2max" in measures:
        add("vo2max", attempt("VO2 max", gh_daily, token, "daily-vo2-max", start, end,
                              lambda p: round(num("vo2Max")(p), 1) if p.get("vo2Max") else None))
    if "steps" in measures:
        add("steps", attempt("steps", gh_steps, token, start, end))

    intervals = {}
    if "sleep" in measures:
        try:
            nights, intervals = gh_sleep(token, start, end, tz)
            results.append(nights)
            log(f"  sleep: {len(nights)} night(s)")
        except Exception as e:  # noqa: BLE001
            errors.append(f"sleep: {e}")
            log(f"  sleep: FAILED {e}")
    if "intraday_hr" in measures:
        sleep_low = {d: iv for d, iv in intervals.items() if d >= intraday_start.isoformat()}
        results.append(attempt("intraday HR", gh_heart_rate, token, intraday_start, end, tz, sleep_low,
                               hr_cfg.get("smoothing_minutes", 5), hr_cfg.get("low_from", "sleep")))
    return results


# ---------------------------------------------------------------- merge + save

def load(repo):
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        # A template copy or fork inherits the original owner's data; start clean
        if repo and data.get("repo") and data["repo"] != repo:
            log(f"data/health.json belongs to {data['repo']}, not {repo}: starting fresh")
            return {}
        return {d["date"]: d for d in data.get("days", [])}
    return {}


def save(days, repo):
    DATA_FILE.parent.mkdir(exist_ok=True)
    rows = [days[k] for k in sorted(days)]
    # one day per line, so git diffs (and anyone reading the file) see whole days
    lines = ",\n".join("    " + json.dumps(r, separators=(",", ":")) for r in rows)
    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    DATA_FILE.write_text(f'{{\n  "repo": {json.dumps(repo)},\n  "updated": "{updated}",\n'
                         f'  "days": [\n{lines}\n  ]\n}}\n', encoding="utf-8")


def merge(days, updates, height_cm):
    for date, fields in updates.items():
        days.setdefault(date, {"date": date}).update(fields)
    for d in days.values():
        if d.get("weightKg") and height_cm:
            d["bmi"] = round(d["weightKg"] / (height_cm / 100) ** 2, 1)


def main():
    if not os.environ.get("TOKENS_DIR"):
        sys.exit("TOKENS_DIR is not set. Withings refresh tokens are single-use, so running this without\n"
                 "saving the new one would break the scheduled workflow. Let the Action run it instead.")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg["profile"].get("timezone", "UTC"))
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    days = load(repo)

    today = dt.datetime.now(tz).date()
    end = today + dt.timedelta(days=1)
    fetch_cfg = cfg.get("fetch", {})
    if days:
        start = today - dt.timedelta(days=fetch_cfg.get("lookback_days", 7))
        intraday_start = start
    else:
        start = today - dt.timedelta(days=fetch_cfg.get("backfill_days", 365))
        intraday_start = today - dt.timedelta(days=INTRADAY_BACKFILL_DAYS)
    log(f"Fetching {start} to {today} ({'update' if days else 'first run'})")

    sources = cfg.get("sources", {})
    height = cfg["profile"].get("height_cm")
    if sources.get("withings", {}).get("enabled"):
        try:
            span = (dt.datetime.combine(start, dt.time(), tz), dt.datetime.combine(end, dt.time(), tz))
            for part in fetch_withings(cfg, tz, *span):
                merge(days, part, height)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Withings: {e}")
            log(f"  Withings FAILED: {e}")
    if sources.get("fitbit", {}).get("enabled"):
        try:
            for part in fetch_google(cfg, tz, start, end, intraday_start):
                merge(days, part, height)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Google Health: {e}")
            log(f"  Google Health FAILED: {e}")

    save(days, repo)
    log(f"Saved {len(days)} day(s) to {DATA_FILE.relative_to(ROOT)}")
    if errors:
        log("\nProblems:\n  " + "\n  ".join(errors))
        sys.exit(1)  # data that did arrive is still saved and committed


if __name__ == "__main__":
    main()
