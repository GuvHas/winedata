# Munskänkarna for Home Assistant

Brings **Munskänkarna's** weekly wine reviews into Home Assistant, each matched
to its **Systembolaget** catalog entry — so you can see this week's high scorers
and *Fynd* on a dashboard, and get notified when a bargain lands.

Munskänkarna is Sweden's wine society; their tasting panel grades new
Systembolaget releases on a 20-point scale and flags price/quality with
*Fynd* → *Mer än prisvärt* → *Prisvärt* → *Ej prisvärt*.

## Features

- One sensor per tasting type, tracking its **current** release
  (Tillfälligt sortiment, Fast sortiment, Hitlistan, Lokalt och småskaligt, …)
- Top pick, Fynd count, latest release date and total wines tested
- Every wine carries its **Systembolaget article number and product link**
- Optional **MQTT bridge** for consumers outside Home Assistant
- `munskankarna.trigger_sync` service for on-demand refresh
- Full UI configuration, English and Swedish translations

## Compatibility

| | |
|---|---|
| Home Assistant | **2024.3 → 2026.x** |
| Python | 3.12 – 3.14 |
| Dependencies | `httpx`, `beautifulsoup4`, `lxml` — all ship wheels for 3.14 |

Verified against Home Assistant's `dev` branch (2026.10): every core API this
integration uses is still present, and the parser and HTTP client pass their
full suites on Python 3.14 (which HA 2026.10 requires).

Two compatibility shims keep the same code working across that range:

- `ConfigFlowResult` is imported with a fallback to `FlowResult`, since the
  former only exists from 2024.4.
- The options flow keeps its entry on a private attribute. Home Assistant
  2024.11+ turned `OptionsFlow.config_entry` into a read-only property, so
  assigning it — the old idiom — now fails; never touching it works everywhere.
- `config_entry=` is passed to `DataUpdateCoordinator` when the running core
  accepts it (2024.12+), and omitted otherwise.

CI runs the suite against both the declared minimum and whatever Home Assistant
ships today, weekly, so a breaking core change surfaces here first.

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/GuvHas/winedata`, category **Integration**
3. Install **Munskänkarna**, then restart Home Assistant
4. Settings → Devices & Services → **Add Integration** → *Munskänkarna*

### Manual

Copy `custom_components/munskankarna/` into your `config/custom_components/`
directory and restart.

## Configuration

Everything is configured through the UI.

**Credentials are optional.** The Vinlocus review pages this integration reads
are public, and it is fully functional anonymously. Supply your member login
only if you need content that requires signing in — the integration performs a
real Umbraco member login (replaying the `__RequestVerificationToken` and
`ufprt` form tokens) and keeps the session cookie for subsequent requests.

### Options

| Option | Default | Notes |
|---|---:|---|
| Update interval (hours) | 6 | Reviews are published weekly at most |
| Wines per sensor | 10 | See *Why the list is capped* below |
| Tastings to track | 4 kinds | Webbviner excluded by default |

**Webbviner are excluded by default** because those wines are sold by
independent web merchants (Supervin, Vinupplevelser) and have **no Systembolaget
article number** — they would render as unlinkable rows.

## Does it follow new releases automatically?

Yes. Every update cycle (6 hours by default) the integration re-reads
Munskänkarna's release index and selects the **newest dated release per tasting
type**. When next week's *Tillfälligt sortiment* is published it is picked up on
the following poll, and the existing sensor switches to it — same `entity_id`,
same attributes, no reconfiguration. Dashboards and automations keep working
untouched. This is covered by `tests/test_rollover.py`.

Two caveats worth knowing:

- A release whose title carries **no parseable date** will not displace a dated
  one. That is deliberate — it prevents an oddly-titled special from hiding the
  current week — but it does mean such a release would be skipped.
- Sensors are created for the tasting types that loaded **at setup time**. If
  you enable a new tasting type in the options, the entry reloads and the
  sensor appears. If a type was failing when you first set the integration up,
  reload the entry once it recovers.

## Entities

| Entity | State | Key attributes |
|---|---|---|
| `sensor.munskankarna_<tasting>` | wine count | `wines`, `release_title`, `release_date`, `release_url` |
| `sensor.munskankarna_top_pick` | best wine's name | `score`, `price`, `url`, `article_number` |
| `sensor.munskankarna_fynd` | number of Fynd | `wines` |
| `sensor.munskankarna_latest_release` | ISO date | `releases` |
| `sensor.munskankarna_wines_tested` | total wines | `warnings` |

Each entry in `wines` carries: `name`, `vintage`, `producer`, `score`, `band`,
`value`, `price`, `price_per_litre`, `volume_ml`, `color`, `country`, `region`,
`grapes`, `article_number`, `url`, `review_url`.

### Why the list is capped

Home Assistant caps a sensor's **state at 255 characters**, so the wine list can
never be the state — it lives in attributes. Attributes are written to the
recorder and broadcast over the websocket on *every* update, so the list is
trimmed to display fields (tasting notes are omitted) and capped by the
*Wines per sensor* option:

| Payload | Size |
|---|---:|
| Full store (all releases) | 266 kB |
| One full release, all fields | 49 kB |
| 10 wines, display fields | **2.3 kB** |

If you raise the cap substantially, consider excluding these entities from the
recorder:

```yaml
recorder:
  exclude:
    entities:
      - sensor.munskankarna_tillfalligt_sortiment
