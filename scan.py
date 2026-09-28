#!/usr/bin/env python3
"""Competitor Watch scanner.

Reads config/clients.yaml, scans every competitor's XML sitemap, compares it
with the previous scan and records pages that were added, removed or changed.
For new and changed pages it reads the title, meta description, H1, H2s,
canonical and HTTP status, sorts each page into an asset type and checks it
against the client's keyword groups.

Output:
  state/<client>/<domain>.json   what we saw last time (used for diffing)
  docs/data/clients.json         list of clients for the dashboard
  docs/data/<client>.json        everything the dashboard shows
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import gzip
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
import yaml
from bs4 import BeautifulSoup
from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 CompetitorWatch/1.0"
)
TIMEOUT = 25
MAX_SITEMAPS = 300
MAX_URLS = 60000
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "en"})


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- helpers

def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def base_domain(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, allowed: set[str]) -> bool:
    h = base_domain(host_of(url))
    return any(h == a or h.endswith("." + a) for a in allowed)


def normalise(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def path_of(url: str) -> str:
    return urlsplit(url).path or "/"


def section_of(url: str) -> str:
    segs = [s for s in path_of(url).split("/") if s]
    return "/" + segs[0] + "/" if segs else "/ (home)"


def compile_patterns(patterns: list[str]):
    out = []
    for p in patterns or []:
        p = str(p).strip()
        if not p:
            continue
        if p.startswith("regex:"):
            out.append(("re", re.compile(p[6:], re.I)))
        elif "*" in p or "?" in p:
            out.append(("glob", p.lower()))
        else:
            out.append(("prefix", p.lower()))
    return out


def excluded(url: str, compiled) -> bool:
    path = path_of(url).lower()
    for kind, pat in compiled:
        if kind == "re" and pat.search(path):
            return True
        if kind == "glob" and fnmatch.fnmatch(path, pat):
            return True
        if kind == "prefix" and path.startswith(pat):
            return True
    return False


def get(url: str, **kw):
    return SESSION.get(url, timeout=TIMEOUT, allow_redirects=True, **kw)


# ---------------------------------------------------------------- sitemaps

def discover_sitemaps(domain: str) -> list[str]:
    found = []
    for scheme_host in (f"https://{domain}", f"https://www.{domain}"):
        try:
            r = get(scheme_host + "/robots.txt")
            if r.ok:
                for line in r.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        found.append(line.split(":", 1)[1].strip())
        except requests.RequestException:
            pass
        if found:
            return list(dict.fromkeys(found))
    for scheme_host in (f"https://{domain}", f"https://www.{domain}"):
        for p in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap-index.xml"):
            try:
                r = get(scheme_host + p)
                if r.ok and b"<" in r.content[:500] and (b"urlset" in r.content[:3000] or b"sitemapindex" in r.content[:3000]):
                    return [r.url]
            except requests.RequestException:
                pass
    return []


def parse_sitemap_bytes(data: bytes):
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
    root = etree.fromstring(data, parser=parser)
    if root is None:
        return "none", []
    tag = etree.QName(root).localname.lower()
    items = []
    for node in root:
        if not isinstance(node.tag, str):
            continue
        loc, lastmod = None, ""
        for child in node:
            if not isinstance(child.tag, str):
                continue
            name = etree.QName(child).localname.lower()
            if name == "loc" and child.text:
                loc = child.text.strip()
            elif name == "lastmod" and child.text:
                lastmod = child.text.strip()
        if loc:
            items.append((loc, lastmod))
    return tag, items


def crawl_sitemaps(start: list[str], allowed: set[str]):
    """Return ({url: lastmod}, [sitemap urls read], [errors])."""
    urls: dict[str, str] = {}
    queue = list(start)
    seen, read, errors = set(), [], []
    while queue and len(seen) < MAX_SITEMAPS and len(urls) < MAX_URLS:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            r = get(sm)
            if not r.ok:
                errors.append(f"{sm} returned {r.status_code}")
                continue
            tag, items = parse_sitemap_bytes(r.content)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{sm}: {type(e).__name__}")
            continue
        read.append(sm)
        if tag == "sitemapindex":
            queue.extend(loc for loc, _ in items)
        else:
            for loc, lastmod in items:
                if same_site(loc, allowed):
                    urls[normalise(loc)] = lastmod[:10] if lastmod else ""
    return urls, read, errors


# ---------------------------------------------------------------- pages

def read_page(url: str) -> dict:
    meta = {"status": None}
    try:
        r = get(url)
        meta["status"] = r.status_code
        if r.url.rstrip("/") != url.rstrip("/"):
            meta["redirect"] = r.url
        ctype = r.headers.get("content-type", "")
        if not r.ok or "html" not in ctype:
            return meta
        soup = BeautifulSoup(r.text, "lxml")
        t = soup.find("title")
        meta["title"] = clean(t.get_text()) if t else ""
        d = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        meta["description"] = clean(d.get("content", "")) if d else ""
        h1 = soup.find("h1")
        meta["h1"] = clean(h1.get_text(" ")) if h1 else ""
        meta["h2"] = [clean(h.get_text(" ")) for h in soup.find_all("h2")[:15] if clean(h.get_text(" "))]
        c = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
        meta["canonical"] = c.get("href", "") if c else ""
        rb = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
        if rb and "noindex" in rb.get("content", "").lower():
            meta["noindex"] = True
    except requests.RequestException as e:
        meta["error"] = type(e).__name__
    return meta


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()[:300]


# ---------------------------------------------------------------- classify

def classify(url: str, title: str, rules) -> str:
    path = path_of(url).lower()
    title = (title or "").lower()
    for rule in rules:
        if any(p.lower() in path for p in rule.get("paths", []) or []):
            return rule["name"]
    for rule in rules:
        if title and any(w.lower() in title for w in rule.get("words", []) or []):
            return rule["name"]
    if path in ("", "/"):
        return "Company page"
    return "Other"


def text_blob(url: str, meta: dict | None) -> str:
    slug = re.sub(r"[-_/.+]+", " ", path_of(url).lower())
    parts = [slug]
    if meta:
        parts += [meta.get("title", ""), meta.get("h1", "")] + list(meta.get("h2", []) or [])
    return " " + " ".join(p.lower() for p in parts if p) + " "


def match_keywords(blob: str, groups) -> dict[str, list[str]]:
    hits = {}
    for g in groups:
        found = []
        for term in g.get("terms", []) or []:
            t = str(term).lower().strip()
            if t and re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?:s|es)?(?![a-z0-9])", blob):
                found.append(t)
        if found:
            hits[g["group"]] = found
    return hits


def set_kw(ev, added=None, removed=None, present=None):
    """Attach keyword info to an event.
    kw_added / kw_removed: groups whose terms appeared or disappeared.
    kw_present: groups mentioned on a changed page (no appear/disappear).
    keywords: every group involved, used for filtering in the dashboard."""
    groups = set()
    if added:
        ev["kw_added"] = added
        groups |= set(added)
    if removed:
        ev["kw_removed"] = removed
        groups |= set(removed)
    if present and not groups:
        ev["kw_present"] = present
        groups |= set(present)
    if groups:
        ev["keywords"] = sorted(groups)


# ---------------------------------------------------------------- scan one competitor

def scan_competitor(client, comp, cfg, today: str):
    settings = cfg.get("settings", {})
    domain = comp["domain"].lower().strip()
    name = comp.get("name") or domain
    state_path = ROOT / "state" / client["id"] / f"{domain}.json"
    prev = json.loads(state_path.read_text()) if state_path.exists() else None

    patterns = compile_patterns(
        (cfg.get("global_exclude") or []) + (client.get("exclude") or []) + (comp.get("exclude") or [])
    )
    rules = cfg.get("asset_types") or []
    groups = client.get("keywords") or []

    sitemaps = comp.get("sitemaps") or discover_sitemaps(domain)
    allowed = {base_domain(domain)} | {base_domain(host_of(s)) for s in sitemaps}
    raw, read, errors = crawl_sitemaps(sitemaps, allowed) if sitemaps else ({}, [], ["No sitemap found"])
    current = {u: lm for u, lm in raw.items() if not excluded(u, patterns)}
    excluded_count = len(raw) - len(current)
    log(f"  {domain}: {len(raw)} urls in sitemap, {excluded_count} excluded, {len(current)} tracked")

    summary = {
        "domain": domain,
        "name": name,
        "last_scan": today,
        "urls_in_sitemap": len(raw),
        "excluded": excluded_count,
        "tracked": len(current),
        "sitemaps": read[:50],
        "errors": errors[:10],
        "status": "ok",
    }

    if not current:
        summary["status"] = "error"
        summary["message"] = "Could not read the sitemap. " + (errors[0] if errors else "")
        if prev:
            summary["tracked"] = len(prev.get("urls", {}))
        return summary, [], prev

    prev_urls = (prev or {}).get("urls", {})
    # Safety net: a sudden large drop usually means a failed or partial fetch,
    # not a real site change. Do not report mass removals in that case.
    if prev_urls and len(current) < 0.5 * len(prev_urls) and len(prev_urls) > 20:
        summary["status"] = "warning"
        summary["message"] = (
            f"Sitemap shrank from {len(prev_urls)} to {len(current)} tracked pages. "
            "Skipped this scan to avoid false removals. It will retry tomorrow."
        )
        return summary, [], prev

    events = []
    new_state_urls: dict[str, dict] = {}
    for u, lm in current.items():
        entry = {"lastmod": lm}
        old = prev_urls.get(u)
        if old and old.get("meta"):
            entry["meta"] = old["meta"]
        entry["type"] = classify(u, (entry.get("meta") or {}).get("title", ""), rules)
        new_state_urls[u] = entry

    if prev is None:
        summary["baseline"] = True
        summary["message"] = "First scan. Saved as the starting point, changes appear from the next scan."
    else:
        added = [u for u in current if u not in prev_urls]
        removed = [u for u in prev_urls if u not in current]
        changed = [
            u for u in current
            if u in prev_urls and current[u] and prev_urls[u].get("lastmod") and current[u] != prev_urls[u].get("lastmod")
        ]
        budget = int(settings.get("max_pages_to_read_per_competitor", 60))
        delay = float(settings.get("request_delay", 0.6))
        # Newest first so the budget goes to the freshest pages
        to_read = sorted(added, key=lambda u: current[u], reverse=True) + sorted(changed, key=lambda u: current[u], reverse=True)
        metas = {}
        for u in to_read[:budget]:
            metas[u] = read_page(u)
            time.sleep(delay)

        def base_event(u, kind, meta, lastmod):
            m = meta or {}
            t = classify(u, m.get("title", ""), rules)
            return {
                "date": today,
                "competitor": domain,
                "kind": kind,
                "url": u,
                "path": path_of(u),
                "section": section_of(u),
                "type": t,
                "lastmod": lastmod,
                "title": m.get("title", ""),
                "h1": m.get("h1", ""),
                "description": m.get("description", ""),
                "status": m.get("status"),
                "canonical": m.get("canonical", ""),
                "noindex": m.get("noindex", False),
            }

        for u in added:
            m = metas.get(u)
            if m:
                new_state_urls[u]["meta"] = slim(m)
                new_state_urls[u]["type"] = classify(u, m.get("title", ""), rules)
            ev = base_event(u, "added", m, current[u])
            set_kw(ev, added=match_keywords(text_blob(u, m), groups))
            events.append(ev)

        for u in removed:
            m = prev_urls[u].get("meta")
            ev = base_event(u, "removed", m, prev_urls[u].get("lastmod", ""))
            set_kw(ev, removed=match_keywords(text_blob(u, m), groups))
            events.append(ev)

        for u in changed:
            new_m = metas.get(u)
            old_m = prev_urls[u].get("meta")
            if new_m:
                new_state_urls[u]["meta"] = slim(new_m)
                new_state_urls[u]["type"] = classify(u, new_m.get("title", ""), rules)
            ev = base_event(u, "changed", new_m or old_m, current[u])
            ev["previous_lastmod"] = prev_urls[u].get("lastmod", "")
            diffs = {}
            if new_m and old_m:
                for f in ("title", "h1", "description", "canonical"):
                    if (old_m.get(f) or "") != (new_m.get(f) or ""):
                        diffs[f] = [old_m.get(f) or "", new_m.get(f) or ""]
                if old_m.get("status") != new_m.get("status"):
                    diffs["status"] = [old_m.get("status"), new_m.get("status")]
            if diffs:
                ev["diffs"] = diffs
            now_hits = match_keywords(text_blob(u, new_m or old_m), groups)
            if new_m and old_m:
                old_hits = match_keywords(text_blob(u, old_m), groups)
                gained = {g: sorted(set(t) - set(old_hits.get(g, []))) for g, t in now_hits.items()}
                lost = {g: sorted(set(t) - set(now_hits.get(g, []))) for g, t in old_hits.items()}
                set_kw(ev, added={g: t for g, t in gained.items() if t},
                       removed={g: t for g, t in lost.items() if t}, present=now_hits)
            else:
                set_kw(ev, present=now_hits)
            events.append(ev)

        summary["added"], summary["removed"], summary["changed"] = len(added), len(removed), len(changed)
        summary["pages_read"] = len(metas)

    new_state = {"domain": domain, "last_scan": today, "urls": new_state_urls}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(new_state, separators=(",", ":")))

    # Current content mix across the whole tracked site
    mix: dict[str, int] = {}
    for e in new_state_urls.values():
        mix[e["type"]] = mix.get(e["type"], 0) + 1
    summary["inventory"] = mix
    return summary, events, new_state


def slim(m: dict) -> dict:
    return {k: m[k] for k in ("title", "h1", "h2", "description", "canonical", "status") if k in m}


# ---------------------------------------------------------------- AI brief

def ai_brief(client, events, cfg, today):
    key = os.environ.get("ANTHROPIC_API_KEY")
    settings = cfg.get("settings", {})
    if not key or not settings.get("ai_brief", True):
        return None
    since = (dt.date.fromisoformat(today) - dt.timedelta(days=7)).isoformat()
    recent = [e for e in events if e["date"] >= since]
    if not recent:
        return {"date": today, "text": "No competitor changes in the last 7 days."}
    lines = []
    for e in recent[:400]:
        kw = ", ".join(e.get("keywords", []))
        lines.append(
            f"{e['date']} | {e['competitor']} | {e['kind']} | {e['type']} | {e['path']} | "
            f"{e.get('title') or e.get('h1') or ''}" + (f" | keywords: {kw}" if kw else "")
        )
    prompt = (
        f"You are an SEO and content strategist at a B2B marketing agency. The client is {client['name']} "
        f"({client.get('site','')}). Below are last week's sitemap changes across their competitors. "
        "Write a short brief (max 180 words) for the client team: the 3 to 5 moves that matter most, "
        "what content types competitors are investing in, and anything touching the keyword groups. "
        "Plain sentences, no headings, no em dashes, no hype.\n\n" + "\n".join(lines)
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": settings.get("ai_model", "claude-sonnet-5"),
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=90,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        text = text.replace(" \u2014 ", ", ").replace("\u2014", ", ").strip()
        return {"date": today, "text": text}
    except Exception as e:  # noqa: BLE001
        log(f"  AI brief skipped: {e}")
        return None


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config" / "clients.yaml"))
    ap.add_argument("--client", help="Only scan this client id")
    ap.add_argument("--only", help="Only scan this competitor domain")
    ap.add_argument("--today", help="Override the scan date (YYYY-MM-DD), for testing")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    today = args.today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    keep_days = int(cfg.get("settings", {}).get("keep_days", 180))
    out_dir = ROOT / "docs" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    client_index = []
    for client in cfg["clients"]:
        if args.client and client["id"] != args.client:
            continue
        log(f"Client: {client['name']}")
        data_path = out_dir / f"{client['id']}.json"
        old = json.loads(data_path.read_text()) if data_path.exists() else {}
        if old.get("sample"):
            old = {}  # never mix preview data with real scans
        old_events = [e for e in old.get("events", []) if e.get("date") != today]
        old_summ = {c["domain"]: c for c in old.get("competitors", [])}

        comps, new_events = [], []
        for comp in client["competitors"]:
            dom = comp["domain"].lower().strip()
            if args.only and dom != args.only:
                if dom in old_summ:
                    comps.append(old_summ[dom])
                continue
            try:
                summ, evs, _ = scan_competitor(client, comp, cfg, today)
            except Exception as e:  # noqa: BLE001
                log(f"  {dom}: FAILED {e}")
                summ = dict(old_summ.get(dom, {"domain": dom, "name": comp.get("name") or dom}))
                summ.update({"status": "error", "message": f"Scan failed: {type(e).__name__}", "last_scan": today})
                evs = []
            comps.append(summ)
            new_events.extend(evs)

        cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=keep_days)).isoformat()
        events = [e for e in old_events + new_events if e["date"] >= cutoff]
        events.sort(key=lambda e: (e["date"], e["competitor"], e["kind"]), reverse=True)

        brief = ai_brief(client, events, cfg, today) or old.get("brief")
        data = {
            "client": {"id": client["id"], "name": client["name"], "site": client.get("site", "")},
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "scan_date": today,
            "keywords": client.get("keywords") or [],
            "exclusions": {
                "global": cfg.get("global_exclude") or [],
                "client": client.get("exclude") or [],
                "competitors": {c["domain"]: c.get("exclude") or [] for c in client["competitors"] if c.get("exclude")},
            },
            "asset_types": [r["name"] for r in cfg.get("asset_types") or []] + ["Other"],
            "brief": brief,
            "competitors": comps,
            "events": events,
        }
        data_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        client_index.append({"id": client["id"], "name": client["name"]})
        log(f"  wrote {data_path.relative_to(ROOT)} with {len(events)} events")

    idx_path = out_dir / "clients.json"
    existing = json.loads(idx_path.read_text()) if idx_path.exists() else []
    ids = {c["id"] for c in client_index}
    merged = client_index + [c for c in existing if c["id"] not in ids and any(x["id"] == c["id"] for x in cfg["clients"])]
    idx_path.write_text(json.dumps(merged, indent=2))


if __name__ == "__main__":
    sys.exit(main())
