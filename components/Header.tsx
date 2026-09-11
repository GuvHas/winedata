'use client';

import { Search, Wine as WineIcon, X } from 'lucide-react';
import type { Release } from '@/types/wine';
import { formatDate } from '@/lib/display';

/**
 * Sticky header: brand, release switcher and instant search.
 *
 * Its height is fixed via the `--header-height` token, which the table's
 * sticky column headers offset against — so nothing overlaps and nothing
 * shifts as the page loads.
 */

interface HeaderProps {
  releases: Release[];
  releaseId: string;
  onReleaseChange: (id: string) => void;
  search: string;
  onSearchChange: (value: string) => void;
  totalWines: number;
}

export function Header({
  releases,
  releaseId,
  onReleaseChange,
  search,
  onSearchChange,
  totalWines,
}: HeaderProps) {
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-paper/90 backdrop-blur-md">
      <div className="mx-auto flex h-[var(--header-height)] max-w-[1400px] items-center gap-2 px-3 sm:gap-3 sm:px-5">
        <a
          href="#resultat"
          className="sr-only-focusable rounded-md bg-claret-500 px-3 py-1.5 text-sm text-white"
        >
          Hoppa till resultaten
        </a>

        <div className="flex shrink-0 items-center gap-2">
          <WineIcon className="h-5 w-5 text-claret-500" aria-hidden="true" />
          <span className="hidden text-[15px] font-semibold tracking-tight text-ink sm:block">
            Vinbetyg
          </span>
        </div>

        {/* Release switcher */}
        <label className="min-w-0 flex-1 sm:flex-none">
          <span className="sr-only">Välj provning</span>
          <select
            value={releaseId}
            onChange={(event) => onReleaseChange(event.target.value)}
            className="w-full max-w-full truncate rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm font-medium text-ink transition-colors hover:border-line-strong sm:w-auto sm:max-w-[22rem]"
          >
            <option value="all">Alla provningar ({totalWines} viner)</option>
            {releases.map((release) => (
              <option key={release.id} value={release.id}>
                {release.title} ({release.wineCount})
              </option>
            ))}
          </select>
        </label>

        {/* Instant search */}
        <div className="relative ml-auto min-w-0 flex-1 sm:max-w-sm">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-ink-faint"
            aria-hidden="true"
          />
          <input
            type="search"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="Sök vin, producent, druva…"
            aria-label="Sök bland vinerna"
            // Native search clear varies by browser; a consistent button is drawn below.
            className="w-full rounded-lg border border-line bg-surface py-1.5 pr-8 pl-8 text-sm text-ink transition-colors placeholder:text-ink-faint hover:border-line-strong [&::-webkit-search-cancel-button]:hidden"
          />
          {search && (
            <button
              type="button"
              onClick={() => onSearchChange('')}
              className="absolute top-1/2 right-1.5 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded text-ink-faint transition-colors hover:text-ink"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="sr-only">Rensa sökningen</span>
            </button>
          )}
        </div>
      </div>
    </header>
  );
}

/** Contextual strip describing the selected release. */
export function ReleaseSummary({ release }: { release: Release | null }) {
  if (!release) return null;

  return (
    <div className="rounded-xl border border-line bg-surface-sunken px-4 py-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <h1 className="text-base font-semibold text-ink">{release.title}</h1>
        <span className="rounded-full bg-claret-50 px-2 py-0.5 text-[11px] font-medium text-claret-700">
          {release.kindLabel}
        </span>
        {release.date && (
          <span className="tabular text-xs text-ink-faint">{formatDate(release.date)}</span>
        )}
      </div>
      {release.summary && (
        <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-ink-muted">
          {release.summary}
        </p>
      )}
    </div>
  );
}
