'use client';

import { useId, useState } from 'react';
import { ChevronDown, Filter, RotateCcw, SlidersHorizontal } from 'lucide-react';
import type { ValueRating, Wine, WineColor } from '@/types/wine';
import type { Filters, SortKey } from '@/lib/filters';
import { PRICE_BRACKETS, countBy } from '@/lib/filters';
import { COLOR_LABELS, COLOR_ORDER, VALUE_LABELS, VALUE_ORDER } from '@/lib/display';
import { ColorDot } from './ColorDot';
import { FilterPill } from './FilterPill';

/**
 * Quick filters.
 *
 * Counts are computed against the release-scoped set rather than the fully
 * filtered set, so a pill always shows how many wines it would bring in and
 * never reads "0" merely because a sibling filter is active.
 */

interface FilterBarProps {
  /** Wines in scope for counting (release + search applied). */
  scope: Wine[];
  filters: Filters;
  onChange: (next: Partial<Filters>) => void;
  onReset: () => void;
  activeCount: number;
}

const SCORE_THRESHOLDS = [14, 15, 16];

export function FilterBar({ scope, filters, onChange, onReset, activeCount }: FilterBarProps) {
  const colorCounts = countBy<WineColor>(scope, (wine) => wine.color);
  const valueCounts = countBy<ValueRating>(scope, (wine) => wine.review.valueRating);

  const toggle = <T,>(list: T[], item: T): T[] =>
    list.includes(item) ? list.filter((entry) => entry !== item) : [...list, item];

  const panelId = useId();
  const [open, setOpen] = useState(false);

  return (
    <section aria-label="Filter">
      {/*
        On phones the four pill rows would occupy roughly half the viewport, so
        they collapse behind a toggle and only a summary line stays sticky.
        From `lg` up the panel is always shown and the toggle is hidden.
      */}
      <div className="flex items-center gap-2 lg:hidden">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls={panelId}
          className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-[13px] font-medium text-ink transition-colors hover:border-line-strong"
        >
          <Filter className="h-3.5 w-3.5" aria-hidden="true" />
          Filter
          {activeCount > 0 && (
            <span className="tabular rounded-full bg-claret-500 px-1.5 text-[11px] text-white">
              {activeCount}
            </span>
          )}
          <ChevronDown
            className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`}
            aria-hidden="true"
          />
        </button>

        {activeCount > 0 && (
          <button
            type="button"
            onClick={onReset}
            className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[13px] font-medium text-claret-600 transition-colors hover:bg-claret-50"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            Rensa
          </button>
        )}
      </div>

      <div
        id={panelId}
        className={`space-y-2.5 ${open ? 'mt-2.5 block' : 'hidden'} lg:mt-0 lg:block`}
      >
      {/* Wine type */}
      <PillRow label="Typ">
        {COLOR_ORDER.filter((color) => (colorCounts[color] ?? 0) > 0).map((color) => (
          <FilterPill
            key={color}
            label={COLOR_LABELS[color]}
            count={colorCounts[color] ?? 0}
            active={filters.colors.includes(color)}
            onToggle={() => onChange({ colors: toggle(filters.colors, color) })}
            adornment={<ColorDot color={color} />}
          />
        ))}
      </PillRow>

      {/* Value verdict */}
      <PillRow label="Prisvärdhet">
        {VALUE_ORDER.filter((rating) => (valueCounts[rating] ?? 0) > 0).map((rating) => (
          <FilterPill
            key={rating}
            label={VALUE_LABELS[rating]}
            count={valueCounts[rating] ?? 0}
            active={filters.values.includes(rating)}
            onToggle={() => onChange({ values: toggle(filters.values, rating) })}
          />
        ))}
      </PillRow>

      {/* Price brackets */}
      <PillRow label="Pris">
        {PRICE_BRACKETS.map((bracket) => {
          const count = scope.filter((wine) => {
            if (wine.priceSek === null) return false;
            return (
              wine.priceSek >= bracket.min &&
              (bracket.max === null || wine.priceSek < bracket.max)
            );
          }).length;

          return (
            <FilterPill
              key={bracket.id}
              label={bracket.label}
              count={count}
              active={filters.brackets.includes(bracket.id)}
              onToggle={() => onChange({ brackets: toggle(filters.brackets, bracket.id) })}
            />
          );
        })}
      </PillRow>

      {/* Score thresholds + catalog toggle */}
      <PillRow label="Betyg">
        {SCORE_THRESHOLDS.map((threshold) => (
          <FilterPill
            key={threshold}
            label={`${threshold}+`}
            count={scope.filter((wine) => (wine.review.score ?? -1) >= threshold).length}
            active={filters.minScore === threshold}
            // Clicking the active threshold clears it.
            onToggle={() =>
              onChange({ minScore: filters.minScore === threshold ? 0 : threshold })
            }
          />
        ))}
        <FilterPill
          label="Finns på Systembolaget"
          count={scope.filter((wine) => wine.systembolaget.articleNumber).length}
          active={filters.systembolagetOnly}
          onToggle={() => onChange({ systembolagetOnly: !filters.systembolagetOnly })}
        />
        {activeCount > 0 && (
          <button
            type="button"
            onClick={onReset}
            className="hidden shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] font-medium text-claret-600 transition-colors hover:bg-claret-50 lg:inline-flex"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            Rensa ({activeCount})
          </button>
        )}
      </PillRow>
      </div>
    </section>
  );
}

/**
 * One horizontally scrollable row of pills.
 * On mobile the row scrolls rather than wrapping, which keeps the filter
 * block a predictable height and avoids pushing the results down.
 */
function PillRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="hidden w-24 shrink-0 text-xs font-semibold tracking-wide text-ink-faint uppercase sm:block">
        {label}
      </span>
      <div
        className="flex gap-1.5 overflow-x-auto pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        role="group"
        aria-label={label}
      >
        {children}
      </div>
    </div>
  );
}

/** Sort control — a native select, which is the best mobile affordance. */
export function SortSelect({
  value,
  onChange,
}: {
  value: SortKey;
  onChange: (sort: SortKey) => void;
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-sm text-ink-muted">
      <SlidersHorizontal className="h-4 w-4 shrink-0" aria-hidden="true" />
      <span className="sr-only sm:not-sr-only">Sortera</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as SortKey)}
        className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink transition-colors hover:border-line-strong"
      >
        <option value="score">Högsta betyg</option>
        <option value="value">Bäst värde</option>
        <option value="price-asc">Lägsta pris</option>
        <option value="price-desc">Högsta pris</option>
        <option value="name">Namn A–Ö</option>
      </select>
    </label>
  );
}
