/**
 * Presentation helpers: Swedish labels and formatting shared by both layouts.
 * Keeping them here stops the card and the table from drifting apart.
 */

import type { QualityBand, ValueRating, Wine, WineColor } from '@/types/wine';

export const COLOR_LABELS: Record<WineColor, string> = {
  red: 'Rött',
  white: 'Vitt',
  rose: 'Rosé',
  sparkling: 'Mousserande',
  fortified: 'Starkvin',
  dessert: 'Dessertvin',
  other: 'Övrigt',
};

/** Order used for the filter pills — most common styles first. */
export const COLOR_ORDER: WineColor[] = [
  'red', 'white', 'rose', 'sparkling', 'fortified', 'dessert', 'other',
];

export const VALUE_LABELS: Record<ValueRating, string> = {
  fynd: 'Fynd',
  'mer-an-prisvart': 'Mer än prisvärt',
  prisvart: 'Prisvärt',
  'ej-prisvart': 'Ej prisvärt',
};

/** Short forms for the dense desktop table. */
export const VALUE_SHORT: Record<ValueRating, string> = {
  fynd: 'Fynd',
  'mer-an-prisvart': 'Mer än prisv.',
  prisvart: 'Prisvärt',
  'ej-prisvart': 'Ej prisv.',
};

export const VALUE_ORDER: ValueRating[] = [
  'fynd', 'mer-an-prisvart', 'prisvart', 'ej-prisvart',
];

export const BAND_LABELS: Record<QualityBand, string> = {
  exceptionellt: 'Exceptionellt vin',
  hogklassigt: 'Högklassigt vin',
  bra: 'Bra till mycket bra vin',
  medelbra: 'Medelbra vin',
  enkelt: 'Enkelt vin',
};

/** Swedish decimal comma, e.g. 14.5 -> "14,5". */
export function formatScore(score: number | null): string {
  if (score === null) return '–';
  return score.toString().replace('.', ',');
}

/** e.g. 199 -> "199 kr"; öre are shown only when non-zero. */
export function formatPrice(price: number | null): string {
  if (price === null) return '–';
  const rounded = Math.round(price * 100) / 100;
  const text = Number.isInteger(rounded)
    ? rounded.toString()
    : rounded.toFixed(2).replace('.', ',');
  return `${text} kr`;
}

/** e.g. 750 -> "75 cl"; falls back to millilitres below 10 cl. */
export function formatVolume(volumeMl: number | null): string {
  if (volumeMl === null) return '–';
  if (volumeMl >= 1000 && volumeMl % 1000 === 0) return `${volumeMl / 1000} l`;
  if (volumeMl >= 100) return `${Math.round(volumeMl / 10)} cl`;
  return `${volumeMl} ml`;
}

export function formatAlcohol(percent: number | null): string {
  if (percent === null) return '–';
  return `${percent.toString().replace('.', ',')} %`;
}

/** e.g. 265.33 -> "265 kr/l" — rounded, since it is a comparison aid. */
export function formatPricePerLitre(value: number | null): string {
  if (value === null) return '–';
  return `${Math.round(value)} kr/l`;
}

/** "Frankrike · Bourgogne" — omits missing parts rather than showing gaps. */
export function formatOrigin(wine: Wine): string {
  return [wine.country, wine.region].filter(Boolean).join(' · ');
}

/** Swedish long date, e.g. "11 september 2026". */
export function formatDate(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('sv-SE', {
    day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC',
  }).format(date);
}

/** Relative freshness for the "senast uppdaterad" line. */
export function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const minutes = Math.round((Date.now() - then) / 60_000);
  if (minutes < 1) return 'nyss';
  if (minutes < 60) return `för ${minutes} min sedan`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `för ${hours} tim sedan`;
  const days = Math.round(hours / 24);
  if (days === 1) return 'igår';
  if (days < 31) return `för ${days} dagar sedan`;
  const months = Math.round(days / 30);
  return months <= 1 ? 'för en månad sedan' : `för ${months} månader sedan`;
}

/** `true` when the wine has a Systembolaget entry to link to. */
export function hasProductLink(wine: Wine): boolean {
  return Boolean(wine.systembolaget.productUrl);
}

/** Vintage, or the conventional "NV" for non-vintage bottlings. */
export function formatVintage(vintage: number | null): string {
  return vintage === null ? 'NV' : vintage.toString();
}
