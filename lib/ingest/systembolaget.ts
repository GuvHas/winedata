/**
 * Systembolaget linking and (optional) metadata enrichment.
 *
 * Linking is pure string work and always available. Enrichment is best-effort:
 * Systembolaget's storefront and API are geo-restricted to Sweden and the API
 * needs a subscription key, so a sync running elsewhere — CI, a container, a
 * laptop abroad — must still succeed. Every failure mode is therefore recorded
 * on the wine as an `EnrichmentStatus` instead of throwing.
 *
 * Munskänkarna already publishes price, volume, alcohol, country and region,
 * so enrichment only ever adds detail; it is never required for a usable app.
 */

import type { EnrichmentStatus, Wine } from '@/types/wine';
import { HttpClient, HttpError } from './http';
import { normalizeArticleNumber, parseAlcohol, pricePerLitre } from './normalize';

export const PRODUCT_URL_BASE = 'https://www.systembolaget.se/produkt/vin';
export const DEFAULT_API_BASE = 'https://api-extern.systembolaget.se/sb-api-ecommerce/v1';

/**
 * Canonical product URL for an article number.
 * Systembolaget also resolves the short form `systembolaget.se/{nr}`, but the
 * `/produkt/vin/{nr}/` form is the stable, shareable one.
 */
export function buildProductUrl(articleNumber: string | null | undefined): string | null {
  const normalized = normalizeArticleNumber(articleNumber);
  return normalized ? `${PRODUCT_URL_BASE}/${normalized}/` : null;
}

/** Catalog search URL, used as a fallback when no article number is known. */
export function buildSearchUrl(query: string): string {
  return `https://www.systembolaget.se/sortiment/?q=${encodeURIComponent(query)}`;
}

/** Subset of the external API's product payload that this project consumes. */
interface ApiProduct {
  productId?: string;
  productNumber?: string;
  productNameBold?: string;
  productNameThin?: string;
  price?: number;
  volume?: number;
  alcoholPercentage?: number;
  country?: string;
  originLevel1?: string;
  originLevel2?: string;
  assortmentText?: string;
  isOrganic?: boolean;
}

export interface EnrichOptions {
  apiKey?: string | null;
  apiBase?: string;
  /** Reuse the ingestion client so throttling and retries are shared. */
  client: HttpClient;
  onLog?: (message: string) => void;
}

/**
 * Enrich one wine in place-ish (returns a new object).
 * Never throws: a failed lookup downgrades `enrichment` and returns the wine.
 */
export async function enrichWine(wine: Wine, options: EnrichOptions): Promise<Wine> {
  const { apiKey, client, apiBase = DEFAULT_API_BASE } = options;
  const log = options.onLog ?? (() => {});
  const articleNumber = wine.systembolaget.articleNumber;

  if (!apiKey) return withStatus(wine, 'skipped');
  if (!articleNumber) return withStatus(wine, 'skipped');

  try {
    const url = `${apiBase}/product/${encodeURIComponent(articleNumber)}`;
    const product = await client.getJson<ApiProduct>(url, {
      'Ocp-Apim-Subscription-Key': apiKey,
    });

    if (!product || typeof product !== 'object') return withStatus(wine, 'not-found');

    return applyProduct(wine, product);
  } catch (error) {
    if (error instanceof HttpError && error.status === 404) {
      return withStatus(wine, 'not-found');
    }
    // Geo-blocks surface as 403, or as a redirect to a "blocked" page.
    if (error instanceof HttpError && (error.status === 403 || error.status === 451)) {
      log(`  Systembolaget enrichment unavailable (HTTP ${error.status}) — link-only`);
      return withStatus(wine, 'unavailable');
    }
    log(`  Systembolaget lookup failed for ${articleNumber}: ${describe(error)}`);
    return withStatus(wine, 'error');
  }
}

/** Merge an API product into a wine, filling gaps without overwriting truth. */
function applyProduct(wine: Wine, product: ApiProduct): Wine {
  const productName =
    [product.productNameBold, product.productNameThin]
      .map((part) => (part ?? '').trim())
      .filter(Boolean)
      .join(' ') || null;

  const volumeMl = typeof product.volume === 'number' ? Math.round(product.volume) : null;
  const priceSek = typeof product.price === 'number' ? product.price : null;
  const alcoholPercent =
    typeof product.alcoholPercentage === 'number'
      ? product.alcoholPercentage
      : parseAlcohol(String(product.alcoholPercentage ?? ''));

  const merged: Wine = {
    ...wine,
    systembolaget: {
      ...wine.systembolaget,
      enrichment: 'enriched',
      productName,
      priceSek,
      volumeMl,
      alcoholPercent,
      country: product.country ?? null,
      region: product.originLevel1 ?? product.originLevel2 ?? null,
      assortmentText: product.assortmentText ?? null,
      organic: typeof product.isOrganic === 'boolean' ? product.isOrganic : null,
      fetchedAt: new Date().toISOString(),
    },
    // Munskänkarna's figures are the review of record; only fill blanks.
    priceSek: wine.priceSek ?? priceSek,
    volumeMl: wine.volumeMl ?? volumeMl,
    alcoholPercent: wine.alcoholPercent ?? alcoholPercent,
    country: wine.country ?? product.country ?? null,
    region: wine.region ?? product.originLevel1 ?? null,
  };

  merged.pricePerLitre = pricePerLitre(merged.priceSek, merged.volumeMl);
  return merged;
}

function withStatus(wine: Wine, enrichment: EnrichmentStatus): Wine {
  return { ...wine, systembolaget: { ...wine.systembolaget, enrichment } };
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
