/**
 * Filtering, searching and sorting.
 *
 * Pure functions over an in-memory array. The whole store is a few hundred
 * wines, so every interaction is a synchronous recompute in the browser — no
 * request round-trip, no loading state, no layout shift.
 */

import type { ValueRating, Wine, WineColor } from '@/types/wine';

export type SortKey = 'score' | 'price-asc' | 'price-desc' | 'value' | 'name';

/** Price brackets in SEK. `max: null` means open-ended. */
export interface PriceBracket {
  id: string;
  label: string;
  min: number;
  max: number | null;
}

export const PRICE_BRACKETS: PriceBracket[] = [
  { id: 'under-100', label: 'Under 100 kr', min: 0, max: 100 },
  { id: '100-150', label: '100–150 kr', min: 100, max: 150 },
  { id: '150-250', label: '150–250 kr', min: 150, max: 250 },
  { id: '250-400', label: '250–400 kr', min: 250, max: 400 },
  { id: 'over-400', label: 'Över 400 kr', min: 400, max: null },
];

export interface Filters {
  /** Release id, or 'all'. */
  releaseId: string;
  search: string;
  colors: WineColor[];
  values: ValueRating[];
  brackets: string[];
  /** Minimum score on the 20-point scale; 0 disables the filter. */
  minScore: number;
  /** Only wines carrying a Systembolaget article number. */
  systembolagetOnly: boolean;
  sort: SortKey;
}

export const DEFAULT_FILTERS: Filters = {
  releaseId: 'all',
  search: '',
  colors: [],
  values: [],
  brackets: [],
  minScore: 0,
  systembolagetOnly: false,
  sort: 'score',
};

/** Value ratings ordered best-first, for the "Bäst värde" sort. */
const VALUE_ORDER: Record<ValueRating, number> = {
  fynd: 0,
  'mer-an-prisvart': 1,
  prisvart: 2,
  'ej-prisvart': 3,
};

/**
 * Fold Swedish characters and diacritics so that searching "chateau" matches
 * "Château" and "rose" matches "rosé".
 */
export function foldText(input: string): string {
  return input
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

/** Precomputed lowercase haystack per wine, so filtering stays cheap. */
export function searchIndex(wine: Wine): string {
  return foldText(
    [
      wine.fullName,
      wine.name,
      wine.producer,
      wine.importer,
      wine.country,
      wine.region,
      wine.appellation,
      wine.colorLabel,
      wine.systembolaget.articleNumber,
      wine.vintage?.toString(),
      ...wine.grapes,
    ]
      .filter(Boolean)
      .join(' '),
  );
}

/** Does this wine's price fall inside any of the selected brackets? */
function matchesBracket(wine: Wine, bracketIds: string[]): boolean {
  if (bracketIds.length === 0) return true;
  if (wine.priceSek === null) return false;

  return bracketIds.some((id) => {
    const bracket = PRICE_BRACKETS.find((candidate) => candidate.id === id);
    if (!bracket) return false;
    const aboveMin = wine.priceSek! >= bracket.min;
    const belowMax = bracket.max === null || wine.priceSek! < bracket.max;
    return aboveMin && belowMax;
  });
}

/**
 * Apply every active filter. Terms in the search box are ANDed, so
 * "rioja 2020" narrows rather than widens.
 */
export function filterWines(
  wines: Wine[],
  filters: Filters,
  indexes?: Map<string, string>,
): Wine[] {
  const terms = foldText(filters.search).split(' ').filter(Boolean);

  return wines.filter((wine) => {
    if (filters.releaseId !== 'all' && wine.releaseId !== filters.releaseId) return false;
    if (filters.colors.length > 0 && !filters.colors.includes(wine.color)) return false;

    if (filters.values.length > 0) {
      const rating = wine.review.valueRating;
      if (rating === null || !filters.values.includes(rating)) return false;
    }

    if (filters.minScore > 0) {
      if (wine.review.score === null || wine.review.score < filters.minScore) return false;
    }

    if (filters.systembolagetOnly && !wine.systembolaget.articleNumber) return false;
    if (!matchesBracket(wine, filters.brackets)) return false;

    if (terms.length > 0) {
      const haystack = indexes?.get(wine.id) ?? searchIndex(wine);
      if (!terms.every((term) => haystack.includes(term))) return false;
    }

    return true;
  });
}

/**
 * Sort a filtered list. Every comparator falls back to name so the order is
 * stable and never appears to shuffle between renders.
 */
export function sortWines(wines: Wine[], sort: SortKey): Wine[] {
  const sorted = [...wines];

  switch (sort) {
    case 'score':
      // Highest score first; break ties by better value, then cheaper.
      sorted.sort(
        (a, b) =>
          nullsLast(b.review.score, a.review.score) ||
          valueRank(a) - valueRank(b) ||
          nullsLast(a.priceSek, b.priceSek) ||
          byName(a, b),
      );
      break;

    case 'price-asc':
      sorted.sort(
        (a, b) => nullsLast(a.priceSek, b.priceSek) || byName(a, b),
      );
      break;

    case 'price-desc':
      sorted.sort((a, b) => nullsLast(b.priceSek, a.priceSek) || byName(a, b));
      break;

    case 'value':
      // Munskänkarna's own verdict leads, then score, then price per litre —
      // their panel judged value in context, which beats any ratio we invent.
      sorted.sort(
        (a, b) =>
          valueRank(a) - valueRank(b) ||
          nullsLast(b.review.score, a.review.score) ||
          nullsLast(a.pricePerLitre, b.pricePerLitre) ||
          byName(a, b),
      );
      break;

    case 'name':
      sorted.sort(byName);
      break;
  }

  return sorted;
}

/** Unrated wines sort after rated ones regardless of direction. */
function valueRank(wine: Wine): number {
  const rating = wine.review.valueRating;
  return rating === null ? 99 : VALUE_ORDER[rating];
}

/**
 * Compare two possibly-null numbers, always placing nulls last.
 * Direction is expressed by the argument order: pass `(a, b)` to sort
 * ascending and `(b, a)` to sort descending.
 */
function nullsLast(a: number | null, b: number | null): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return a - b;
}

function byName(a: Wine, b: Wine): number {
  return a.fullName.localeCompare(b.fullName, 'sv');
}

/** Counts shown on the filter pills, so users can see what a filter will do. */
export function countBy<T extends string>(
  wines: Wine[],
  key: (wine: Wine) => T | null,
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const wine of wines) {
    const value = key(wine);
    if (value === null) continue;
    counts[value] = (counts[value] ?? 0) + 1;
  }
  return counts;
}
