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

## Attribution

Reviews, scores and tasting notes are the work of
[Munskänkarna](https://www.munskankarna.se/sv/vinlocus/). Prices and article
numbers refer to [Systembolaget](https://www.systembolaget.se/). This is an
unofficial integration, not affiliated with either organisation. The default
6-hour interval and the 0.75 s spacing between requests are deliberate — please
keep them reasonable.
