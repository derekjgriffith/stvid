#!/usr/bin/env python3
"""Enrich satellites identified by STVID using CelesTrak and SatNOGS.

The tool recursively scans ``*_data.json`` products below the configured
``observations_path``, aggregates observations by NORAD catalog number, caches
the raw online responses, and writes CSV and JSON summaries in the observations
root.  Interest scores are deterministic heuristics and include an explanation
of every contribution.
"""

from __future__ import annotations

import argparse
import configparser
import csv
import json
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CELESTRAK_URL = "https://celestrak.org/satcat/records.php"
SATNOGS_URL = "https://db.satnogs.org/api/satellites/"
USER_AGENT = "STVID satellite-enrichment tool"

OUTPUT_COLUMNS = (
    "norad_id",
    "international_designator",
    "object_name",
    "object_type",
    "operational_status",
    "owner",
    "operator",
    "launch_date",
    "launch_site",
    "decay_date",
    "period_minutes",
    "inclination_deg",
    "apogee_km",
    "perigee_km",
    "radar_cross_section_m2",
    "purpose",
    "purpose_confidence",
    "associated_satellites",
    "interest_score",
    "interest_reasons",
    "first_observed",
    "last_observed",
    "observation_count",
    "measurement_count",
    "tle_catalog",
    "celestrak_url",
    "satnogs_url",
    "mission_url",
    "summary",
    "sources",
    "retrieved_at",
)

STATUS_LABELS = {
    "+": "operational",
    "P": "partially operational",
    "B": "backup/standby",
    "S": "spare",
    "X": "extended mission",
    "D": "decayed",
    "N": "non-operational",
    "-": "non-operational",
    "?": "unknown",
}

SATNOGS_STATUS_LABELS = {
    "alive": "operational",
    "dead": "non-operational",
    "future": "pre-launch",
    "re-entered": "decayed/re-entered",
}

PURPOSE_GROUPS = (
    ("ELINT/SIGINT", ("elint", "sigint", "signals intelligence", "electronic intelligence")),
    ("radar surveillance", ("radar reconnaissance", "radar surveillance", "sar", "synthetic aperture radar")),
    ("optical surveillance", ("optical reconnaissance", "imaging intelligence", "imint", "spy satellite")),
    ("missile warning", ("missile warning", "early warning")),
    ("military communications", ("military communication", "milsatcom")),
    ("space surveillance", ("space surveillance", "space situational awareness", "ssa")),
    ("Earth observation", ("earth observation", "remote sensing")),
    ("weather", ("weather", "meteorological")),
    ("navigation", ("navigation", "gps", "glonass", "galileo", "beidou")),
    ("communications", ("communications", "communication satellite", "telecom")),
    ("technology demonstration", ("technology demonstration", "tech demo")),
    ("scientific", ("science", "scientific", "astronomy", "research")),
)


@dataclass
class ObservedObject:
    norad_id: int
    first_observed: str = ""
    last_observed: str = ""
    files: set[str] = field(default_factory=set)
    measurement_count: int = 0
    tle_catalogs: set[str] = field(default_factory=set)
    catalog_names: set[str] = field(default_factory=set)


def read_observations_path(config_path: Path) -> Path:
    cfg = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    if not cfg.read(config_path):
        raise FileNotFoundError(f"Could not read configuration file: {config_path}")
    if not cfg.has_section("Setup"):
        raise ValueError("Configuration file has no [Setup] section")
    value = cfg.get("Setup", "observations_path").strip()
    if not value:
        raise ValueError("[Setup] observations_path is empty")
    root = Path(value).expanduser()
    if not root.is_absolute():
        root = config_path.resolve().parent / root
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Observations folder does not exist: {root}")
    return root


