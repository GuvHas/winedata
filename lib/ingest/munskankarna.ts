/**
 * Munskänkarna / Vinlocus scraper.
 *
 * Vinlocus is server-rendered, and each release listing page embeds the full
 * record for every wine it covers — score, price, value verdict, grapes,
 * producer, origin, tasting note and the Systembolaget article number. So one
 * request per release is enough; the per-wine detail pages are only fetched
 * when the caller explicitly asks for the few extra fields they add
 * (currently the importer).
 *
 * Every selector below has a text-based fallback, and a card that fails to
 * yield a name is skipped with a warning rather than aborting the sync.
 */

import * as cheerio from 'cheerio';
import type { AnyNode } from 'domhandler';
import type { IngestResult, Release, Wine } from '@/types/wine';
import type { HttpClient } from './http';
import { buildProductUrl } from './systembolaget';
import {
  clean,
  cleanOrNull,
  normalizeArticleNumber,
  parseAlcohol,
  parseAssortmentKind,
  parseColor,
  parsePrice,
  parseReleaseDate,
  parseScore,
  parseValueRating,
  parseVolumeMl,
  pricePerLitre,
  scoreBand,
  slugify,
  splitVintage,
} from './normalize';

export const DEFAULT_BASE_URL = 'https://www.munskankarna.se';

/** Link paths under /sv/vinlocus/ that are facets, not releases. */
const NON_RELEASE_SEGMENTS = new Set([
  'land', 'druva', 'importor', 'provningstyp', 'producent', 'argang', 'sok',
]);

type Cheerio$ = cheerio.CheerioAPI;

/** Resolve a possibly relative href against the site base. */
function absolute(href: string, baseUrl: string): string {
  try {
    return new URL(href, baseUrl).toString();
  } catch {
    return href;
  }
}

/**
 * Discover published releases from the "Provningstyper" index.
 *
 * The index groups release links under an `<h3>` per tasting type, so the
 * document is walked in order while tracking the most recent heading. That
 * heading is only a hint: `parseAssortmentKind` re-derives the kind from the
 * slug, which is stable even when the headings are reworded.
 */
export function parseReleaseIndex(html: string, baseUrl = DEFAULT_BASE_URL): Release[] {
  const $ = cheerio.load(html);
  const releases = new Map<string, Release>();
  let currentHeading = '';

  $('h3, a[href*="/vinlocus/"]').each((_, element) => {
    const node = $(element);

    if (element.tagName?.toLowerCase() === 'h3') {
      currentHeading = clean(node.text());
      return;
    }

    const href = node.attr('href');
    if (!href) return;

    const slug = releaseSlugFromHref(href);
    if (!slug || releases.has(slug)) return;

    const title = clean(node.text()) || clean(node.attr('title'));
    // "Visa fler från …" links point at facet pages, already excluded by slug,
    // but they can also carry a release-shaped href in some layouts.
    if (!title || /^visa fler/i.test(title)) return;

    releases.set(slug, buildRelease(slug, title, currentHeading, baseUrl));
  });

  return [...releases.values()];
}

/** Extract a release slug from an href, or null if it is a facet/detail link. */
function releaseSlugFromHref(href: string): string | null {
  const path = href.split('?')[0].split('#')[0];
  const match = path.match(/\/sv\/vinlocus\/([^/]+)\/?$/);
  if (!match) return null;
  const slug = match[1];
  if (NON_RELEASE_SEGMENTS.has(slug)) return null;
  return slug;
}

function buildRelease(
  slug: string,
  title: string,
  headingHint: string,
  baseUrl: string,
): Release {
  // Prefer the slug for classification; fall back to the title, then the
  // surrounding heading, so a reworded slug still lands in the right bucket.
  let classified = parseAssortmentKind(slug);
  if (classified.kind === 'ovrigt') classified = parseAssortmentKind(title);
  if (classified.kind === 'ovrigt' && headingHint) {
    classified = parseAssortmentKind(headingHint);
  }

  return {
    id: slug,
    title,
    kind: classified.kind,
    kindLabel: classified.label,
    date: parseReleaseDate(title) ?? parseReleaseDate(slug.replace(/-/g, ' ')),
    url: absolute(`/sv/vinlocus/${slug}`, baseUrl),
    summary: null,
    wineCount: 0,
  };
}

/**
 * Parse a release page into its wines.
 *
 * `release` supplies identity/title; the page itself supplies the editorial
 * summary and the wine cards.
 */
