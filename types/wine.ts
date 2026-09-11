/**
 * Shared domain schema.
 *
 * Two sources are modelled side by side:
 *  - `review`        — what Munskänkarna's tasting panel published (Vinlocus).
 *  - `systembolaget` — the catalog entry the review points at.
 *
 * Munskänkarna's own fields (price, volume, alcohol, country) are recorded on
 * the wine itself because they are always present; `systembolaget` carries the
 * article number, the canonical product link, and any live metadata fetched
 * from Systembolaget. Anything that cannot be determined is `null` rather than
 * guessed, so the UI can distinguish "unknown" from "zero".
 */

/** Wine colour/style, normalised from Munskänkarna's Swedish category labels. */
export type WineColor =
  | 'red'
  | 'white'
  | 'rose'
  | 'sparkling'
  | 'fortified'
  | 'dessert'
  | 'other';

/**
 * Munskänkarna's price/quality verdict, in descending order of attractiveness.
 * Rendered as a symbol next to the score on their own site.
 */
export type ValueRating = 'fynd' | 'mer-an-prisvart' | 'prisvart' | 'ej-prisvart';

/** Scale a score is expressed on. Munskänkarna grades on 20 points. */
export type ScoreScale = 20 | 100;

/**
 * Quality band names published alongside Munskänkarna's 20-point scale.
 * 18–20 exceptionellt · 15–17.5 högklassigt · 12–14.5 bra–mycket bra
 * 9–11.5 medelbra · 6–8.5 enkelt
 */
export type QualityBand =
  | 'exceptionellt'
  | 'hogklassigt'
  | 'bra'
  | 'medelbra'
  | 'enkelt';

/** A Systembolaget assortment category, derived from the tasting type. */
export type AssortmentKind =
  | 'tillfalligt-sortiment'
  | 'fast-sortiment'
  | 'hitlistan'
  | 'lokalt-och-smaskaligt'
  | 'bestallningssortimentet'
  | 'webbviner'
  | 'temaprovning'
  | 'ovrigt';

/** A single published tasting ("provning") — the weekly/monthly release batch. */
export interface Release {
  /** Slug as used by Munskänkarna, e.g. `tillfalligt-sortiment-11-september-2026`. */
  id: string;
  /** Display title, e.g. `Tillfälligt sortiment 11 september 2026`. */
  title: string;
  kind: AssortmentKind;
  /** Human label for `kind`, e.g. `Tillfälligt sortiment`. */
  kindLabel: string;
  /** ISO date (YYYY-MM-DD) parsed from the title, when it carries one. */
  date: string | null;
  /** Absolute URL of the release page on munskankarna.se. */
  url: string;
  /** Editorial blurb ("Om provningen"), when present. */
  summary: string | null;
  /** Number of wines ingested for this release. */
  wineCount: number;
}

/** Munskänkarna's review of one wine. */
export interface MunskankarnaReview {
  /** Numeric score, e.g. 14.5. Null when the panel published no score. */
  score: number | null;
  /** Score exactly as printed, e.g. `14,5` (Swedish decimal comma). */
  scoreLabel: string | null;
  scale: ScoreScale;
  /** Band the score falls into, derived from `score`. */
  band: QualityBand | null;
  valueRating: ValueRating | null;
  /** Value wording as printed, e.g. `Fynd i sin prisklass`. */
  valueLabel: string | null;
  /** True when flagged "Druv- eller distrikttypisk". */
  typical: boolean;
  /** Full tasting note ("Bedömning"). */
  tastingNote: string | null;
  /** Absolute URL of the individual review page. */
  url: string | null;
}

/** How a wine was matched to a Systembolaget catalog entry. */
export type MatchMethod =
  /** Article number printed directly in the Munskänkarna review. */
  | 'artikelnummer'
  /** Resolved by searching the catalog for name + producer. */
  | 'name-search'
  /** No catalog entry could be determined. */
  | 'unmatched';

/** Outcome of the optional live metadata fetch. */
export type EnrichmentStatus =
  | 'enriched'
  | 'skipped'
  | 'not-found'
  | 'unavailable'
  | 'error';

/** The Systembolaget side of the match. */
export interface SystembolagetInfo {
  /** Article number ("artikelnummer"), e.g. `9049001`. */
  articleNumber: string | null;
  /** Canonical product URL, or null when there is no article number. */
  productUrl: string | null;
  matchMethod: MatchMethod;
  enrichment: EnrichmentStatus;
  /** Fields below are populated only when live enrichment succeeds. */
  productName: string | null;
  priceSek: number | null;
  volumeMl: number | null;
  alcoholPercent: number | null;
  country: string | null;
  region: string | null;
  /** e.g. `Tillfälligt sortiment`, as Systembolaget classifies it. */
  assortmentText: string | null;
  /** Sustainability/organic markers, when reported. */
  organic: boolean | null;
  /** ISO timestamp of the last successful enrichment. */
  fetchedAt: string | null;
}

/** One reviewed wine: the join of a Munskänkarna review and a catalog entry. */
export interface Wine {
  /** Stable id: `<releaseId>/<wine-slug>`. */
  id: string;
  /** Name with the vintage stripped, e.g. `Brut Nature Gran Reserva`. */
  name: string;
  /** Name exactly as published, e.g. `Brut Nature Gran Reserva 2017`. */
  fullName: string;
  /** Secondary heading, when the review carries one. */
  subtitle: string | null;
  /** Four-digit vintage, or null for non-vintage wines. */
  vintage: number | null;
  producer: string | null;
  importer: string | null;

  color: WineColor;
  /** Swedish label as published, e.g. `Mousserande vin`. */
  colorLabel: string;

  country: string | null;
  region: string | null;
  /** Appellation/denomination, e.g. `Cava`, `Cannonau di Sardegna`. */
  appellation: string | null;
  grapes: string[];

  /** Price in SEK as printed by Munskänkarna. */
  priceSek: number | null;
  volumeMl: number | null;
  alcoholPercent: number | null;
  /** Computed: price normalised to SEK per litre. Null when either input is. */
  pricePerLitre: number | null;

  review: MunskankarnaReview;
  systembolaget: SystembolagetInfo;

  releaseId: string;
}

/** The persisted store written by `npm run sync:wines`. */
export interface WineStore {
  /** Schema version, bumped when the shape changes incompatibly. */
  version: 1;
  /** ISO timestamp of the sync that produced this file. */
  generatedAt: string;
  /** Where the data came from — real sync, or the bundled fixture. */
  source: 'munskankarna' | 'fixture';
  releases: Release[];
  wines: Wine[];
}

/** Per-release ingestion outcome, reported by the sync CLI. */
export interface IngestResult {
  release: Release;
  wines: Wine[];
  warnings: string[];
}
