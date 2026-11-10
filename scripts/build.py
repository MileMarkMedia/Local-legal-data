#!/usr/bin/env python3
"""Build the public firm dataset and county fatal crash reports.

  python scripts/build.py firms     offline: dataset files and firm pages
  python scripts/build.py all       also geocodes offices and pulls NHTSA FARS data

Network steps use only free public U.S. government APIs and need no keys.
"""
import csv, datetime, json, re, sys, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "source"
GEO_CACHE = SRC / "geocode.json"
FARS_CACHE = SRC / "fars.json"
CRASH_AREAS = {"Personal Injury", "Car Accidents", "Truck Accidents"}
FIRST_YEAR = 2019
UA = {"User-Agent": "local-legal-data/1.0 (public data reports)"}
PUBLISHER = "MileMark Legal Marketing"
DISCLOSURE = f"Firms listed here are clients of {PUBLISHER}, which maintains this dataset."


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def get_json(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # network and parse errors are retried, then reported
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {last}")


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def esc(text):
    """Escape characters that Markdown or Liquid would treat as markup."""
    return re.sub(r"([\\`*_{}\[\]<>|])", r"\\\1", text or "")


# ---------------------------------------------------------------- geocoding
def geocode(firms):
    cache = load(GEO_CACHE, {})
    changed = False
    for f in firms:
        if f["country"] != "US" or f["slug"] in cache:
            continue
        street = re.sub(r"[,\s]+(suite|ste\.?|unit|floor|fl\.?|#|bldg\.?|building|room|rm\.?)\b.*$", "", f["street"], flags=re.I)
        street = re.sub(r",?\s+\d+(st|nd|rd|th)\s+floor.*$", "", street, flags=re.I)
        query = urllib.parse.urlencode({
            "address": f"{street}, {f['city']}, {f['state']} {f['postal_code']}",
            "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json"})
        try:
            data = get_json("https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress?" + query)
            matches = data["result"]["addressMatches"]
        except Exception as exc:
            print(f"::warning::Geocoder failed for {f['slug']}: {exc}")
            continue
        entry = {"matched": False}
        if matches:
            county = matches[0]["geographies"]["Counties"][0]
            state_code = matches[0]["addressComponents"].get("state")
            if state_code == f["state"]:
                entry = {"matched": True, "county": county["NAME"], "state_fips": county["STATE"],
                         "county_fips": county["COUNTY"], "geoid": county["GEOID"]}
        cache[f["slug"]] = entry
        changed = True
        time.sleep(0.4)
    if changed:
        dump(GEO_CACHE, cache)
    print("Geocoded", sum(1 for v in cache.values() if v.get("matched")), "of", len(cache), "checked offices")


# ---------------------------------------------------------------- FARS
def fars_rows(state_fips, county_fips, year):
    url = ("https://crashviewer.nhtsa.dot.gov/CrashAPI/crashes/GetCrashesByLocation?"
           + urllib.parse.urlencode({"fromCaseYear": year, "toCaseYear": year, "state": int(state_fips),
                                     "county": int(county_fips), "format": "json"}))
    data = get_json(url)
    if not isinstance(data, dict) or "Results" not in data:
        raise RuntimeError("Unexpected FARS response")
    rows = data["Results"]
    while rows and isinstance(rows[0], list):
        rows = [r for group in rows for r in group]
    out = []
    for r in rows:
        low = {k.lower(): v for k, v in r.items()}
        if int(low.get("caseyear", year)) != year:
            continue
        out.append({"fatals": int(low["fatals"]), "vehicles": int(low.get("totalvehicles") or 0),
                    "city": (low.get("cityname") or "").strip(), "road": (low.get("tway_id") or "").strip()})
    return out


def fetch_fars(firms, geo):
    cache = load(FARS_CACHE, {"counties": {}})
    counties = cache["counties"]
    wanted = {}
    for f in firms:
        g = geo.get(f["slug"]) or {}
        if g.get("matched") and CRASH_AREAS & set(f["practice_areas"]):
            wanted[g["geoid"]] = g
    last_year = datetime.date.today().year - 1
    known_latest = cache.get("latest_year") or 0
    printed = False
    for geoid, g in sorted(wanted.items()):
        entry = counties.setdefault(geoid, {"county": g["county"], "state_fips": g["state_fips"],
                                           "county_fips": g["county_fips"], "years": {}})
        for year in range(FIRST_YEAR, last_year + 1):
            key = str(year)
            # Older years are final. Refresh only the newest two published years and anything missing.
            if key in entry["years"] and year < known_latest - 1:
                continue
            try:
                rows = fars_rows(g["state_fips"], g["county_fips"], year)
            except Exception as exc:
                print(f"::warning::FARS request failed for {geoid} {year}: {exc}")
                continue
            if rows and not printed:
                print("Sample FARS row:", rows[0])
                printed = True
            entry["years"][key] = rows
            time.sleep(0.25)
    years_with_data = [int(y) for c in counties.values() for y, rows in c["years"].items() if rows]
    if years_with_data:
        cache["latest_year"] = max(years_with_data)
    cache["checked"] = datetime.date.today().isoformat()
    dump(FARS_CACHE, cache)
    print("FARS counties:", len(wanted), "latest year with data:", cache.get("latest_year"))


# ---------------------------------------------------------------- output
STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
          "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
          "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
          "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
          "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
          "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
          "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
          "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
          "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
          "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "ON": "Ontario"}


def county_summaries(firms, geo):
    cache = load(FARS_CACHE, {"counties": {}})
    latest = cache.get("latest_year")
    if not latest:
        return {}, None
    years = list(range(max(FIRST_YEAR, latest - 4), latest + 1))
    by_geoid = defaultdict(list)
    for f in firms:
        g = geo.get(f["slug"]) or {}
        if g.get("matched") and CRASH_AREAS & set(f["practice_areas"]):
            by_geoid[g["geoid"]].append(f)
    out = {}
    for geoid, local in by_geoid.items():
        c = cache["counties"].get(geoid)
        if not c or any(str(y) not in c["years"] for y in years):
            continue  # never publish a county with a missing year
        table, roads, cities = [], Counter(), Counter()
        for y in years:
            rows = c["years"][str(y)]
            table.append({"year": y, "crashes": len(rows), "deaths": sum(r["fatals"] for r in rows),
                          "multi": sum(1 for r in rows if r["vehicles"] > 1)})
            for r in rows:
                if r["road"]:
                    roads[r["road"]] += 1
                if r["city"] and not re.search(r"not applicable|unknown|not reported|other", r["city"], re.I):
                    cities[r["city"].title()] += 1
        if sum(t["crashes"] for t in table) == 0:
            continue
        state = local[0]["state"]
        out[geoid] = {"geoid": geoid, "county": c["county"], "state": state, "state_name": STATES[state],
                      "path": f"reports/fatal-crashes/{state.lower()}/{slugify(c['county'])}.md",
                      "years": table, "roads": roads.most_common(10), "cities": cities.most_common(8),
                      "firms": sorted(local, key=lambda f: f["name"].lower())}
    return out, cache.get("checked")


def firm_record(f, geo, reports):
    g = geo.get(f["slug"]) or {}
    rec = {k: f[k] for k in ("name", "website", "phone", "street", "city", "state", "postal_code", "country",
                             "practice_areas", "areas_served", "year_established", "attorneys", "offices",
                             "recognition", "profiles", "description")}
    rec["id"] = f["slug"]
    rec["county"] = g.get("county") if g.get("matched") else None
    rec["county_fips"] = g.get("geoid") if g.get("matched") else None
    return rec


def firm_page(f, rec, report):
    place = f"{f['city']}, {STATES.get(f['state'], f['state'])}"
    lines = [f"# {esc(f['name'])}", "", esc(f["description"]), ""]
    lines += ["## Practice areas", ""] + [f"- {esc(p)}" for p in f["practice_areas"]] + [""]
    lines += ["## Contact", "", f"- Address: {esc(f['street'])}, {esc(f['city'])}, {f['state']} {f['postal_code']}"]
    if rec["county"]:
        lines.append(f"- County: {esc(rec['county'])}")
    lines += [f"- Phone: {esc(f['phone'])}", f"- Website: [{esc(f['website'].split('//')[1].strip('/'))}]({f['website']})", ""]
    extra = [o for o in f["offices"] if o["address"] and slugify(o["address"])[:12] != slugify(f["street"])[:12]]
    if extra:
        lines += ["## Other offices", ""] + [
            f"- {esc(o['label'] or 'Office')}: {esc(o['address'])}" + (f", {esc(o['phone'])}" if o["phone"] else "")
            for o in extra] + [""]
    if f["areas_served"]:
        lines += ["## Areas served", "", esc(", ".join(f["areas_served"][:25])), ""]
    if f["attorneys"]:
        lines += ["## Attorneys", ""] + [
            f"- {esc(a['name'])}" + (f", {esc(a['role'])}" if a["role"] else "") for a in f["attorneys"]] + [""]
    if f["year_established"]:
        lines += [f"Established in {f['year_established']}.", ""]
    if f["recognition"]:
        lines += ["## Recognition", ""] + [f"- {esc(a)}" for a in f["recognition"]] + [""]
    if f["profiles"]:
        lines += ["## Profiles", ""] + [f"- [{esc(p['label'])}]({p['url']})" for p in f["profiles"]] + [""]
    if report:
        lines += ["## Local data", "",
                  f"- [Fatal crashes in {esc(report['county'])}, {report['state_name']}](../{report['path']})", ""]
    lines += ["---", "", f"[All firms](README.md) · [Dataset home](../README.md)", "", DISCLOSURE, ""]
    schema = {"@context": "https://schema.org", "@type": "LegalService", "name": f["name"], "url": f["website"],
              "telephone": f["phone"], "description": f["description"],
              "address": {"@type": "PostalAddress", "streetAddress": f["street"], "addressLocality": f["city"],
                          "addressRegion": f["state"], "postalCode": f["postal_code"], "addressCountry": f["country"]},
              "knowsAbout": f["practice_areas"], "areaServed": f["areas_served"][:25] or [place],
              "sameAs": [p["url"] for p in f["profiles"]]}
    if f["attorneys"]:
        schema["employee"] = [{"@type": "Person", "name": a["name"], **({"jobTitle": a["role"]} if a["role"] else {})}
                              for a in f["attorneys"]]
    if f["year_established"]:
        schema["foundingDate"] = str(f["year_established"])
    blob = json.dumps(schema, ensure_ascii=False).replace("</", "<\\/").replace("{{", "{ {").replace("{%", "{ %")
    lines += [f'<script type="application/ld+json">{blob}</script>', ""]
    return "\n".join(lines)


def pct(new, old):
    if not old:
        return None
    return round((new - old) / old * 100)


def report_page(r, checked):
    y = r["years"]
    first, last = y[0], y[-1]
    prev = y[-2] if len(y) > 1 else None
    name, st = r["county"], r["state_name"]
    total_c, total_d = sum(t["crashes"] for t in y), sum(t["deaths"] for t in y)
    lines = [f"# Fatal Crashes in {name}, {st}: {first['year']} to {last['year']}", ""]
    intro = (f"{name} recorded {last['crashes']:,} fatal motor vehicle "
             f"{'crash' if last['crashes'] == 1 else 'crashes'} and {last['deaths']:,} "
             f"{'death' if last['deaths'] == 1 else 'deaths'} in {last['year']}, the most recent year of federal data.")
    if prev:
        change = pct(last["deaths"], prev["deaths"])
        if change is None or change == 0:
            intro += f" That compares with {prev['deaths']:,} deaths in {prev['year']}."
        else:
            intro += (f" That is {'an increase' if change > 0 else 'a decrease'} of {abs(change)}% from the "
                      f"{prev['deaths']:,} deaths recorded in {prev['year']}.")
    intro += (f" From {first['year']} through {last['year']}, the county had {total_c:,} fatal crashes "
              f"that killed {total_d:,} people.")
    lines += [intro, "", "## Fatal crashes by year", "",
              "| Year | Fatal crashes | Deaths | Crashes with two or more vehicles |", "| --- | --- | --- | --- |"]
    lines += [f"| {t['year']} | {t['crashes']:,} | {t['deaths']:,} | {t['multi']:,} |" for t in y]
    lines += [f"| Total | {total_c:,} | {total_d:,} | {sum(t['multi'] for t in y):,} |", ""]
    if r["cities"]:
        lines += ["## Cities with the most fatal crashes", "",
                  f"Counts cover {first['year']} through {last['year']} and include only crashes recorded inside a city.", "",
                  "| City | Fatal crashes |", "| --- | --- |"]
        lines += [f"| {esc(c)} | {n:,} |" for c, n in r["cities"]] + [""]
    if r["roads"]:
        lines += ["## Roads with the most fatal crashes", "",
                  f"Road names appear as they were entered in the federal crash record for {first['year']} through {last['year']}.", "",
                  "| Road | Fatal crashes |", "| --- | --- |"]
        lines += [f"| {esc(road)} | {n:,} |" for road, n in r["roads"]] + [""]
    lines += ["## About this data", "",
              "The figures come from the Fatality Analysis Reporting System (FARS), the census of fatal traffic crashes "
              "kept by the National Highway Traffic Safety Administration. FARS counts a crash when it involves a motor "
              "vehicle on a public road and a person dies within 30 days. Crashes that caused injuries or property damage "
              "without a death are not included, so these numbers describe only the most serious crashes in the county.", "",
              f"Source: [NHTSA Crash API](https://crashviewer.nhtsa.dot.gov/CrashAPI). Data retrieved {checked}. "
              f"County totals for every county in this project are in "
              f"[fatal-crashes-by-county.csv](../../../data/fatal-crashes-by-county.csv).", ""]
    lines += [f"## Injury law firms in {name}", ""]
    for f in r["firms"]:
        lines.append(f"- [{esc(f['name'])}](../../../firms/{f['slug']}.md), {esc(f['city'])}: "
                     f"{esc(', '.join(p for p in f['practice_areas'] if p in CRASH_AREAS | {'Medical Malpractice', 'Product Liability', "Workers' Compensation"}))}")
    lines += ["", "---", "", "[All crash reports](../README.md) · [Dataset home](../../../README.md)", "", DISCLOSURE, ""]
    schema = {"@context": "https://schema.org", "@type": "Dataset",
              "name": f"Fatal crashes in {name}, {st}, {first['year']} to {last['year']}",
              "description": f"Yearly counts of fatal motor vehicle crashes and deaths in {name}, {st}, from NHTSA FARS.",
              "spatialCoverage": f"{name}, {st}", "temporalCoverage": f"{first['year']}/{last['year']}",
              "isBasedOn": "https://crashviewer.nhtsa.dot.gov/CrashAPI",
              "license": "https://creativecommons.org/publicdomain/zero/1.0/",
              "creator": {"@type": "Organization", "name": PUBLISHER}}
    lines += [f'<script type="application/ld+json">{json.dumps(schema)}</script>', ""]
    return "\n".join(lines)


def write_if_changed(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def build(firms, exported):
    geo = load(GEO_CACHE, {})
    reports, checked = county_summaries(firms, geo)
    report_for = {f["slug"]: r for r in reports.values() for f in r["firms"]}
    records = [firm_record(f, geo, reports) for f in firms]
    dump(ROOT / "data" / "firms.json", {"updated": exported, "count": len(records), "firms": records})
    with open(ROOT / "data" / "firms.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "website", "phone", "street", "city", "state", "postal_code", "county",
                    "practice_areas", "attorneys", "recognition", "year_established"])
        for r in records:
            w.writerow([r["name"], r["website"], r["phone"], r["street"], r["city"], r["state"], r["postal_code"],
                        r["county"] or "", "; ".join(r["practice_areas"]), "; ".join(a["name"] for a in r["attorneys"]),
                        "; ".join(r["recognition"]), r["year_established"] or ""])
    firm_dir = ROOT / "firms"
    keep = {"README.md"}
    for f, rec in zip(firms, records):
        write_if_changed(firm_dir / f"{f['slug']}.md", firm_page(f, rec, report_for.get(f["slug"])))
        keep.add(f"{f['slug']}.md")
    for old in firm_dir.glob("*.md"):
        if old.name not in keep:
            old.unlink()
    by_state = defaultdict(list)
    for f in firms:
        by_state[f["state"]].append(f)
    idx = ["# Law Firm Directory", "", f"{len(firms)} law firms in {len(by_state)} states and provinces. "
           "Each page lists the firm's practice areas, offices, attorneys and public profiles.", ""]
    for st in sorted(by_state, key=lambda s: STATES.get(s, s)):
        idx += [f"## {STATES.get(st, st)}", ""]
        idx += [f"- [{esc(f['name'])}]({f['slug']}.md), {esc(f['city'])}: {esc(', '.join(f['practice_areas'][:3]))}"
                for f in by_state[st]] + [""]
    idx += ["---", "", "[Dataset home](../README.md)", "", DISCLOSURE, ""]
    write_if_changed(firm_dir / "README.md", "\n".join(idx))

    rep_dir = ROOT / "reports" / "fatal-crashes"
    kept = set()
    for r in reports.values():
        write_if_changed(ROOT / r["path"], report_page(r, checked))
        kept.add((ROOT / r["path"]).resolve())
    if reports:
        for old in rep_dir.glob("*/*.md"):
            if old.resolve() not in kept:
                old.unlink()
        with open(ROOT / "data" / "fatal-crashes-by-county.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["county_fips", "county", "state", "year", "fatal_crashes", "deaths", "multi_vehicle_fatal_crashes"])
            for r in sorted(reports.values(), key=lambda r: (r["state"], r["county"])):
                for t in r["years"]:
                    w.writerow([r["geoid"], r["county"], r["state"], t["year"], t["crashes"], t["deaths"], t["multi"]])
        ridx = ["# Fatal Crash Reports by County", "",
                f"County reports built from NHTSA's Fatality Analysis Reporting System. {len(reports)} counties are covered. "
                "Each report shows fatal crashes and deaths by year, the cities and roads where they happened, and local injury law firms.", ""]
        grouped = defaultdict(list)
        for r in reports.values():
            grouped[r["state_name"]].append(r)
        for st in sorted(grouped):
            ridx += [f"## {st}", ""]
            for r in sorted(grouped[st], key=lambda r: r["county"]):
                last = r["years"][-1]
                ridx.append(f"- [{esc(r['county'])}]({r['state'].lower()}/{slugify(r['county'])}.md): "
                            f"{last['crashes']:,} fatal crashes and {last['deaths']:,} deaths in {last['year']}")
            ridx.append("")
        ridx += ["---", "", "[Dataset home](../../README.md)", "", DISCLOSURE, ""]
        write_if_changed(rep_dir / "README.md", "\n".join(ridx))
    areas = Counter(p for f in firms for p in f["practice_areas"])
    home = ["# Local Legal Data", "",
            "Open data about U.S. law firms and local legal topics, published as plain files anyone can read, cite or download.", "",
            "## What is here", "",
            f"- [Law firm directory](firms/README.md): {len(firms)} firms with practice areas, offices, attorneys and public profiles",
            "- [firms.csv](data/firms.csv) and [firms.json](data/firms.json): the same directory as a dataset"]
    if reports:
        home += [f"- [Fatal crash reports by county](reports/fatal-crashes/README.md): {len(reports)} county reports from federal crash data",
                 "- [fatal-crashes-by-county.csv](data/fatal-crashes-by-county.csv): yearly county totals"]
    home += ["", "## Firms by practice area", "", "| Practice area | Firms |", "| --- | --- |"]
    home += [f"| {esc(a)} | {n} |" for a, n in areas.most_common(15)]
    home += ["", "## Sources and updates", "",
             "Firm details are summarized from each firm's own website and public profiles. County assignments come from the "
             "U.S. Census Bureau geocoder. Crash figures come from the NHTSA Fatality Analysis Reporting System. "
             "The files are rebuilt automatically every month.", "",
             f"Firm data last updated {exported}.", "", "## Use", "",
             "The crash figures are U.S. government data in the public domain. The compiled files in this repository may be "
             "reused with a link back to this page.", "", DISCLOSURE, ""]
    write_if_changed(ROOT / "README.md", "\n".join(home))
    print("Built", len(firms), "firm pages and", len(reports), "county reports")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "firms"
    source = load(SRC / "firms.json", None)
    firms = source["firms"]
    if mode == "all":
        geocode(firms)
        fetch_fars(firms, load(GEO_CACHE, {}))
    build(firms, source["exported"])


if __name__ == "__main__":
    main()