export function parseReleasePage(
  html: string,
  release: Release,
  baseUrl = DEFAULT_BASE_URL,
): IngestResult {
  const $ = cheerio.load(html);
  const warnings: string[] = [];
  const wines: Wine[] = [];
  const seenIds = new Set<string>();

  const enriched: Release = {
    ...release,
    title: clean($('h1').first().text()) || release.title,
    summary: cleanOrNull($('.c-wine-contentdescription').first().text())?.replace(
      /^Om provningen\s*/i,
      '',
    ) ?? null,
  };
  enriched.date = parseReleaseDate(enriched.title) ?? enriched.date;

  // Cards live in `ul#wine-bottles-list`; `li.wine-section` rows are colour
  // headings that apply to the cards following them, used as a fallback when a
  // card carries no category image of its own.
  let sectionLabel = '';
  const items = $('#wine-bottles-list > li');
  const cards = items.length > 0 ? items : $('.c-wine-info').parent();

  cards.each((_, element) => {
    const node = $(element);

    if (node.hasClass('wine-section')) {
      sectionLabel = clean(node.find('.cat-header').text() || node.text());
      return;
    }

    const card = node.hasClass('c-wine-info') ? node : node.find('.c-wine-info').first();
    if (card.length === 0) return;

    const wine = parseWineCard($, card, enriched, sectionLabel, baseUrl);
    if (!wine) {
      warnings.push(`Skipped an unparseable card in ${enriched.id}`);
      return;
    }
    // Guard against a wine appearing twice on the same page.
    if (seenIds.has(wine.id)) return;
    seenIds.add(wine.id);
    wines.push(wine);
  });

  if (wines.length === 0) {
    warnings.push(
      `No wines found on ${enriched.url} — the page markup may have changed, ` +
        'or the release may require an authenticated session.',
    );
  }

  enriched.wineCount = wines.length;
  return { release: enriched, wines, warnings };
}

/** Parse a single `.c-wine-info` card. Returns null when there is no name. */
function parseWineCard(
  $: Cheerio$,
  card: cheerio.Cheerio<AnyNode>,
  release: Release,
  sectionLabel: string,
  baseUrl: string,
): Wine | null {
  const titleLink = card.find('.c-wine-info__headings h3 a').first();
  const fullName =
    clean(titleLink.find('span').first().text()) ||
    clean(titleLink.text()) ||
    clean(card.find('h3').first().text());
  if (!fullName) return null;

  const reviewHref = titleLink.attr('href') ?? null;
  const { name, vintage } = splitVintage(fullName);

  const scoreLabel = cleanOrNull(card.find('.wine-points').first().text());
  const score = parseScore(scoreLabel);

  const priceSek = parsePrice(
    card.find('.c-wine-info__price, .c-wine-bottle__price').first().text(),
  );

  const valueLabel = cleanOrNull(
    card.find('.c-wine-info__stat[name="category"] span').first().text(),
  );

  const colorLabel =
    clean(card.find('.c-wine-info__catimg').first().attr('alt')) ||
    clean(card.find('.c-wine-info__catvolumealcohol .tooltiptext').first().text()) ||
    sectionLabel;

  // `<div class="…volumeAlocoholValues"><span>75 cl</span>,<span>12% vol.</span></div>`
  const measures = card
    .find('.c-wine-info__volumeAlocoholValues span')
    .map((_, el) => clean($(el).text()))
    .get();
  const volumeMl = parseVolumeMl(measures.find((m) => /\b(cl|ml|l)\b/i.test(m)) ?? null);
  const alcoholPercent = parseAlcohol(measures.find((m) => m.includes('%')) ?? null);

  const origin = parseOrigin($, card);
  const producer = cleanOrNull(card.find('.c-wine-info__producer span').first().text());

  const articleNumber = parseArticleNumber($, card);

  const grapes = card
    .find('a[href*="/vinlocus/druva/"]')
    .map((_, el) => clean($(el).text()).toLowerCase())
    .get()
    .filter((grape, index, all) => grape !== '' && all.indexOf(grape) === index);

  const tastingNote = cleanOrNull(card.find('.c-wine-info__text').first().text());

  const id = `${release.id}/${slugify(reviewHref?.split('/').pop() ?? fullName)}`;

  return {
    id,
    name,
    fullName,
    subtitle: cleanOrNull(card.find('.c-wine-info__headings h4').first().text()),
    vintage,
    producer,
    importer: null,
    color: parseColor(colorLabel),
    colorLabel: colorLabel || 'Okänd',
    country: origin.country,
    region: origin.region,
    appellation: origin.appellation,
    grapes,
    priceSek,
    volumeMl,
    alcoholPercent,
    pricePerLitre: pricePerLitre(priceSek, volumeMl),
    review: {
      score,
      scoreLabel,
      scale: 20,
      band: scoreBand(score),
      valueRating: parseValueRating(valueLabel),
      valueLabel,
      typical: card.find('.c-wine-info__typical').length > 0,
      tastingNote,
      url: reviewHref ? absolute(reviewHref, baseUrl) : null,
    },
    systembolaget: {
      articleNumber,
      productUrl: buildProductUrl(articleNumber),
      matchMethod: articleNumber ? 'artikelnummer' : 'unmatched',
      enrichment: 'skipped',
      productName: null,
      priceSek: null,
      volumeMl: null,
      alcoholPercent: null,
      country: null,
      region: null,
      assortmentText: null,
      organic: null,
      fetchedAt: null,
    },
    releaseId: release.id,
  };
}

/**
 * Origin row: `<span class="fi fi-es"></span><a>Spanien</a>, <a>Navarra</a>, Cava`
 * Country and region are links; whatever trails them is the appellation.
 */
