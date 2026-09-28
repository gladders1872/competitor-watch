"""Builds clearly labelled SAMPLE data for previewing the dashboard.
The real scanner overwrites docs/data/five-sigma.json on its first run."""
import json, random, datetime as dt, re
from pathlib import Path
import yaml
import sys
sys.path.insert(0, str(Path(__file__).parent))
from scan import classify, match_keywords, text_blob, section_of, set_kw

random.seed(7)
ROOT = Path(__file__).resolve().parent.parent
cfg = yaml.safe_load((ROOT / "config/clients.yaml").read_text())
client = cfg["clients"][0]
rules, groups = cfg["asset_types"], client["keywords"]
today = dt.date(2026, 9, 28)

topics = [
    ("blog", "How agentic AI is changing claims handling"), ("blog", "5 ways to cut claims cycle time"),
    ("blog", "Subrogation recovery: where carriers leave money on the table"), ("blog", "What adjusters want from AI"),
    ("blog", "FNOL automation checklist for 2027"), ("blog", "Fraud signals every SIU team should track"),
    ("blog", "Reducing litigation rates in auto claims"), ("blog", "Generative AI in insurance: a practical guide"),
    ("blog", "Claims leakage explained"), ("blog", "Customer experience in property claims"),
    ("webinars", "Webinar: AI agents for the claims desk"), ("webinars", "Webinar: Modernizing core claims without a rip and replace"),
    ("webinars", "On-demand: Fraud detection with machine learning"), ("resources/whitepapers", "Whitepaper: The state of claims automation 2026"),
    ("resources/ebooks", "eBook: The adjuster productivity playbook"), ("resources/reports", "Benchmark report: Claims cycle times by line"),
    ("case-studies", "Case study: Regional carrier cuts FNOL time by 60%"), ("case-studies", "Customer story: MGA scales claims with no new hires"),
    ("news", "Announces partnership with Guidewire"), ("news", "Raises Series C to expand AI agent platform"),
    ("events", "Meet us at ITC Vegas"), ("podcast", "Episode 42: The future of the claims adjuster"),
    ("platform", "Claims management platform"), ("solutions", "Straight through processing for auto claims"),
    ("glossary", "What is first notice of loss"), ("glossary", "What is subrogation"),
]
weights = {
    "assured.com": 3, "federato.ai": 4, "himarley.com": 5, "snapsheetclaims.com": 4, "tractable.ai": 3,
    "bevaya.ai": 2, "sprout.ai": 4, "shift-technology.com": 6, "agentech.com": 3, "claraanalytics.com": 4,
    "charlee.ai": 2, "docosoft.com": 2, "wilbur.io": 1, "omnius.com": 1, "cccis.com": 7,
}
sizes = {d: random.randint(90, 900) for d in weights}
sizes["cccis.com"] = 4200
events, comps = [], []
for d, w in weights.items():
    for _ in range(w * 7):
        folder, title = random.choice(topics)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower().split(": ")[-1]).strip("-")
        url = f"https://www.{d}/{folder}/{slug}/"
        date = (today - dt.timedelta(days=int(random.triangular(0, 89, 5)))).isoformat()
        kind = random.choices(["added", "changed", "removed"], [6, 3, 1])[0]
        meta = {"title": title, "h1": title, "h2": []}
        ev = {"date": date, "competitor": d, "kind": kind, "url": url, "path": f"/{folder}/{slug}/",
              "section": section_of(url), "type": classify(url, title, rules), "lastmod": date,
              "title": title, "h1": title, "description": "", "status": 200 if kind != "removed" else None,
              "canonical": url, "noindex": False}
        hits = match_keywords(text_blob(url, meta), groups)
        if kind == "added": set_kw(ev, added=hits)
        elif kind == "removed": set_kw(ev, removed=hits)
        else:
            if hits and random.random() < 0.4: set_kw(ev, added=hits)
            else: set_kw(ev, present=hits)
            if random.random() < 0.3:
                ev["diffs"] = {"title": [title.replace("AI", "automation"), title]}
        events.append(ev)
    size = sizes[d]
    inv = {}
    for t, share in [("Blog", .35), ("Product / Solution", .15), ("Whitepaper / eBook", .07), ("Webinar", .05),
                     ("Case study", .06), ("News / Press", .1), ("Event", .03), ("Company page", .06),
                     ("Glossary / Knowledge", .04), ("Podcast / Video", .03), ("Other", .06)]:
        inv[t] = max(0, int(size * share * random.uniform(.4, 1.6)))
    tracked = sum(inv.values())
    comps.append({"domain": d, "name": d, "last_scan": today.isoformat(), "urls_in_sitemap": tracked + int(tracked * .18),
                  "excluded": int(tracked * .18), "tracked": tracked, "status": "ok", "inventory": inv, "errors": []})
comps[-2]["status"] = "error"; comps[-2]["message"] = "Could not read the sitemap. https://www.omnius.com/sitemap.xml returned 403"
events.sort(key=lambda e: (e["date"], e["competitor"]), reverse=True)
data = {
    "sample": True,
    "client": {"id": "five-sigma", "name": "Five Sigma", "site": "fivesigmalabs.com"},
    "generated": today.isoformat() + "T06:12:00+00:00", "scan_date": today.isoformat(),
    "keywords": groups,
    "exclusions": {"global": cfg["global_exclude"], "client": [], "competitors": {}},
    "asset_types": [r["name"] for r in rules] + ["Other"],
    "brief": {"date": today.isoformat(), "text": "Sample brief. Once your API key is added, a short weekly summary written from the real scan appears here: the moves that matter most, which content types competitors are investing in, and anything touching your keyword groups."},
    "competitors": comps, "events": events,
}
out = ROOT / "docs/data"
out.mkdir(parents=True, exist_ok=True)
(out / "five-sigma.json").write_text(json.dumps(data, separators=(",", ":")))
(out / "clients.json").write_text(json.dumps([{"id": "five-sigma", "name": "Five Sigma"}], indent=2))
print(len(events), "sample events")
