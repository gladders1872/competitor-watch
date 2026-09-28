# Competitor Watch

Daily tracking of competitor sitemaps for Wadi Digital clients. It shows which pages competitors add, change and remove, what kind of content they are publishing, and sends keyword alerts when something your client cares about appears or disappears.

It runs for free on GitHub: a GitHub Action does the scanning every morning, and GitHub Pages hosts the dashboard.

Set up for **Five Sigma** with 15 competitors.

## What is new compared with the original tool

1. **Excluded paths.** Sections that do not matter (careers, legal pages, foreign language versions, tag archives) are ignored at scan time, so they never show up as noise. You can also hide a path in the dashboard for a quick look without changing anything for anyone else.
2. **Content type tracking.** Every page is sorted into a type: blog, webinar, whitepaper or eBook, case study, news, event, podcast or video, glossary, product page and so on. The dashboard shows a grid of competitor by content type, either for pages added or updated recently, or for the whole site today.
3. **Keyword alerts.** Keyword groups (for example "Agentic AI" or "Subrogation and recovery") are checked against the URL, page title, H1 and H2 headings. You get an alert when a competitor publishes a matching page, removes one, or changes a page so a keyword appears or disappears.
4. **No limit on competitors.** The original allowed 5 per client.

## Setup, step by step (browser only, no terminal)

Everything below is done on github.com in your browser. Allow about 15 minutes.

### Step 1: Create the repository

1. Go to https://github.com/new
2. Repository name: `competitor-watch`
3. Choose **Public**. GitHub Pages is free for public repos. The dashboard carries a `noindex` tag so search engines skip it, but anyone with the link can see it.
4. Tick **Add a README file**, then click **Create repository**.

### Step 2: Upload the files

1. Unzip `competitor-watch.zip` on your computer.
2. In your new repository, click **Add file**, then **Upload files**.
3. Open the unzipped `competitor-watch` folder and drag these into the upload box: the `config`, `docs` and `scanner` folders, plus `requirements.txt` and `README.md`.
4. Click **Commit changes**.

### Step 3: Add the daily scan (the workflow file)

The workflow sits in a hidden folder called `.github`, which Chromebooks and Macs often skip when dragging folders. So create it by hand:

1. Click **Add file**, then **Create new file**.
2. In the file name box, type exactly: `.github/workflows/scan.yml`
   (typing the `/` creates the folders as you go)
3. Open `scan.yml` from the unzipped folder (it is in `.github/workflows/`), copy everything, and paste it into the big box. If you cannot see the hidden folder, the same text is in `docs/setup/scan.yml.txt`.
4. Click **Commit changes**.

### Step 4: Let the scan save its results

1. Go to **Settings**, then **Actions**, then **General**.
2. Scroll to **Workflow permissions**, choose **Read and write permissions**, click **Save**.

### Step 5: Turn on the dashboard

1. Go to **Settings**, then **Pages**.
2. Under **Build and deployment**, set Source to **Deploy from a branch**.
3. Branch: **main**, folder: **/docs**. Click **Save**.
4. After a minute or two the page shows your dashboard address, something like `https://YOUR-NAME.github.io/competitor-watch/`

At this point the dashboard shows **sample data** with a banner saying so. That is expected.

### Step 6 (optional): Turn on the AI brief

1. Get an API key from https://console.anthropic.com
2. In the repository, go to **Settings**, then **Secrets and variables**, then **Actions**.
3. Click **New repository secret**. Name: `ANTHROPIC_API_KEY`. Paste the key as the value. Click **Add secret**.

Without a key everything else still works; the brief box simply does not appear.

### Step 7: Run the first scan

1. Go to the **Actions** tab. If asked, click the button to enable workflows.
2. Click **Daily competitor scan** on the left, then **Run workflow**, then the green **Run workflow** button.
3. Wait for it to finish (a few minutes; CCC's large sitemap takes longest).

The first scan records a **baseline**: it saves what every competitor has today. Changes start appearing from the second scan, which runs automatically the next morning at 07:17 Israel time. The "Whole site today" view in the content grid works straight away.

## Everyday changes

All settings live in `config/clients.yaml`. To edit it: open the file on GitHub, click the pencil icon, make your change, click **Commit changes**. The next scan uses it.

**Exclude a section**, for every competitor:

```yaml
global_exclude:
  - /careers/
  - /investors/
```

**Exclude a section for one competitor only**, for example CCC's repair shop pages:

```yaml
      - domain: cccis.com
        exclude:
          - /repair-facilities/
```

Pattern rules: `/news/` matches anything starting with that path. A `*` means "anything", so `*/tag/*` also catches `/blog/tag/ai/`. For advanced cases, `regex:` followed by a regular expression.

**Add or change keywords:**

```yaml
    keywords:
      - group: Agentic AI
        terms: [agentic, ai agent, digital coworker]
```

Matching ignores capitals, treats hyphens in URLs as spaces, and also matches simple plurals ("adjuster" matches "adjusters").

**Add a competitor:** add a line `- domain: example.com` under `competitors`. If the scan says it cannot find a sitemap, add its sitemap address:

```yaml
      - domain: example.com
        sitemaps: [https://www.example.com/sitemap_index.xml]
```

**Add another client:** copy the whole Five Sigma block under `clients`, give it a new `id`, name, keywords and competitors. It appears in the Client menu on the dashboard.

**Tune content types:** edit `asset_types`. Rules are checked top to bottom, and the first match wins.

## Good to know

* Only pages listed in a competitor's XML sitemap are tracked. A site with no sitemap, or one that blocks automated requests, shows an **Error** status in the Competitors table with the reason.
* "Changed" means the sitemap's `lastmod` date moved. Some sites update that date on every page every day; if one competitor floods the feed with changes, that is usually why.
* For safety, if a sitemap suddenly shrinks by more than half, that day's scan for that competitor is skipped instead of reporting hundreds of false removals.
* Each scan reads up to 60 new or changed pages per competitor (titles and headings), with a short pause between requests. Change `max_pages_to_read_per_competitor` in the settings if needed.
* History is kept for 180 days (`keep_days`).

## Files

| Path | What it is |
|---|---|
| `config/clients.yaml` | Clients, competitors, keywords, exclusions, content types |
| `scanner/scan.py` | The scanner |
| `.github/workflows/scan.yml` | Runs the scanner daily and saves results |
| `docs/index.html` | The dashboard |
| `docs/data/` | Scan results the dashboard reads |
| `state/` | What each site looked like last time (used for comparison) |
