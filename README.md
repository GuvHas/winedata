# Munskänkarna for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.12%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/badge/version-1.0.2-blue.svg)](https://github.com/GuvHas/winedata/releases)

Weekly wine reviews from **Munskänkarna**, Sweden's wine society, matched to
**Systembolaget's** catalog — on your dashboard, and available to automations.

Munskänkarna's tasting panel grades new Systembolaget releases on a 20-point
scale and flags price/quality as *Fynd* → *Mer än prisvärt* → *Prisvärt* →
*Ej prisvärt*. This integration tracks the current release of each tasting type
and exposes every wine with its **article number and a direct product link**, so
a bargain is one tap from the Systembolaget page.

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
| Tastings to track | 4 types | Webbviner excluded by default |

**Webbviner are excluded by default** — those wines are sold by independent web
merchants (Supervin, Vinupplevelser) and have **no Systembolaget article
number**, so they cannot be linked to a product page.

> **Why "wines per sensor" is capped.** A Home Assistant sensor's state is
> limited to 255 characters, so the wine list lives in attributes — and
> attributes are written to the recorder and pushed to every client on each
> update. The list is trimmed to display fields (tasting notes are omitted):
> 10 wines is about 2.3 kB, versus roughly 49 kB for a full release.
> If you raise the cap a lot, consider excluding the entity from the recorder.

## Entities

All entities are grouped under one **Munskänkarna** device.

| Entity | State | Key attributes |
|---|---|---|
| `sensor.munskankarna_tillfalligt_sortiment` | wines in the release | `wines`, `release_title`, `release_date`, `release_url`, `summary` |
| `sensor.munskankarna_fast_sortiment` | wines in the release | same |
| `sensor.munskankarna_hitlista` | wines in the release | same |
| `sensor.munskankarna_lokalt_och_smaskaligt` | wines in the release | same |
| `sensor.munskankarna_top_pick` | best wine's name | `score`, `price`, `url`, `article_number`, `producer` |
| `sensor.munskankarna_fynd` | number of *Fynd* | `wines` |
| `sensor.munskankarna_latest_release` | ISO date | `releases` |
| `sensor.munskankarna_wines_tested` | total wines | `warnings` |

A sensor only exists for a tasting type you have enabled, and goes
`unavailable` if that particular release fails to load — the others keep working.

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

A complete two-view dashboard is in
[`dashboard/munskankarna-lovelace.yaml`](dashboard/munskankarna-lovelace.yaml)
(**Settings → Dashboards → + → ⋮ → Raw configuration editor**). It uses only
built-in cards — no HACS frontend plugins. Two highlights:

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

  | **{{ w.score }}** | [{{ w.name }}{% if w.vintage %} {{ w.vintage }}{% endif
  %}]({{ w.url or w.review_url }}){% if w.value == 'fynd' %} ⭐{% endif %} |
  {{ w.price | round(0) }} kr |

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

  | **{{ w.score }}** | {{ w.name }}{% if w.vintage %} {{ w.vintage }}{% endif
  %}<br><sub>{{ w.producer }}</sub> | {{ w.country }}{% if w.region %},
  {{ w.region }}{% endif %} | {{ w.price | round(0) }} kr |
  {{ w.price_per_litre | round(0) }} | {% if w.url %}[🔗]({{ w.url }}){% endif %} |

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
- Sensors are created for the tasting types that loaded **at setup time**.
  Enabling a new type in Options reloads the entry and creates its sensor.

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

**"Detected blocking call to load_verify_locations"**
Fixed in 1.0.1 — update the integration.

## Development

```bash
pip install -r requirements-test.txt
python -m pytest          # 171 tests
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

So releases are made by workflow, not by hand:

1. Bump `version` in `custom_components/munskankarna/manifest.json` (and the
   matching `version` in `pyproject.toml` — a test enforces they agree)
2. Merge that to `main`
3. **Actions → Release → Run workflow**

The workflow reads the version from the manifest, creates the matching `vX.Y.Z`
tag if needed, and publishes a non-draft Release marked latest. HACS then offers
that version instead of a commit.

Parser tests run against HTML captured from the live site
(`tests/fixtures/`), and the suite includes a probe replicating Home
Assistant's own blocking-call detector.

## Attribution

Reviews, scores and tasting notes are the work of
[Munskänkarna](https://www.munskankarna.se/sv/vinlocus/). Prices and article
numbers refer to [Systembolaget](https://www.systembolaget.se/).

This is an unofficial integration, not affiliated with either organisation. The
default 6-hour interval and the 0.75 s spacing between requests are deliberate —
please keep them reasonable.

Licensed under the [MIT License](LICENSE).