function parseOrigin(
  $: Cheerio$,
  card: cheerio.Cheerio<AnyNode>,
): { country: string | null; region: string | null; appellation: string | null } {
  const row = card.find('.c-wine-info__faded, .c-wine-bottle__origin').first();
  if (row.length === 0) return { country: null, region: null, appellation: null };

  const links = row
    .find('a[href*="/vinlocus/land/"]')
    .map((_, el) => clean($(el).text()))
    .get()
    .filter(Boolean);

  // The appellation is the text after the final link, minus separators.
  const fullText = clean(row.text());
  let appellation: string | null = null;
  if (links.length > 0) {
    const lastLink = links[links.length - 1];
    const tailStart = fullText.lastIndexOf(lastLink);
    if (tailStart !== -1) {
      appellation = cleanOrNull(
        fullText.slice(tailStart + lastLink.length).replace(/^[,\s·|-]+/, ''),
      );
    }
  } else {
    appellation = cleanOrNull(fullText);
  }

  return {
    country: links[0] ?? null,
    region: links[1] ?? null,
    appellation,
  };
}

/** Article number from the Systembolaget link, or from adjacent text. */
function parseArticleNumber($: Cheerio$, card: cheerio.Cheerio<AnyNode>): string | null {
  const link = card.find('a[href*="systembolaget.se"]').first();
  if (link.length > 0) {
    const fromText = normalizeArticleNumber(link.find('span').first().text() || link.text());
    if (fromText) return fromText;

    const href = link.attr('href') ?? '';
    // Matches both https://systembolaget.se/9049001 and /produkt/vin/…/9049001/
    const fromHref = href.match(/(\d{4,10})\/?(?:[?#].*)?$/);
    if (fromHref) return normalizeArticleNumber(fromHref[1]);
  }

  // Fallback: the label without a link, e.g. "Systembolaget: 9049001".
  const labelled = clean(card.text()).match(/Systembolaget:?\s*(\d{4,10})/i);
  return labelled ? normalizeArticleNumber(labelled[1]) : null;
}

/**
 * Parse an individual review page. The listing already carries almost
 * everything; this adds the importer and acts as a fallback when a listing
 * card is incomplete.
 */
export function parseWineDetail(html: string): Partial<Wine> {
  const $ = cheerio.load(html);
  const rows = $('.c-wine-bottle__datarow');

  const valueOf = (label: string): string | null => {
    const row = rows
      .filter((_, el) => new RegExp(`^${label}`, 'i').test(clean($(el).find('strong').text())))
      .first();
    if (row.length === 0) return null;
    // Drop the <strong> label and keep the remaining text.
    const copy = row.clone();
    copy.find('strong').remove();
    return cleanOrNull(copy.text());
  };

  const detail: Partial<Wine> = {};

  const importer = valueOf('Importör');
  if (importer) detail.importer = importer;

  const producer = valueOf('Producent');
  if (producer) detail.producer = producer;

  const grapes = $('a[href*="/vinlocus/druva/"]')
    .map((_, el) => clean($(el).text()).toLowerCase())
    .get()
    .filter((grape, index, all) => grape !== '' && all.indexOf(grape) === index);
  if (grapes.length > 0) detail.grapes = grapes;

  // "Om vinet" lists Volym and Alkohol as <strong>-labelled paragraphs.
  const aboutText = clean($('#c-wine-bottle-desktop-other, .c-wine-bottle__greybox').text());
  const volumeMatch = aboutText.match(/Volym\s*([\d.,]+\s*(?:cl|ml|l)\b)/i);
  if (volumeMatch) {
    const volumeMl = parseVolumeMl(volumeMatch[1]);
    if (volumeMl !== null) detail.volumeMl = volumeMl;
  }
  const alcoholMatch = aboutText.match(/Alkohol\s*([\d.,]+\s*%)/i);
  if (alcoholMatch) {
    const alcoholPercent = parseAlcohol(alcoholMatch[1]);
    if (alcoholPercent !== null) detail.alcoholPercent = alcoholPercent;
  }

  return detail;
}

/** Fetch and parse the release index. */
export async function fetchReleaseIndex(
  client: HttpClient,
  baseUrl = DEFAULT_BASE_URL,
): Promise<Release[]> {
  const html = await client.getText(absolute('/sv/vinlocus/provningstyp', baseUrl));
  return parseReleaseIndex(html, baseUrl);
}

/** Fetch and parse one release page. */
export async function fetchRelease(
  client: HttpClient,
  release: Release,
  baseUrl = DEFAULT_BASE_URL,
): Promise<IngestResult> {
  const html = await client.getText(release.url);
  return parseReleasePage(html, release, baseUrl);
}

/** Fetch a wine's detail page and merge in the extra fields. */
export async function enrichFromDetail(client: HttpClient, wine: Wine): Promise<Wine> {
  if (!wine.review.url) return wine;
  const html = await client.getText(wine.review.url);
  const detail = parseWineDetail(html);
  const merged: Wine = { ...wine, ...detail };
  merged.pricePerLitre = pricePerLitre(merged.priceSek, merged.volumeMl);
  return merged;
}
