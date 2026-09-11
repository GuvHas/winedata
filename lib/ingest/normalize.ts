/**
 * Pure helpers that turn the strings printed on munskankarna.se into the typed
 * values in `types/wine.ts`. Kept free of I/O so they can be unit tested.
 */

import type {
  AssortmentKind,
  QualityBand,
  ScoreScale,
  ValueRating,
  WineColor,
} from '@/types/wine';

/** Collapse whitespace (including NBSP) and trim. */
export function clean(input: string | null | undefined): string {
  if (!input) return '';
  return input.replace(/[\u00a0\u202f]/g, ' ').replace(/\s+/g, ' ').trim();
}

/** `clean`, but returns null for empty strings. */
export function cleanOrNull(input: string | null | undefined): string | null {
  const value = clean(input);
  return value === '' ? null : value;
}

/**
 * Parse a Swedish-formatted score: `14,5` -> 14.5, `15` -> 15.
 * Returns null for anything that is not a plausible 0–20 score.
 */
export function parseScore(input: string | null | undefined): number | null {
  const text = clean(input).replace(',', '.');
  const match = text.match(/\d+(?:\.\d+)?/);
  if (!match) return null;
  const value = Number.parseFloat(match[0]);
  if (!Number.isFinite(value) || value < 0 || value > 20) return null;
  return value;
}

/** Parse `199:-`, `1 250 kr`, `159:50` -> number of SEK. */
export function parsePrice(input: string | null | undefined): number | null {
  const text = clean(input);
  if (!text) return null;
  // Strip thousands separators (space) but keep a decimal comma/colon group.
  const match = text.match(/(\d[\d\s]*)(?:[,:](\d{1,2}))?/);
  if (!match) return null;
  const whole = Number.parseInt(match[1].replace(/\s/g, ''), 10);
  if (!Number.isFinite(whole)) return null;
  // `159:-` uses `-` as the öre placeholder; only digits count as decimals.
  const fraction = match[2] ? Number.parseInt(match[2].padEnd(2, '0'), 10) / 100 : 0;
  const value = whole + fraction;
  return value > 0 ? Math.round(value * 100) / 100 : null;
}

/** Parse `75 cl`, `750 ml`, `1,5 l` -> millilitres. */
export function parseVolumeMl(input: string | null | undefined): number | null {
  const text = clean(input).toLowerCase();
  const match = text.match(/(\d+(?:[.,]\d+)?)\s*(cl|ml|l)\b/);
  if (!match) return null;
  const amount = Number.parseFloat(match[1].replace(',', '.'));
  if (!Number.isFinite(amount)) return null;
  const factor = match[2] === 'cl' ? 10 : match[2] === 'l' ? 1000 : 1;
  const ml = Math.round(amount * factor);
  return ml > 0 ? ml : null;
}

/** Parse `12% vol.`, `14 %`, `13,5%` -> percentage number. */
export function parseAlcohol(input: string | null | undefined): number | null {
  const text = clean(input);
  const match = text.match(/(\d+(?:[.,]\d+)?)\s*%/);
  if (!match) return null;
  const value = Number.parseFloat(match[1].replace(',', '.'));
  if (!Number.isFinite(value) || value <= 0 || value > 60) return null;
  return value;
}

/**
 * Split a published wine name into base name and vintage.
 * `Brut Nature Gran Reserva 2017` -> { name, vintage: 2017 }
 * Non-vintage wines keep the full name and a null vintage.
 */
export function splitVintage(fullName: string): { name: string; vintage: number | null } {
  const text = clean(fullName);
  // Only a trailing 4-digit year in a plausible range counts as a vintage, so
  // names like "Château Musar 1000" or "Cuvée 21" are left intact.
  const match = text.match(/^(.*?)[\s,]+((?:19|20)\d{2})$/);
  if (!match) return { name: text, vintage: null };
  const vintage = Number.parseInt(match[2], 10);
  const currentYear = new Date().getUTCFullYear();
  if (vintage < 1900 || vintage > currentYear + 2) return { name: text, vintage: null };
  const name = clean(match[1]);
  return name ? { name, vintage } : { name: text, vintage: null };
}

/** Map Munskänkarna's Swedish category label to a normalised colour. */
export function parseColor(label: string | null | undefined): WineColor {
  const text = clean(label).toLowerCase();
  if (!text) return 'other';
  if (text.includes('mousser') || text.includes('champagne')) return 'sparkling';
  if (text.includes('ros')) return 'rose';
  if (text.includes('rött') || text.includes('rott') || text.startsWith('röd')) return 'red';
  if (text.includes('vitt') || text.includes('vit ')) return 'white';
  if (text.includes('starkvin') || text.includes('special')) return 'fortified';
  if (text.includes('dessert') || text.includes('sött') || text.includes('sot')) return 'dessert';
  return 'other';
}

