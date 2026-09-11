'use client';

import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, SearchX } from 'lucide-react';
import type { Release, WineStore } from '@/types/wine';
import {
  DEFAULT_FILTERS,
  type Filters,
  filterWines,
  searchIndex,
  sortWines,
} from '@/lib/filters';
import { FilterBar, SortSelect } from './FilterBar';
import { Header, ReleaseSummary } from './Header';
import { WineCard } from './WineCard';
import { WineTable } from './WineTable';
import { useStickyOffset } from '@/lib/useStickyOffset';

/**
 * The interactive shell.
 *
 * The entire store (a few hundred wines) is handed down from the server
 * component and filtered in memory, so typing and toggling are synchronous —
 * no fetch, no spinner, no layout shift.
 *
 * Both layouts are rendered and toggled with CSS breakpoints rather than a JS
 * media query, which avoids a hydration mismatch and a first-paint flash.
 */
/** Wines rendered before the "show more" threshold kicks in. */
const PAGE_SIZE = 40;

export function WineBrowser({ store }: { store: WineStore }) {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const stickyRef = useStickyOffset<HTMLDivElement>();

  // Keeps typing responsive: the input updates immediately, the (heavier)
  // list recompute is allowed to lag by a frame.
  const deferredSearch = useDeferredValue(filters.search);

  // Built once per store; reused by every keystroke.
  const indexes = useMemo(() => {
    const map = new Map<string, string>();
    for (const wine of store.wines) map.set(wine.id, searchIndex(wine));
    return map;
  }, [store.wines]);

  const releaseById = useMemo(() => {
    const map = new Map<string, Release>();
    for (const release of store.releases) map.set(release.id, release);
    return map;
  }, [store.releases]);

  /** Release + search applied, but not the pills — this is what pills count against. */
  const scope = useMemo(
    () =>
      filterWines(
        store.wines,
        {
          ...DEFAULT_FILTERS,
          releaseId: filters.releaseId,
          search: deferredSearch,
        },
        indexes,
      ),
    [store.wines, filters.releaseId, deferredSearch, indexes],
  );

  const visible = useMemo(() => {
    const filtered = filterWines(scope, { ...filters, releaseId: 'all', search: '' }, indexes);
    return sortWines(filtered, filters.sort);
  }, [scope, filters, indexes]);

  // Both layouts render every visible wine, so an unbounded list would put
  // hundreds of subtrees in the DOM at once. Showing a page at a time keeps
  // the initial render small; the full set is still filtered and sorted, so
  // counts and ordering always reflect everything that matched.
  const shown = useMemo(() => visible.slice(0, limit), [visible, limit]);

  // A new filter or sort should start back at the top of the list.
  const resultSignature = `${filters.releaseId}|${deferredSearch}|${filters.colors.join()}|${filters.values.join()}|${filters.brackets.join()}|${filters.minScore}|${filters.systembolagetOnly}|${filters.sort}`;
  const previousSignature = useRef(resultSignature);
  useEffect(() => {
    if (previousSignature.current !== resultSignature) {
      previousSignature.current = resultSignature;
      setLimit(PAGE_SIZE);
    }
  }, [resultSignature]);

  const activeCount =
    filters.colors.length +
    filters.values.length +
    filters.brackets.length +
    (filters.minScore > 0 ? 1 : 0) +
    (filters.systembolagetOnly ? 1 : 0);

  const update = (next: Partial<Filters>) => setFilters((current) => ({ ...current, ...next }));

  const resetPills = () =>
    update({ colors: [], values: [], brackets: [], minScore: 0, systembolagetOnly: false });

  const selectedRelease =
    filters.releaseId === 'all' ? null : (releaseById.get(filters.releaseId) ?? null);

  return (
    <>
      <Header
        releases={store.releases}
        releaseId={filters.releaseId}
        onReleaseChange={(releaseId) => update({ releaseId })}
        search={filters.search}
        onSearchChange={(search) => update({ search })}
        totalWines={store.wines.length}
      />

      <main className="mx-auto max-w-[1400px] px-3 pb-16 sm:px-5">
        {/* Sticky filter block, offset below the header. */}
        <div
          ref={stickyRef}
          className="sticky top-[var(--header-height)] z-20 -mx-3 border-b border-line bg-paper/95 px-3 py-3 backdrop-blur-md sm:-mx-5 sm:px-5"
        >
          <FilterBar
            scope={scope}
            filters={filters}
            onChange={update}
            onReset={resetPills}
            activeCount={activeCount}
          />
        </div>

        <div className="mt-4 space-y-4">
          <ReleaseSummary release={selectedRelease} />

          <div className="flex items-center justify-between gap-3">
            <p className="tabular text-sm text-ink-muted" aria-live="polite">
              <span className="font-semibold text-ink">{visible.length}</span>{' '}
              {visible.length === 1 ? 'vin' : 'viner'}
              {visible.length !== store.wines.length && (
                <span className="text-ink-faint"> av {store.wines.length}</span>
              )}
            </p>
            <SortSelect value={filters.sort} onChange={(sort) => update({ sort })} />
          </div>

          <div id="resultat" tabIndex={-1}>
            {visible.length === 0 ? (
              <EmptyState onReset={resetPills} hasFilters={activeCount > 0 || Boolean(filters.search)} />
            ) : (
              <>
                {/* Mobile: cards */}
                <div className="space-y-2.5 lg:hidden">
                  {shown.map((wine) => (
                    <WineCard key={wine.id} wine={wine} />
                  ))}
                </div>

                {/* Desktop: table */}
                <div className="hidden lg:block">
                  <WineTable
                    wines={shown}
                    sort={filters.sort}
                    onSortChange={(sort) => update({ sort })}
                  />
                </div>

                {shown.length < visible.length && (
                  <div className="mt-4 flex justify-center">
                    <button
                      type="button"
                      onClick={() => setLimit((value) => value + PAGE_SIZE * 2)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-4 py-2 text-sm font-medium text-ink transition-colors hover:border-line-strong"
                    >
                      <ChevronDown className="h-4 w-4" aria-hidden="true" />
                      Visa fler
                      <span className="tabular text-ink-faint">
                        ({visible.length - shown.length} kvar)
                      </span>
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </main>
    </>
  );
}

function EmptyState({ onReset, hasFilters }: { onReset: () => void; hasFilters: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-line-strong px-6 py-16 text-center">
      <SearchX className="h-8 w-8 text-ink-faint" aria-hidden="true" />
      <p className="mt-3 text-sm font-medium text-ink">Inga viner matchar</p>
      <p className="mt-1 max-w-sm text-[13px] text-ink-muted">
        {hasFilters
          ? 'Prova att ta bort ett filter eller söka på något annat.'
          : 'Den här provningen innehåller inga viner ännu.'}
      </p>
      {hasFilters && (
        <button
          type="button"
          onClick={onReset}
          className="mt-4 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:border-line-strong"
        >
          Rensa filter
        </button>
      )}
    </div>
  );
}
