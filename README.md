# Vinbetyg

Weekly wine ratings from **Munskänkarna** (Sweden's wine society), matched to
**Systembolaget's** catalog and presented as a responsive, filterable browser.

Every reviewed wine carries its Munskänkarna score (20-point scale), the
panel's price/quality verdict, a full tasting note, and — where the wine is
sold by Systembolaget — the article number and a direct product link.

```bash
npm install
npm run dev          # http://localhost:3000 — runs immediately on bundled data
npm run sync:wines   # fetch the latest reviews
```

---

## Contents

- [How it works](#how-it-works)
- [Data sources](#data-sources)
- [Getting started](#getting-started)
- [Syncing data](#syncing-data)
- [Configuration](#configuration)
- [The interface](#the-interface)
- [Project layout](#project-layout)
- [Data model](#data-model)
- [Testing and validation](#testing-and-validation)
- [Home Assistant integration](#home-assistant-integration)
- [Known limitations](#known-limitations)

---

## How it works

```
munskankarna.se/sv/vinlocus/          Systembolaget
  │ release index                       │ (optional metadata API)
  ▼                                     ▼
lib/ingest/munskankarna.ts ──► Wine ──► lib/ingest/systembolaget.ts
  │  cheerio parsers                    │  article number → product URL
  ▼                                     ▼
        scripts/sync-wines.ts  ──►  data/wines.json
                                          │
                                          ▼
        app/page.tsx (server) ──► components/WineBrowser.tsx (client)
```

The store is read on the server and handed to the client as a single payload.
All filtering, searching and sorting then happen in memory, so interactions are
synchronous — no request round-trip and no loading states.

## Data sources

### 1. Munskänkarna / Vinlocus

[Vinlocus](https://www.munskankarna.se/sv/vinlocus/) is Munskänkarna's public
review database. Reviews are grouped into *provningar* (tastings), which map to
Systembolaget's release cycle:

| Tasting type | Cadence | In Systembolaget? |
|---|---|---|
| Tillfälligt sortiment | Weekly | Yes |
| Fast sortiment (nya viner / nya årgångar) | Monthly | Yes |
| Hitlista | Roughly biweekly | Yes |
| Lokalt och småskaligt | Monthly | Yes |
| Ordervaror | Monthly | Yes (order assortment) |
| Webbviner | Monthly | **No** — independent web merchants |
| Temaprovning | Occasional | Varies |

Each release page server-renders the *complete* record for every wine it
covers, so ingestion needs **one request per release** rather than one per
wine. The per-wine detail pages are fetched only with `--details`, which adds
the importer.

Fields extracted: name, vintage, producer, colour/style, country, region,
appellation, grapes, price, volume, alcohol, score, quality band, value
verdict, grape/district typicality, tasting note, and the Systembolaget
article number.

### 2. Systembolaget

Product links are generated from the article number and always work:

```
https://www.systembolaget.se/produkt/vin/{artikelnummer}/
```

Live metadata enrichment (price, volume, ABV, origin, assortment, organic) is
**optional** and off by default — see [Configuration](#configuration).
Munskänkarna already publishes price, volume, alcohol and origin, so the app is
fully functional without it.

Wines with no article number — Webbviner, sold by independent merchants — are
kept and clearly marked, with a catalog search link instead of a dead product
link. Filter them out with the **Finns på Systembolaget** pill.

## Getting started

Requires Node.js 20+ (developed on 22).

```bash
npm install
npm run dev
```

The app starts on real bundled data (`seeds/sample-reviews.json` — a frozen
snapshot of an actual sync: 212 wines across 7 releases), so **no network
access or credentials are needed to develop and test the UI**. The footer
shows "Exempeldata" until you run a sync.

## Syncing data

```bash
npm run sync:wines              # two newest releases of each tasting type
npm run sync:latest             # newest release of each type only
npm run sync:wines -- --all     # every release on the index
```

| Flag | Effect |
|---|---|
| `--all` | Ingest every release listed on the index |
| `--latest` | Newest release per tasting type only |
| `--kind <kind>` | Restrict to a tasting type (repeatable) |
| `--release <slug>` | Ingest one specific release (repeatable) |
| `--limit <n>` | Cap the number of releases fetched |
| `--details` | Also fetch per-wine detail pages (adds importer; one extra request per wine) |
| `--out <path>` | Write somewhere other than `data/wines.json` |
| `--dry-run` | Parse and report, write nothing |

Examples:

```bash
# This week's Systembolaget release
npm run sync:wines -- --kind tillfalligt-sortiment --limit 1

# One specific release, without writing
npm run sync:wines -- --release hitlista-3-september-2026 --dry-run

# Refresh the committed fixture
npm run sync:wines -- --latest --out seeds/sample-reviews.json
```

Output goes to `data/wines.json` (gitignored). Syncs are **incremental**: a
re-synced release replaces its own wines, and untouched releases are preserved,
so the store accumulates history across runs. Writes are atomic — an
interrupted sync cannot truncate the file.

The CLI reports a per-run summary and exits non-zero if any release failed:

```
Summary
  releases parsed     7
  wines parsed        212
  with artikelnummer  178  (84%)
  with score          212  (100%)
  with price          212  (100%)
```

### Scheduling

The sync is a plain CLI, so any scheduler works. Munskänkarna publishes
*Tillfälligt sortiment* on release days, so a daily run is ample:

```cron
15 6 * * * cd /path/to/winedata && npm run sync:wines >> sync.log 2>&1
```

## Configuration

Copy `.env.example` to `.env` and fill in only what you need — **the defaults
require no credentials**.

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `MUNSKANKARNA_COOKIE` | Session cookie, for member-restricted pages |
| `MUNSKANKARNA_USERNAME` / `_PASSWORD` | Reserved for credential-based login |
| `MUNSKANKARNA_BASE_URL` | Override the site base (testing/mirrors) |
| `INGEST_DELAY_MS` | Delay between requests (default `750`) |
| `SYSTEMBOLAGET_API_KEY` | Enables live catalog enrichment |
| `SYSTEMBOLAGET_API_BASE` | Override the API base URL |

### Munskänkarna credentials

The Vinlocus pages this project reads are **public** — the default sync needs no
login. If you need member-restricted content, supply a session cookie:

1. Sign in at [munskankarna.se](https://www.munskankarna.se/) in your browser.
2. Open DevTools → **Network**, reload, click any `munskankarna.se` request.
3. Copy the full **Cookie** request header.
4. Set it in `.env`:

   ```bash
   MUNSKANKARNA_COOKIE="ASP.NET_SessionId=...; .ASPXAUTH=..."
   ```

The login form is JavaScript-driven rather than a plain HTML `POST`, so cookie
injection is the supported path; `MUNSKANKARNA_USERNAME`/`_PASSWORD` are
reserved for a future form-login implementation and are not used today. The
CLI prints which mode it is running in:

```
Auth: session cookie supplied     # or: Auth: anonymous (public Vinlocus pages)
```

### Systembolaget enrichment

Without a key, the app uses Munskänkarna's own price/volume/alcohol/origin and
still generates correct product links. With a key, each wine is additionally
looked up in Systembolaget's external API:

```bash
SYSTEMBOLAGET_API_KEY=your-subscription-key
```

Enrichment never fails a sync. Each wine records its outcome — `enriched`,
`skipped`, `not-found`, `unavailable` or `error` — and the app degrades to
link-only.

> **Note:** `systembolaget.se` and its API are geo-restricted to Sweden. From
> outside Sweden the enricher records `unavailable` and the sync continues
> normally.

## The interface

**Desktop (≥1024px)** — a dense, scannable table: score, value verdict, wine
(with producer and grapes), vintage, origin, price, comparison price per litre,
and the Systembolaget link. Sortable column headers expose `aria-sort`, and the
header row sticks beneath the filter bar while scrolling.

**Mobile (<1024px)** — compact cards sized for one-handed browsing: score
anchors the left edge, price the right, and the Systembolaget control sits
along the bottom within thumb reach. Filters collapse behind a single sticky
**Filter** button so results stay visible.

Both layouts share one filter/sort engine (`lib/filters.ts`) and one set of
display helpers (`lib/display.ts`), so they cannot drift apart.

### Interactions

- **Release switcher** — jump to any tasting, e.g. *Tillfälligt sortiment 11 september 2026*.
- **Instant search** — across name, producer, grape, region, country and article
  number. Accent- and case-insensitive (`chateau` matches *Château*), and
  multiple terms narrow rather than widen.
- **Filter pills** — wine type, value verdict, price bracket, score threshold
  (14+/15+/16+), and Systembolaget availability. Each pill shows how many wines
  it would bring in.
- **Sorting** — highest score, best value, price low→high, price high→low, or
  name. *Best value* leads with Munskänkarna's own verdict (Fynd → Mer än
  prisvärt → Prisvärt → Ej prisvärt), then score, then price per litre — their
  panel judged value in context, which beats any ratio computed after the fact.
- **Copy artikelnummer** — one tap copies the number for Systembolaget's app or
  an in-store terminal, with a clipboard fallback for non-secure origins.

Results render a page at a time (**Visa fler**) so the initial DOM stays small
while counts and ordering always reflect the full matching set.

## Project layout

```
app/
  layout.tsx           Document shell, metadata, viewport
  page.tsx             Server component: loads the store, renders the browser
  globals.css          Design tokens (light/dark), focus and motion rules
  icon.svg             Favicon
components/
  WineBrowser.tsx      Client shell: state, filtering, layout switching
  Header.tsx           Sticky header: release switcher + instant search
  FilterBar.tsx        Filter pills (collapsible on mobile) + sort select
  WineTable.tsx        Desktop table with sortable sticky headers
  WineCard.tsx         Mobile card
  SystembolagetLink.tsx  Product link + copy-to-clipboard
  ScoreBadge.tsx / ValueBadge.tsx / ColorDot.tsx / FilterPill.tsx
lib/
  filters.ts           Filtering, search and sorting (pure)
  display.ts           Swedish labels and formatting
  useStickyOffset.ts   Measures the filter block for sticky table headers
  ingest/
    munskankarna.ts    Cheerio parsers for the index, releases and details
    systembolaget.ts   Link builder + optional metadata enrichment
    normalize.ts       Score/price/volume/date/vintage parsing (pure)
    http.ts            Fetch with retry, backoff, throttling, cookies
    store.ts           Atomic JSON read/write and incremental merge
scripts/sync-wines.ts  The sync CLI
seeds/sample-reviews.json  Committed fixture — the app runs on this
data/wines.json        Sync output (gitignored)
tests/                 Unit tests + captured HTML fixtures
types/wine.ts          Shared schema
```

## Data model

`types/wine.ts` models both sources side by side. A `Wine` carries the shared
identity and Munskänkarna's published figures, with the review and the catalog
match nested:

```ts
interface Wine {
  id: string;                  // `<releaseId>/<wine-slug>`
  name: string;                // vintage stripped
  fullName: string;            // as published
  vintage: number | null;      // null for non-vintage
  producer: string | null;
  color: WineColor;            // 'red' | 'white' | 'rose' | 'sparkling' | …
  country / region / appellation: string | null;
  grapes: string[];
  priceSek / volumeMl / alcoholPercent: number | null;
  pricePerLitre: number | null;          // computed

  review: {
    score: number | null;                // 0–20
    scale: 20 | 100;
    band: QualityBand | null;            // exceptionellt … enkelt
    valueRating: ValueRating | null;     // fynd | mer-an-prisvart | …
    typical: boolean;
    tastingNote: string | null;
    url: string | null;
  };

  systembolaget: {
    articleNumber: string | null;
    productUrl: string | null;
    matchMethod: 'artikelnummer' | 'name-search' | 'unmatched';
    enrichment: 'enriched' | 'skipped' | 'not-found' | 'unavailable' | 'error';
    // …live metadata, populated only when enrichment succeeds
  };

  releaseId: string;
}
```

Anything that cannot be determined is `null` rather than guessed, so the UI can
distinguish *unknown* from *zero*.

Scores keep their native 20-point scale with Munskänkarna's published bands
(18–20 exceptionellt, 15–17.5 högklassigt, 12–14.5 bra till mycket bra, 9–11.5
medelbra, 6–8.5 enkelt). The `scale` field exists so a future source on a
100-point scale fits the same schema; no lossy conversion is performed.

## Testing and validation

```bash
npm test        # 29 unit tests
npm run typecheck
npm run lint
npm run build
```

Parser tests run against **HTML captured from the live site**
(`tests/fixtures/`), so an upstream markup change surfaces as a failing test
rather than as an empty store after a sync. Filter and sort tests run against
the committed fixture and assert real invariants — that releases partition the
wine set exactly, that price brackets neither overlap nor drop wines, that
sorting is total and non-mutating, and that combined filters intersect.

The Home Assistant integration adds 148 Python tests, built test-first
(red → green → refactor) across five phases: parser, API/config flow,
coordinator/sensors, MQTT bridge, and HACS/dashboard compliance. Notable
coverage includes credential redaction in diagnostics, attribute payloads
staying under 8 kB, states respecting Home Assistant's 255-character limit, and
the Lovelace templates being rendered against live entity state so the
dashboard cannot drift from the sensor attributes it reads.

Verified in-browser during development (Chromium, light and dark, 390px and
1440px):

- **0 violations** from axe-core across WCAG 2.1 A/AA at both breakpoints in
  both themes. All three text tiers and all four value badges meet the 4.5:1
  contrast minimum.
- **Cumulative Layout Shift 0.000** and no horizontal overflow at any width.
- Keyboard path verified: skip link → release switcher → search → filter pills.

Accessibility is built in rather than bolted on: colour never carries meaning
alone (every badge is labelled), filter pills are real buttons with
`aria-pressed`, sortable headers expose `aria-sort`, the result count is an
`aria-live` region, zoom is not blocked, and `prefers-reduced-motion` is
honoured.

## Home Assistant integration

This repository also ships a HACS-distributable Home Assistant integration in
[`custom_components/munskankarna/`](custom_components/munskankarna/README.md),
so the same reviews can drive a dashboard and automations.

```
custom_components/munskankarna/   HACS integration (Python, async)
dashboard/                        Lovelace YAML, mobile + desktop views
tests/test_*.py                   pytest suite (the *.test.ts files are the web app's)
```

It is an independent async port of the same parsing rules — `httpx` +
BeautifulSoup rather than fetch + Cheerio — and both read the **same HTML
fixtures** in `tests/fixtures/`. `tests/test_parity.py` asserts the two produce
identical output, so the implementations cannot drift apart unnoticed.

Sensors expose the current release per tasting type, the top pick, the Fynd
count and the latest release date, each wine carrying its Systembolaget article
number and product link. An optional MQTT bridge republishes the snapshot for
consumers outside Home Assistant.

Releases roll over on their own: each poll re-reads the index and selects the
newest dated release per tasting type, so next week's *Tillfälligt sortiment*
is picked up without reconfiguration and the `entity_id` never changes.
Compatible with Home Assistant 2024.3 through 2026.x on Python 3.12–3.14.

```bash
pip install -r requirements-test.txt
python -m pytest          # 148 tests
ruff check custom_components tests
```

See the [integration README](custom_components/munskankarna/README.md) for
installation, options and example automations.

## Known limitations

- **Webbviner have no article number.** Those wines are sold by independent
  merchants, not Systembolaget. They are kept, marked, and given a catalog
  search link; the *Finns på Systembolaget* filter hides them.
- **Systembolaget enrichment is geo-restricted** to Sweden and needs an API
  key. Without it the app relies on Munskänkarna's own figures, which are
  complete for price, volume, alcohol and origin.
- **Importer requires `--details`.** It is the one field absent from listing
  pages, costing one extra request per wine.
- **Scraping depends on upstream markup.** Selectors have text-based fallbacks
  and a card that fails to parse is skipped with a warning rather than aborting
  the run, but a significant redesign would need the parsers updated — the
  fixture tests are there to catch exactly that.

## Attribution

Reviews, scores and tasting notes are the work of
[Munskänkarna](https://www.munskankarna.se/sv/vinlocus/). Prices and article
numbers refer to [Systembolaget](https://www.systembolaget.se/). This project
is an unofficial reader for personal use and is not affiliated with either
organisation; please respect their terms of service and keep sync frequency
reasonable (the default 750 ms delay between requests is deliberate).
