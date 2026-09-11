'use client';

import { ArrowDown, ArrowUp, ChevronsUpDown, Grape } from 'lucide-react';
import type { Wine } from '@/types/wine';
import type { SortKey } from '@/lib/filters';
import {
  COLOR_LABELS,
  formatAlcohol,
  formatPrice,
  formatPricePerLitre,
  formatVintage,
  formatVolume,
} from '@/lib/display';
import { ColorDot } from './ColorDot';
import { ScoreBadge } from './ScoreBadge';
import { ValueBadge } from './ValueBadge';
import { SystembolagetLink } from './SystembolagetLink';

/**
 * Desktop table.
 *
 * Dense and scannable: one row per wine, numeric columns right-aligned and
 * tabular so they compare down the column, and a sticky header that keeps the
 * column meanings visible through a long release.
 */

interface WineTableProps {
  wines: Wine[];
  sort: SortKey;
  onSortChange: (sort: SortKey) => void;
}

/** Columns whose header toggles a sort, and the key each one applies. */
const SORTABLE: Partial<Record<string, { key: SortKey; alt?: SortKey }>> = {
  score: { key: 'score' },
  value: { key: 'value' },
  wine: { key: 'name' },
  price: { key: 'price-asc', alt: 'price-desc' },
};

export function WineTable({ wines, sort, onSortChange }: WineTableProps) {
  const toggle = (column: keyof typeof SORTABLE) => {
    const config = SORTABLE[column];
    if (!config) return;
    // A second click on price flips the direction.
    if (config.alt && sort === config.key) onSortChange(config.alt);
    else onSortChange(config.key);
  };

  // `overflow-clip` rather than `overflow-hidden` below: both clip the rounded
  // corners, but `hidden` establishes a scroll container, which would make the
  // sticky <th> offsets resolve against that box instead of the viewport and
  // push the header down into the rows.
  return (
    <div className="overflow-clip rounded-xl border border-line bg-surface">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">
          Bedömda viner med betyg, prisvärdhet, pris och länk till Systembolaget.
          Kolumnrubriker med knapp går att sortera på.
        </caption>
        <thead>
          <tr className="border-b border-line bg-surface-sunken text-left">
            <Th
              className="w-20 text-center"
              sorted={sortStateFor('score', sort)}
              onClick={() => toggle('score')}
            >
              Betyg
            </Th>
            <Th
              className="w-32"
              sorted={sortStateFor('value', sort)}
              onClick={() => toggle('value')}
            >
              Prisvärdhet
            </Th>
            <Th sorted={sortStateFor('wine', sort)} onClick={() => toggle('wine')}>
              Vin
            </Th>
            <Th className="w-16 text-center">Årgång</Th>
            <Th className="w-36">Ursprung</Th>
            <Th
              className="w-28 text-right"
              sorted={sortStateFor('price', sort)}
              onClick={() => toggle('price')}
            >
              Pris
            </Th>
            <Th className="w-20 text-right">Jämförpris</Th>
            <Th className="w-40">Systembolaget</Th>
          </tr>
        </thead>

        <tbody>
          {wines.map((wine) => (
            <tr
              key={wine.id}
              className="border-b border-line/70 transition-colors last:border-0 hover:bg-surface-sunken/60"
            >
              <td className="px-3 py-2.5 text-center align-middle">
                <ScoreBadge score={wine.review.score} band={wine.review.band} />
              </td>

              <td className="px-3 py-2.5 align-middle">
                <ValueBadge rating={wine.review.valueRating} short />
              </td>

              <td className="px-3 py-2.5 align-middle">
                <div className="font-medium text-ink">{wine.name}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-ink-muted">
                  {wine.producer && <span className="truncate">{wine.producer}</span>}
                  {wine.grapes.length > 0 && (
                    <span className="inline-flex items-center gap-1 text-ink-faint">
                      <Grape className="h-3 w-3 shrink-0" aria-hidden="true" />
                      <span className="max-w-[22ch] truncate">{wine.grapes.join(', ')}</span>
                    </span>
                  )}
                  {wine.review.typical && (
                    <span
                      className="text-ink-faint"
                      title="Druv- eller distrikttypisk"
                    >
                      · typisk
                    </span>
                  )}
                </div>
              </td>

              <td className="tabular px-3 py-2.5 text-center align-middle text-ink-muted">
                {formatVintage(wine.vintage)}
              </td>

              <td className="px-3 py-2.5 align-middle">
                <div className="flex items-center gap-1.5">
                  <ColorDot color={wine.color} />
                  <span className="truncate text-ink">{wine.country ?? '–'}</span>
                </div>
                <div className="mt-0.5 truncate text-xs text-ink-faint">
                  {wine.region ?? COLOR_LABELS[wine.color]}
                </div>
              </td>

              <td className="tabular px-3 py-2.5 text-right align-middle font-medium text-ink whitespace-nowrap">
                {formatPrice(wine.priceSek)}
                <div className="text-xs font-normal text-ink-faint">
                  {formatVolume(wine.volumeMl)}
                  {wine.alcoholPercent !== null && ` · ${formatAlcohol(wine.alcoholPercent)}`}
                </div>
              </td>

              <td className="tabular px-3 py-2.5 text-right align-middle text-ink-muted whitespace-nowrap">
                {formatPricePerLitre(wine.pricePerLitre)}
              </td>

              <td className="px-3 py-2.5 align-middle">
                <SystembolagetLink wine={wine} variant="compact" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type SortState = 'none' | 'asc' | 'desc';

/** Map the active sort key onto a column's indicator state. */
function sortStateFor(column: string, sort: SortKey): SortState {
  switch (column) {
    case 'score':
      return sort === 'score' ? 'desc' : 'none';
    case 'value':
      return sort === 'value' ? 'desc' : 'none';
    case 'wine':
      return sort === 'name' ? 'asc' : 'none';
    case 'price':
      if (sort === 'price-asc') return 'asc';
      if (sort === 'price-desc') return 'desc';
      return 'none';
    default:
      return 'none';
  }
}

interface ThProps {
  children: React.ReactNode;
  className?: string;
  sorted?: SortState;
  onClick?: () => void;
}

function Th({ children, className = '', sorted, onClick }: ThProps) {
  // Parks directly beneath the sticky header + filter block. `--filters-height`
  // is measured at runtime, so this stays correct as the filter rows wrap.
  const base = `sticky top-[calc(var(--header-height)+var(--filters-height))] z-10 bg-surface-sunken px-3 py-2 text-xs font-semibold tracking-wide text-ink-muted uppercase ${className}`;

  if (!onClick) {
    return (
      <th scope="col" className={base}>
        {children}
      </th>
    );
  }

  const active = sorted && sorted !== 'none';
  const Icon = sorted === 'asc' ? ArrowUp : sorted === 'desc' ? ArrowDown : ChevronsUpDown;

  return (
    <th
      scope="col"
      className={base}
      // Communicates the current sort to assistive technology.
      aria-sort={sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : 'none'}
    >
      <button
        type="button"
        onClick={onClick}
        className={`inline-flex items-center gap-1 rounded transition-colors hover:text-ink ${
          active ? 'text-claret-600' : ''
        } ${className.includes('text-right') ? 'flex-row-reverse' : ''}`}
      >
        {children}
        <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
      </button>
    </th>
  );
}