```

## Event-loop safety

Home Assistant flags any blocking I/O performed on the event loop. Creating an
`httpx.AsyncClient()` without a `verify` argument is one such case — httpx then
calls `ssl.create_default_context()`, which reads certifi's CA bundle from disk:

```
Detected blocking call to load_verify_locations with args
(<ssl.SSLContext ...>, '.../certifi/cacert.pem', None, None) inside the event loop
```

This integration never lets that happen:

- Inside Home Assistant, the config flow and coordinator pass
  `homeassistant.util.ssl.get_default_context()` — a context HA has already
  built off-loop at startup — so none is created here at all.
- Standalone (tests, CLI use), `api.py` builds one in a worker thread via
  `asyncio.to_thread` and caches it for the process.

One client, with one login, serves an entire update cycle regardless of how
many tastings are tracked. An earlier version opened a fresh client and
re-posted the login for the index *and* every release — five of each per poll
on a default configuration.

`tests/test_event_loop_safety.py` guards this by installing the same probe Home
Assistant uses (patching `ssl.SSLContext.load_verify_locations`) and driving the
config flow, entry setup and a full update cycle through it. It includes a test
asserting the probe still catches a deliberately unguarded client, so it cannot
silently stop testing anything.

## Reauthentication

If Munskänkarna rejects stored credentials, the coordinator raises
`ConfigEntryAuthFailed` and Home Assistant opens a reauth dialog asking for the
password again. The base URL and options are preserved; only the credentials
are replaced.

## Services

### `munskankarna.trigger_sync`
Fetch the latest reviews immediately.

### `munskankarna.publish_mqtt`
Publish the current snapshot to MQTT. Fields: `topic`, `retain` (default true).

Messages are published **retained**, which is what lets MQTT-discovered
entities survive a Home Assistant restart — unlike states pushed via the REST
API, which vanish until the next poll.

## Dashboard

A ready-made Lovelace config with mobile and desktop views is at
[`dashboard/munskankarna-lovelace.yaml`](../../dashboard/munskankarna-lovelace.yaml).
It uses only built-in cards.

## Example automation

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

## Troubleshooting

**"Failed to connect, or the page layout was not recognised"** — setup verifies
it can both *reach* and *parse* the site. A 200 that yields no releases is
treated as a failure, because every sensor would otherwise be silently empty.

**A sensor is unavailable** — that tasting type's release failed to load; the
others are unaffected. Check `sensor.munskankarna_wines_tested`'s `warnings`
attribute, or download diagnostics from the integration page (credentials are
redacted).

**"Detected blocking call to load_verify_locations"** — fixed in 1.0.1. Update
the integration; see [Event-loop safety](#event-loop-safety).

## Attribution

Reviews, scores and tasting notes are the work of
[Munskänkarna](https://www.munskankarna.se/sv/vinlocus/). Prices and article
numbers refer to [Systembolaget](https://www.systembolaget.se/). This is an
unofficial integration, not affiliated with either organisation. The default
6-hour interval and the 0.75 s spacing between requests are deliberate — please
keep them reasonable.