def collect_observations(root: Path) -> tuple[dict[int, ObservedObject], int]:
    objects: dict[int, ObservedObject] = {}
    failures = 0
    for path in sorted(root.rglob("*_data.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            start = str(document.get("start", ""))
            satellites = document.get("satellites", [])
            if not isinstance(satellites, list):
                raise ValueError("satellites is not a list")
            for satellite in satellites:
                # STVID-generated unknown IDs must never be sent to catalogs.
                if (
                    not isinstance(satellite, dict)
                    or satellite.get("catalogname") == "unid"
                    or satellite.get("tlefile") is None
                ):
                    continue
                norad_id = int(satellite["satno"])
                observed = objects.setdefault(norad_id, ObservedObject(norad_id))
                observed.files.add(str(path.relative_to(root)))
                observed.measurement_count += len(satellite.get("measurements", []))
                if satellite.get("tlefile"):
                    observed.tle_catalogs.add(str(satellite["tlefile"]))
                if satellite.get("catalogname"):
                    observed.catalog_names.add(str(satellite["catalogname"]))
                if start and (not observed.first_observed or start < observed.first_observed):
                    observed.first_observed = start
                if start and (not observed.last_observed or start > observed.last_observed):
                    observed.last_observed = start
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            failures += 1
            print(f"Failed to read {path}: {exc}", file=sys.stderr)
    return objects, failures


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def cache_path(cache_dir: Path, provider: str, norad_id: int) -> Path:
    return cache_dir / f"{provider}_{norad_id}.json"


def read_cache(path: Path) -> tuple[dict[str, Any] | None, datetime | None]:
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(wrapper["fetched_at"])
        data = wrapper["data"]
        return data, fetched
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None, None


def write_cache(path: Path, data: dict[str, Any]) -> datetime:
    fetched = utc_now()
    wrapper = {"fetched_at": fetched.isoformat(), "data": data}
    path.write_text(json.dumps(wrapper, indent=2, sort_keys=True), encoding="utf-8")
    return fetched


def get_json(url: str, params: dict[str, Any], timeout: float) -> Any:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_result(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return payload[0] if payload and isinstance(payload[0], dict) else {}
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            return results[0] if results and isinstance(results[0], dict) else {}
        return payload
    return {}


def fetch_provider(
    provider: str,
    norad_id: int,
    cache_dir: Path,
    max_age_days: float,
    refresh: bool,
    timeout: float,
) -> tuple[dict[str, Any], datetime | None, bool]:
    path = cache_path(cache_dir, provider, norad_id)
    cached, cached_at = read_cache(path)
    age_seconds = math.inf
    if cached_at is not None:
        age_seconds = (utc_now() - cached_at).total_seconds()
    if cached is not None and not refresh and age_seconds <= max_age_days * 86400:
        return cached, cached_at, True

    if provider == "celestrak":
        url = CELESTRAK_URL
        params = {"CATNR": norad_id, "FORMAT": "JSON"}
    else:
        url = SATNOGS_URL
        params = {"norad_cat_id": norad_id}
    try:
        payload = get_json(url, params, timeout)
        result = normalize_result(payload)
        fetched_at = write_cache(path, result)
        return result, fetched_at, False
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        if cached is not None:
            print(
                f"Warning: {provider} lookup for {norad_id} failed; using stale cache: {exc}",
                file=sys.stderr,
            )
            return cached, cached_at, True
        print(f"Warning: {provider} lookup for {norad_id} failed: {exc}", file=sys.stderr)
        return {}, None, False


def first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def as_float(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def combined_text(celestrak: dict[str, Any], satnogs: dict[str, Any]) -> str:
    values: list[str] = []
    for source in (celestrak, satnogs):
        for key, value in source.items():
            if isinstance(value, (str, int, float)):
                values.append(f"{key} {value}")
            elif isinstance(value, list):
                values.extend(str(item) for item in value if isinstance(item, str))
    return " ".join(values).casefold()


def infer_purpose(text: str) -> tuple[str, str]:
    for purpose, terms in PURPOSE_GROUPS:
        if any(term in text for term in terms):
            return purpose, "keyword heuristic"
    return "unknown", "unknown"


def interest_score(
    observed: ObservedObject,
    row: dict[str, Any],
    text: str,
    today: date | None = None,
) -> tuple[int, list[str]]:
    score = 10
    reasons = ["baseline +10"]
    today = today or date.today()

    catalogs = " ".join(observed.tle_catalogs | observed.catalog_names).casefold()
    classified_terms = ("classfd", "classified", "nrol", "national reconnaissance")
    military_terms = ("military", "defense", "defence", "reconnaissance", "surveillance")
    intelligence_terms = ("elint", "sigint", "signals intelligence", "electronic intelligence")
    sensor_terms = ("synthetic aperture radar", "radar surveillance", "optical reconnaissance", "imint")
    formation_terms = ("formation flying", "formation", "rendezvous", "inspector", "tandem", "cluster")

    if any(term in catalogs or term in text for term in classified_terms):
        score += 25
        reasons.append("classified/classfd indicator +25")
    if any(term in text for term in military_terms):
        score += 15
        reasons.append("military/reconnaissance indicator +15")
    if any(term in text for term in intelligence_terms):
        score += 20
        reasons.append("ELINT/SIGINT indicator +20")
    if any(term in text for term in sensor_terms):
        score += 15
        reasons.append("radar/optical surveillance indicator +15")
    if any(term in text for term in formation_terms):
        score += 12
        reasons.append("formation/RPO indicator +12")
    associations = row.get("associated_satellites")
    if associations:
        score += 5
        reasons.append("SatNOGS associated objects +5")

    status = str(row.get("operational_status", "")).casefold()
    if any(term in status for term in ("operational", "extended mission")) and not status.startswith("non-"):
        score += 10
        reasons.append("operational status +10")
    elif any(term in status for term in ("decayed", "non-operational", "defunct")):
        score -= 8
        reasons.append("non-operational/decayed -8")

    launched = parse_date(row.get("launch_date"))
    if launched is not None:
        age_years = (today - launched).days / 365.25
        if age_years <= 1:
            score += 20
            reasons.append(f"recent launch ({age_years:.1f} y) +20")
        elif age_years <= 3:
            score += 12
            reasons.append(f"recent launch ({age_years:.1f} y) +12")
        elif age_years <= 10:
            score += 5
            reasons.append(f"launch within 10 years ({age_years:.1f} y) +5")
        elif age_years >= 50:
            score += 20
            reasons.append(f"historic age ({age_years:.1f} y) +20")
        elif age_years >= 30:
            score += 12
            reasons.append(f"historic age ({age_years:.1f} y) +12")
        elif age_years >= 20:
            score += 6
            reasons.append(f"older payload ({age_years:.1f} y) +6")

    perigee = as_float(row.get("perigee_km"))
    if not row.get("decay_date") and perigee is not None:
        if perigee < 150:
            score += 25
            reasons.append(f"very low perigee ({perigee:g} km) +25")
        elif perigee < 200:
            score += 18
            reasons.append(f"low perigee ({perigee:g} km) +18")
        elif perigee < 300:
            score += 8
            reasons.append(f"low orbit ({perigee:g} km) +8")

    object_type = str(row.get("object_type", "")).casefold()
    if object_type in {"pay", "payload"}:
        score += 5
        reasons.append("payload +5")
    elif object_type in {"deb", "debris"}:
        score -= 5
        reasons.append("debris -5")

    return max(0, min(100, score)), reasons


def make_row(
    observed: ObservedObject,
    celestrak: dict[str, Any],
    satnogs: dict[str, Any],
    fetched_times: list[datetime],
) -> dict[str, Any]:
    norad_id = observed.norad_id
    status_code = str(first(celestrak, "OPS_STATUS_CODE"))
    satnogs_status = str(first(satnogs, "status", "status_name"))
    celestrak_status = STATUS_LABELS.get(status_code, status_code)
    status = celestrak_status or SATNOGS_STATUS_LABELS.get(
        satnogs_status.casefold(), satnogs_status
    )
    text = combined_text(celestrak, satnogs)
    purpose, purpose_confidence = infer_purpose(text)
    celestrak_url = f"{CELESTRAK_URL}?{urlencode({'CATNR': norad_id, 'FORMAT': 'JSON'})}"
    satnogs_url = str(first(satnogs, "url")) or f"{SATNOGS_URL}?{urlencode({'norad_cat_id': norad_id})}"
    mission_url = first(satnogs, "website", "website_url", "citation")
    object_name = first(celestrak, "OBJECT_NAME") or first(satnogs, "name")

    row: dict[str, Any] = {
        "norad_id": norad_id,
        "international_designator": first(celestrak, "OBJECT_ID") or first(satnogs, "international_designator"),
        "object_name": object_name,
        "object_type": first(celestrak, "OBJECT_TYPE") or first(satnogs, "type"),
        "operational_status": status,
        "owner": first(celestrak, "OWNER"),
        "operator": first(satnogs, "operator"),
        "launch_date": first(celestrak, "LAUNCH_DATE") or first(satnogs, "launched"),
        "launch_site": first(celestrak, "LAUNCH_SITE"),
        "decay_date": first(celestrak, "DECAY_DATE") or first(satnogs, "decayed"),
        "period_minutes": first(celestrak, "PERIOD"),
        "inclination_deg": first(celestrak, "INCLINATION"),
        "apogee_km": first(celestrak, "APOGEE"),
        "perigee_km": first(celestrak, "PERIGEE"),
        "radar_cross_section_m2": first(celestrak, "RCS"),
        "associated_satellites": first(satnogs, "associated_satellites"),
        "purpose": purpose,
        "purpose_confidence": purpose_confidence,
        "first_observed": observed.first_observed,
        "last_observed": observed.last_observed,
        "observation_count": len(observed.files),
        "measurement_count": observed.measurement_count,
        "tle_catalog": "; ".join(sorted(observed.tle_catalogs)),
        "celestrak_url": celestrak_url,
        "satnogs_url": satnogs_url,
        "mission_url": mission_url,
        "sources": "; ".join(
            source for source, record in (
                ("CelesTrak SATCAT", celestrak), ("SatNOGS DB", satnogs)
            ) if record
        ),
        "retrieved_at": max(fetched_times).isoformat() if fetched_times else "",
    }
    score, reasons = interest_score(observed, row, text)
    row["interest_score"] = score
    row["interest_reasons"] = "; ".join(reasons)
    facts = [f"{object_name or 'Unknown object'} (NORAD {norad_id})"]
    if row["object_type"]:
        facts.append(str(row["object_type"]))
    if row["operational_status"]:
        facts.append(str(row["operational_status"]))
    if row["purpose"] != "unknown":
        facts.append(str(row["purpose"]))
    row["summary"] = "; ".join(facts) + "."
    return row


def safe_output_stem(value: str) -> str:
    candidate = Path(value)
    if candidate.name != value:
        raise ValueError("-o/--output must be a base file name, not a path")
    if candidate.suffix.casefold() in {".csv", ".json"}:
        candidate = candidate.with_suffix("")
    if not candidate.name:
        raise ValueError("output base name is empty")
    return candidate.name


def write_outputs(root: Path, stem: str, rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    csv_path = root / f"{stem}.csv"
    json_path = root / f"{stem}.json"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return csv_path, json_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="configuration.ini", help="configuration file")
    parser.add_argument(
        "-o", "--output", default="observed_satellites", metavar="NAME",
        help="CSV/JSON output base name (default: observed_satellites)",
    )
    parser.add_argument(
        "--cache-days", type=float, default=7.0,
        help="reuse catalog responses for this many days (default: 7)",
    )
    parser.add_argument("--refresh", action="store_true", help="ignore fresh cached responses")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cache_days < 0 or args.timeout <= 0:
        print("Error: --cache-days must be nonnegative and --timeout positive", file=sys.stderr)
        return 2
    try:
        root = read_observations_path(Path(args.config))
        stem = safe_output_stem(args.output)
        objects, failures = collect_observations(root)
        cache_dir = root / ".satellite_catalog_cache"
        cache_dir.mkdir(exist_ok=True)
    except (configparser.Error, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    rows: list[dict[str, Any]] = []
    for index, observed in enumerate(objects.values(), start=1):
        print(f"[{index}/{len(objects)}] NORAD {observed.norad_id}")
        celestrak, ct_time, _ = fetch_provider(
            "celestrak", observed.norad_id, cache_dir,
            args.cache_days, args.refresh, args.timeout,
        )
        satnogs, sn_time, _ = fetch_provider(
            "satnogs", observed.norad_id, cache_dir,
            args.cache_days, args.refresh, args.timeout,
        )
        fetched_times = [value for value in (ct_time, sn_time) if value is not None]
        rows.append(make_row(observed, celestrak, satnogs, fetched_times))
        # Be courteous when the services are being queried repeatedly.
        if index < len(objects):
            time.sleep(0.2)

    rows.sort(key=lambda row: (-int(row["interest_score"]), int(row["norad_id"])))
    try:
        csv_path, json_path = write_outputs(root, stem, rows)
    except OSError as exc:
        print(f"Error writing output: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"Enriched {len(rows)} identified object(s); {failures} input file(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