/** Map the printed value wording to a `ValueRating`. */
export function parseValueRating(label: string | null | undefined): ValueRating | null {
  const text = clean(label).toLowerCase();
  if (!text) return null;
  // Check the negative form first: "ej prisvärt" also contains "prisvärt".
  if (text.startsWith('ej ') || text.includes('inte prisvärd')) return 'ej-prisvart';
  if (text.includes('fynd')) return 'fynd';
  if (text.includes('mer än prisvärt') || text.includes('mer an prisvart')) {
    return 'mer-an-prisvart';
  }
  if (text.includes('prisvärt') || text.includes('prisvart')) return 'prisvart';
  return null;
}

/** Munskänkarna's published quality bands for the 20-point scale. */
export function scoreBand(score: number | null, scale: ScoreScale = 20): QualityBand | null {
  if (score === null) return null;
  const value = scale === 100 ? (score / 100) * 20 : score;
  if (value >= 18) return 'exceptionellt';
  if (value >= 15) return 'hogklassigt';
  if (value >= 12) return 'bra';
  if (value >= 9) return 'medelbra';
  return 'enkelt';
}

/** Price normalised to SEK per litre, for comparing across bottle sizes. */
export function pricePerLitre(priceSek: number | null, volumeMl: number | null): number | null {
  if (priceSek === null || volumeMl === null || volumeMl <= 0) return null;
  return Math.round((priceSek / volumeMl) * 1000 * 100) / 100;
}

const MONTHS: Record<string, number> = {
  januari: 1, februari: 2, mars: 3, april: 4, maj: 5, juni: 6,
  juli: 7, augusti: 8, september: 9, oktober: 10, november: 11, december: 12,
};

/**
 * Pull an ISO date out of a release title.
 * `Tillfälligt sortiment 11 september 2026` -> `2026-09-11`
 * `Ordervaror oktober 2026`                 -> `2026-10-01` (month precision)
 * `Temaprovning champagne 2025`             -> null
 */
export function parseReleaseDate(title: string): string | null {
  const text = clean(title).toLowerCase();
  const monthNames = Object.keys(MONTHS).join('|');

  const dayMatch = text.match(new RegExp(`(\\d{1,2})\\s+(${monthNames})\\s+(\\d{4})`));
  if (dayMatch) {
    const [, day, month, year] = dayMatch;
    return toIsoDate(Number(year), MONTHS[month], Number(day));
  }

  const monthMatch = text.match(new RegExp(`(${monthNames})\\s+(\\d{4})`));
  if (monthMatch) {
    const [, month, year] = monthMatch;
    return toIsoDate(Number(year), MONTHS[month], 1);
  }

  return null;
}

function toIsoDate(year: number, month: number, day: number): string | null {
  if (!Number.isFinite(year) || !month || !Number.isFinite(day)) return null;
  if (day < 1 || day > 31) return null;
  const date = new Date(Date.UTC(year, month - 1, day));
  // Reject impossible dates such as 31 february.
  if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  return date.toISOString().slice(0, 10);
}

/** Ordered so that more specific slugs are tested before generic ones. */
const ASSORTMENT_RULES: Array<{ match: RegExp; kind: AssortmentKind; label: string }> = [
  { match: /tillfalligt-sortiment|tillfälligt sortiment/, kind: 'tillfalligt-sortiment', label: 'Tillfälligt sortiment' },
  { match: /fast-sortiment|fast sortiment|ordinarie/, kind: 'fast-sortiment', label: 'Fast sortiment' },
  { match: /hitlist/, kind: 'hitlistan', label: 'Hitlista' },
  { match: /lokalt-och-smaskaligt|lokalt och småskaligt/, kind: 'lokalt-och-smaskaligt', label: 'Lokalt och småskaligt' },
  { match: /ordervaror|odervaror|bestallningssortiment|beställningssortiment/, kind: 'bestallningssortimentet', label: 'Ordervaror' },
  { match: /webbviner|webbhandlare/, kind: 'webbviner', label: 'Webbviner' },
  { match: /temaprovning|tema/, kind: 'temaprovning', label: 'Temaprovning' },
];

/** Classify a release from its slug and/or title. */
export function parseAssortmentKind(
  slugOrTitle: string,
): { kind: AssortmentKind; label: string } {
  const text = clean(slugOrTitle).toLowerCase();
  for (const rule of ASSORTMENT_RULES) {
    if (rule.match.test(text)) return { kind: rule.kind, label: rule.label };
  }
  return { kind: 'ovrigt', label: 'Övrigt' };
}

/** Lowercase ASCII slug, with Swedish characters folded. */
export function slugify(input: string): string {
  return clean(input)
    .toLowerCase()
    .replace(/[åä]/g, 'a')
    .replace(/ö/g, 'o')
    .replace(/é/g, 'e')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/** Article numbers are digits only; reject anything else. */
export function normalizeArticleNumber(input: string | null | undefined): string | null {
  const digits = clean(input).replace(/\D/g, '');
  if (digits.length < 3 || digits.length > 10) return null;
  return digits;
}
