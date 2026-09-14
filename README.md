# Munskänkarna for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.12%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/badge/version-1.1.2-blue.svg)](https://github.com/GuvHas/winedata/releases)

Weekly wine reviews from **Munskänkarna**, Sweden's wine society, matched to
**Systembolaget's** catalog — on your dashboard, and available to automations.

Munskänkarna's tasting panel grades new Systembolaget releases on a 20-point
scale and flags price/quality as *Fynd* → *Mer än prisvärt* → *Prisvärt* →
*Ej prisvärt*. This integration tracks the current release of each tasting type
and exposes every wine with its **article number and a direct product link**, so
a bargain is one tap from the Systembolaget page.

> [!NOTE]
> An unofficial, community-built integration. Not affiliated with, endorsed by
> or supported by Munskänkarna or Systembolaget — please send questions here,
> not to them.

---

## Contents

- [What you get](#what-you-get)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Entities](#entities)
- [Dashboard examples](#dashboard-examples)
- [Automations](#automations)
- [Services](#services)
- [MQTT bridge](#mqtt-bridge-optional)
- [Release history](#release-history)
- [How it stays current](#how-it-stays-current)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Attribution](#attribution)

---

## What you get

- One sensor per tasting type, always tracking its **current** release
- Top pick, *Fynd* count, latest release date and total wines tested
- Every wine carries its **Systembolaget article number and product URL**
- Releases roll over automatically — next week's tasting appears with no
  reconfiguration and the same `entity_id`
- **Keeps the last few releases per tasting type**, so a dashboard can show
  several weeks at once
- Optional MQTT bridge for consumers outside Home Assistant
- Full UI setup, English and Swedish, diagnostics with credentials redacted

## Prerequisites

| | |
|---|---|
| Home Assistant | 2024.12 or newer (tested through **2026.8**) |
| Python | 3.12 – 3.14 (whatever your HA ships) |
| Dependencies | `httpx`, `beautifulsoup4` — pure Python, installed automatically |
| Account | **None.** The review pages are public; a member login is optional |

No API key, no scraping setup, no compiled dependencies.

## Installation

### HACS (recommended)

1. Open **HACS → Integrations**
2. Top-right **⋮ → Custom repositories**
3. Add repository `https://github.com/GuvHas/winedata`, category **Integration**
4. Find **Munskänkarna** in the list and click **Download**
5. **Restart Home Assistant**

### Manual

Copy `custom_components/munskankarna/` into your `config/custom_components/`
directory and restart Home Assistant.

## Configuration

**Settings → Devices & Services → + Add Integration → Munskänkarna**

The setup dialog asks for:

| Field | Required | Notes |
|---|---|---|
| Base URL | no | Defaults to `https://www.munskankarna.se` |
| Username | no | Member e-mail or number — only for login-gated content |
| Password | no | Only needed alongside a username |

Leave the credentials blank unless you need member-only content. Setup verifies
the site can be both *reached* and *parsed*, so a silent layout change fails
immediately rather than leaving you with empty sensors.

### Options

**Settings → Devices & Services → Munskänkarna → Configure**

| Option | Default | Notes |
|---|---:|---|
| Update interval (hours) | 6 | Reviews appear weekly at most |
| Wines per sensor | 10 | See the note below |
| Releases kept per tasting type | 3 | See *Release history* below |
| Tastings to track | 4 types | Webbviner excluded by default |

**Webbviner are excluded by default** — those wines are sold by independent web
merchants (Supervin, Vinupplevelser) and have **no Systembolaget article
number**, so they cannot be linked to a product page.

> **Why "wines per sensor" is capped.** A Home Assistant sensor's state is
> limited to 255 characters, so the wine list lives in attributes — and
> attributes are written to the recorder and pushed to every client on each
> update. The list is trimmed to display fields (tasting notes are omitted),
> and each entry costs roughly 500 bytes: the default of 10 is about 6 kB per
> update, and the ceiling of 25 about 13 kB. A full release would be ~49 kB.
> The release `summary` is truncated to 280 characters for the same reason —
> most tastings publish a sentence, but Webbviner publishes a ~1500-character
> editorial listing into that field.
> If you raise the cap, consider excluding the entity from the recorder.

## Entities

All entities are grouped under one **Munskänkarna** device.

| Entity | State | Key attributes |
|---|---|---|
| `sensor.munskankarna_tillfalligt_sortiment` | wines in the release | `wines`, `release_title`, `release_date`, `release_url`, `summary`, `stale` |
| `sensor.munskankarna_fast_sortiment` | wines in the release | same |
| `sensor.munskankarna_hitlista` | wines in the release | same |
| `sensor.munskankarna_lokalt_och_smaskaligt` | wines in the release | same |
| `sensor.munskankarna_top_pick` | best wine's name | `score`, `price`, `url`, `article_number`, `producer` |
| `sensor.munskankarna_fynd` | number of *Fynd* | `wines` |
| `sensor.munskankarna_latest_release` | ISO date | `releases` |
| `sensor.munskankarna_wines_tested` | total wines | `warnings` |
| `sensor.munskankarna_history` | releases retained | `releases` — the whole archive, wines included |
| `sensor.munskankarna_fynd_history` | *Fynd* across the archive | `per_kind` |

Entity ids are pinned to the integration's own name, so renaming the device in
the UI changes what you *see* without changing the ids your dashboards and
automations depend on.

> [!NOTE]
> Entities created **before** 1.1.1 kept whatever id Home Assistant derived at
> the time, so a renamed device could leave you with a mix —
> `sensor.munskankarna_hitlista` alongside `sensor.virtual_munskankarna_history`.
> Upgrading to **1.1.2 renames those for you** on the next restart, and logs
> each rename. Ids you chose yourself are never touched: only an id that is
> exactly what Home Assistant would have derived is moved, and never onto an id
> something else already holds. If a rename is skipped for that reason, the log
> says which entity is in the way — free the id, or point the dashboard at the
> one you have.

A sensor exists for every tasting type you have enabled. If its release cannot
be refreshed on a given poll, it keeps the wines from the last successful one
and sets `stale: true` rather than blanking — it only reads `unavailable` if it
has never loaded. The other types are unaffected either way.

### Shape of a `wines` entry

```yaml
- name: Brut Nature Gran Reserva
  full_name: Brut Nature Gran Reserva 2017
  vintage: 2017
  producer: Heretat Mas Tinell, S.L.
  score: 14.0              # Munskänkarna's 20-point scale
  band: bra                # exceptionellt | hogklassigt | bra | medelbra | enkelt
  value: mer-an-prisvart   # fynd | mer-an-prisvart | prisvart | ej-prisvart
  price: 199.0             # SEK
  price_per_litre: 265.33
  volume_ml: 750
  color: sparkling         # red | white | rose | sparkling | fortified | dessert
  country: Spanien
  region: Navarra
  grapes: [xarel-lo, macabeo, parellada]
  article_number: "9049001"
  url: https://www.systembolaget.se/produkt/vin/9049001/
  review_url: https://www.munskankarna.se/sv/vinlocus/...
```

Wines are sorted best-first: highest score, then Munskänkarna's own value
verdict, then price.

## Dashboard examples

A complete four-view dashboard is in
[`dashboard/munskankarna-lovelace.yaml`](dashboard/munskankarna-lovelace.yaml).

| View | For | Shows |
|---|---|---|
| **Veckans viner** | phone | This week's picks, one tap to Systembolaget |
| **Alla provningar** | desktop | Full detail per tasting type, current release |
| **Arkiv** | both | Every retained release, grouped by publication date |
| **Höjdpunkter** | both | Best wines and every *Fynd* across the whole window |

### Installing it

1. **Settings → Dashboards → + Add Dashboard → New dashboard from scratch**
2. Open it, then the **pencil** (top right) **→ ⋮ → Raw configuration editor**
3. Select all, paste the file over it, **Save**

> [!WARNING]
> Pasting replaces a dashboard's *entire* configuration. Start a new dashboard
> unless you mean to overwrite an existing one.

The entity ids assume a single config entry and default naming — check yours
under **Developer Tools → States** (filter on `munskankarna`). A second config
entry gets `_2`-suffixed ids.

### Requirements

| | |
|---|---|
| Home Assistant | 2024.12+ (for the `sections` view type) |
| Integration | 1.1.0+ for the **Arkiv** and **Höjdpunkter** views — they read `sensor.munskankarna_history` |
| HACS frontend cards | **None.** Every card in the active config ships with Home Assistant |

Two optional HACS cards (`flex-table-card`, `auto-entities`) are sketched,
commented out, at the bottom of the file. They improve the archive views and
the dashboard is complete without them.

Responsiveness comes from the `sections` view type: `max_columns` is an upper
bound and sections reflow to one column on a phone. The mobile-leaning views
keep their tables to three or four columns so they stay readable at that width.

Every optional field is guarded with `is not none`. That is not decoration: a
markdown card that raises renders as a red error box, so one wine with no price
would take out the whole table rather than its own row. Anything sorted carries
an explicit rank for the same reason — Jinja's `sort()` raises comparing `None`
to a number, and sorting happens before any display guard can help.

Two highlights:

### Mobile — this week's picks

Tap the wine name to open it on Systembolaget.

```yaml
type: markdown
content: >-
  {% set s = 'sensor.munskankarna_tillfalligt_sortiment' %}

  {% set wines = state_attr(s,'wines') or [] %}

  ### {{ state_attr(s,'release_title') or 'Tillfälligt sortiment' }}

  {% if wines %}

  | | Vin | Pris |

  |---:|---|---:|

  {% for w in wines -%}

  | **{% if w.score is not none %}{{ w.score }}{% else %}—{% endif %}** |
  [{{ w.name }}{% if w.vintage %} {{ w.vintage }}{% endif %}]({{ w.url or
  w.review_url }}){% if w.value == 'fynd' %} ⭐{% endif %} | {% if w.price is
  not none %}{{ w.price | round(0) }} kr{% else %}—{% endif %} |

  {% endfor %}

  _{{ state_attr(s,'release_date') }} · {{ states(s) }} viner totalt_

  {% else %}_Inga viner i den här provningen._{% endif %}
```

### Desktop — full detail with comparison price

```yaml
type: markdown
content: >-
  {% set s = 'sensor.munskankarna_tillfalligt_sortiment' %}

  {% set wines = state_attr(s,'wines') or [] %}

  {% if wines %}

  | Betyg | Vin | Ursprung | Pris | kr/l | |

  |---:|---|---|---:|---:|---|

  {% for w in wines -%}

  | **{% if w.score is not none %}{{ w.score }}{% else %}—{% endif %}** |
  {{ w.name }}{% if w.vintage %} {{ w.vintage }}{% endif %}<br><sub>{{
  w.producer or '—' }}</sub> | {{ w.country or '—' }}{% if w.region %},
  {{ w.region }}{% endif %} | {% if w.price is not none %}{{ w.price | round(0)
  }} kr{% else %}—{% endif %} | {% if w.price_per_litre is not none %}{{
  w.price_per_litre | round(0) }}{% else %}—{% endif %} | {% if w.url
  %}[🔗]({{ w.url }}){% endif %} |

  {% endfor %}

  {% else %}_Ingen data._{% endif %}
```

### A refresh button

```yaml
type: button
name: Uppdatera nu
icon: mdi:refresh
tap_action:
  action: perform-action
  perform_action: munskankarna.trigger_sync
  data: {}
```

> The dashboard's templates are rendered against live entity state in the test
> suite, so they cannot drift from the attributes they read.

## Automations

Notify when a genuine bargain lands:

```yaml
automation:
  - alias: "Wine: notify on a high-scoring Fynd"
    triggers:
      - trigger: state
        entity_id: sensor.munskankarna_tillfalligt_sortiment
    conditions:
      - condition: template
        value_template: >-
          {{ state_attr('sensor.munskankarna_tillfalligt_sortiment','wines')
             | selectattr('value','eq','fynd')
             | selectattr('score','ge',15)
             | selectattr('price','le',150)
             | list | count > 0 }}
    actions:
      - action: notify.mobile_app
        data:
          title: "Veckans vinfynd 🍷"
          message: >-
            {% set picks = state_attr('sensor.munskankarna_tillfalligt_sortiment','wines')
               | selectattr('value','eq','fynd')
               | selectattr('score','ge',15)
               | selectattr('price','le',150) | list %}
            {% for w in picks %}{{ w.name }} — {{ w.score }}/20, {{ w.price }} kr
            {% endfor %}
```

## Services

| Service | Purpose |
|---|---|
| `munskankarna.trigger_sync` | Fetch the latest reviews immediately |
| `munskankarna.publish_mqtt` | Publish the current snapshot to MQTT (`topic`, `retain`) |

## MQTT bridge (optional)

`munskankarna.publish_mqtt` publishes **retained** discovery and state messages,
so subscribers — including Home Assistant's own MQTT discovery — get current
state on connect rather than waiting for the next poll. Retained messages are
also what lets discovered entities survive a restart.

Requires the MQTT integration. Without it, the service logs a warning and does
nothing; your sensors are unaffected.

**Topics are scoped to the config entry.** Discovery goes to
`homeassistant/sensor/munskankarna_<entry_id>/<kind>/config` and state, unless
you pass a `topic`, to `munskankarna/wines/<entry_id>/<kind>/state`. Retained
messages are keyed by topic, so without the entry id a second entry — or a
second Home Assistant sharing the broker — would overwrite the first's
discovery config and the two would collapse into one entity.

> [!NOTE]
> If you used the MQTT bridge before 1.0.4, the old retained messages are
> still on the broker under the unscoped topics and the discovered entity will
> be re-created under a new id. Clear the stale ones by publishing an empty
> retained payload to `homeassistant/sensor/munskankarna_<kind>/config`.

## Release history

The integration retains the **last few releases per tasting type**, not just the
current one, so a dashboard can show several weeks side by side. The depth is
set in Options and defaults to three.

Retention is **count-based, not age-based**, and that is deliberate. The
categories publish on very different cadences:

| Tasting type | Publishes roughly | 3 releases ≈ |
|---|---|---|
| Tillfälligt sortiment | every 7 days | 3 weeks |
| Hitlistan | every 14 days | 6 weeks |
| Lokalt och småskaligt | every 28–35 days | 3 months |
| Fast sortiment | every 30–60 days | 3–6 months |

A fixed 21-day window would hold three or four releases of *Tillfälligt
sortiment* and **nothing at all** for the others through most of each month —
two of the four tracked by default would simply read zero. Counting releases
instead means every category keeps its current release plus real history; the
trade-off is that "three releases" reaches back further for the slower ones.

> [!NOTE]
> The archive lives in `.storage`, **not the recorder**. It costs no database
> rows and no websocket traffic, and it survives a restart — so Home Assistant
> restarting does not re-read release pages that cannot have changed. Published
> release pages are treated as immutable: only the current release of each type
> is re-read on each poll.

### Why the archive sometimes shows fewer wines than you configured

Home Assistant's recorder refuses to store a state whose attributes exceed
**16 KiB**, dropping them with a warning — the entity keeps working while its
history is silently lost. The archive spans every retained release of every
tracked type, so it can outgrow that on its own.

`sensor.munskankarna_history` therefore measures its payload and trims the wine
lists **uniformly** until it fits, publishing what it did:

| Attribute | Meaning |
|---|---|
| `wines_per_release` | How many wines each retained release is showing |
| `truncated` | `true` when that is fewer than your *Wines per sensor* option |

Every release always keeps its date, wine count and link — only the wine lists
shrink, so the timeline never lies about what was published. **The dashboard
says when it is showing a trimmed list**, and the *Fynd* card reconciles what
it can list against the true total on `sensor.munskankarna_fynd_history`. With the four
default types, three releases each, expect around three wines per release; to
see more of each, track fewer tasting types or reduce the retention depth.

Wines in the archive also carry a leaner field set than the current-release
sensors — name, vintage, producer, score, value, price, price/litre and one
`url` — which is roughly 240 bytes each rather than 590.

The whole archive, wines included, is carried by the single
`sensor.munskankarna_history` entity. The per-type sensors get only a compact
`history` summary — dates and counts, no wine lists — so they stay exactly the
size they were. If you would rather the archive never reached the recorder:

```yaml
recorder:
  exclude:
    entities:
      - sensor.munskankarna_history
```

The **Arkiv** and **Höjdpunkter** views of
[the dashboard](dashboard/munskankarna-lovelace.yaml) are built on it: every
retained release grouped by publication date, and the best wines and all *Fynd*
across the whole window. Stock cards only.

## How it stays current

Every update cycle the integration re-reads Munskänkarna's release index and
selects the **newest dated release per tasting type**. When next week's
*Tillfälligt sortiment* is published, the existing sensor switches to it — same
`entity_id`, same attributes — so dashboards and automations keep working with
no reconfiguration.

Two things worth knowing:

- A release whose title has **no parseable date** will not displace a dated one.
  That is deliberate (it stops an oddly-titled special hiding the current week),
  but such a release is skipped.
- Sensors are created for **every tasting type you have enabled**, whether or
  not its page loaded on the first poll. One that was unreachable at startup
  reads `unavailable` and starts reporting as soon as a later poll succeeds —
  no reload. Enabling a new type in Options reloads the entry and adds it.
- A page that cannot be recognised as a release page at all — maintenance, a
  login wall, a redesign — **never reports zero wines**. That tasting type
  keeps its previous wines, flagged `stale: true`; if *nothing* could be
  refreshed the whole update fails, so every sensor keeps what it had. Either
  way an authoritative-looking `0` is never published.
- Recognition requires the wine list itself, not just the surrounding page. A
  redesign that keeps the chrome but renames the cards is treated as broken,
  not as a quiet week.

One client with one login serves an entire update cycle, and all I/O is
non-blocking `httpx` — nothing touches the event loop.

## Troubleshooting

**"Failed to connect, or the page layout was not recognised"**
Setup requires the site to be both reachable *and* parseable. A 200 response
that yields no releases counts as a failure, because every sensor would
otherwise be silently empty.

**A sensor is `unavailable`**
That tasting type's release failed to load; the others are unaffected. Check the
`warnings` attribute on `sensor.munskankarna_wines_tested`, or download
diagnostics from the integration page — credentials are redacted.

**Home Assistant asks me to sign in again**
Munskänkarna rejected the stored credentials. The reauth dialog only replaces
the username and password; your base URL and options are preserved.

**Updates have stopped and the log says "asked us to slow down"**
Munskänkarna returned HTTP 429 and named a cooldown, which the integration now
honours — no request is made until it expires, and pressing *Uppdatera nu* will
not override it. Download diagnostics to see
`coordinator.rate_limit_cooldown_seconds`. If it recurs, raise the update
interval in Options; the reviews are published weekly at most.

**"This dashboard references the following entities, which are unknown to
Home Assistant"**
The entities exist — read the ids Home Assistant suggests in that message. If
they carry a prefix (`sensor.virtual_munskankarna_fynd_history`), they were
registered before 1.1.1 under a renamed device. Update to 1.1.2 and restart:
the ids are renamed automatically, and the log records each one. An id you
chose yourself is left alone by design, so point the dashboard at it instead.

**"Detected blocking call to load_verify_locations"**
Fixed in 1.0.1 — update the integration.

## Development

```bash
pip install -r requirements-test.txt
python -m pytest          # 375 tests
ruff check custom_components tests
```

CI runs the suite against the declared minimum (2024.12), the primary target
(**2026.8**) and whatever Home Assistant ships today, plus a job exercising the
Home Assistant-independent modules on Python 3.14 — weekly, so a breaking core
release or an upstream markup change surfaces before users hit it.

### Cutting a release

HACS shows a **commit hash** rather than a version number until the repository
has a *published GitHub Release*. It decides by calling GitHub's
`repos.releases.list` API, which returns Release objects only — **a bare git
tag is not enough**, and drafts and pre-releases are skipped.

Releasing is therefore automatic, and the manifest version is the trigger:

1. Bump `version` in `custom_components/munskankarna/manifest.json` (and the
   matching `version` in `pyproject.toml` — a test enforces they agree)
2. Merge that to `main`

That is the whole procedure. On every push to `main` the Release workflow reads
the manifest version and asks GitHub whether a Release for it already exists:

- **It does** → the run is a no-op, so ordinary merges cost nothing. The run
  logs a notice reminding you to bump the version if the change should reach
  users.
- **It does not** → the `vX.Y.Z` tag is created and a non-draft, non-prerelease
  Release marked *latest* is published. HACS then offers that version.

Because the version always comes from the manifest, the tag, the Release and
the manifest cannot disagree. A pushed `v*` tag that contradicts the manifest
fails the run rather than publishing a mismatched Release.

**Actions → Release → Run workflow** still works, as a recovery path if a run
fails for an unrelated reason; it is idempotent, as is the whole workflow.

> [!IMPORTANT]
> Forgetting the version bump is the one way to ship nothing: the code reaches
> `main` but HACS keeps offering the previous version, since there is no new
> Release to offer. The notice in the workflow log is there to catch it.

## Attribution

The reviews, scores and value verdicts this integration surfaces are the work of
**[Munskänkarna](https://www.munskankarna.se)**, Sweden's wine society, and
remain theirs. Article numbers and product pages belong to
**[Systembolaget](https://www.systembolaget.se)**.

This project is built and maintained by [@GuvHas](https://github.com/GuvHas) as
an independent, unofficial integration. It is **not affiliated with, endorsed by
or supported by Munskänkarna or Systembolaget**, and neither organisation is
responsible for it or for anything it displays. Bugs, questions and feature
requests belong in this repository's
[issue tracker](https://github.com/GuvHas/winedata/issues) — please do not take
them to Munskänkarna or Systembolaget.

It reads Munskänkarna's **public** review pages; credentials are optional and
only needed for member-only content. Requests identify themselves honestly
rather than impersonating a browser, are spaced out, are limited to one poll
every few hours by default, and stop entirely when the site asks them to. If you
value the reviews, [support Munskänkarna](https://www.munskankarna.se) by
becoming a member.
