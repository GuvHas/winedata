import { Database, RefreshCw } from 'lucide-react';
import { WineBrowser } from '@/components/WineBrowser';
import { loadStore } from '@/lib/ingest/store';
import { formatRelative } from '@/lib/display';

/**
 * The store is read on the server at request time, so the browser receives the
 * data already embedded — first paint is complete and interactive with no
 * client-side fetch.
 */
export const dynamic = 'force-dynamic';

export default async function HomePage() {
  const store = await loadStore();

  if (store.wines.length === 0) {
    return <NoData />;
  }

  return (
    <>
      <WineBrowser store={store} />
      <DataFooter
        source={store.source}
        generatedAt={store.generatedAt}
        releaseCount={store.releases.length}
        wineCount={store.wines.length}
      />
    </>
  );
}

function DataFooter({
  source,
  generatedAt,
  releaseCount,
  wineCount,
}: {
  source: string;
  generatedAt: string;
  releaseCount: number;
  wineCount: number;
}) {
  return (
    <footer className="mx-auto max-w-[1400px] px-3 pb-10 sm:px-5">
      <div className="flex flex-col gap-1.5 border-t border-line pt-4 text-xs text-ink-faint sm:flex-row sm:items-center sm:justify-between">
        <p>
          Betyg och bedömningar från{' '}
          <a
            href="https://www.munskankarna.se/sv/vinlocus/"
            target="_blank"
            rel="noreferrer noopener"
            className="underline decoration-dotted underline-offset-2 hover:text-ink-muted"
          >
            Munskänkarna / Vinlocus
          </a>
          . Priser och artikelnummer avser Systembolaget.
        </p>
        <p className="tabular inline-flex items-center gap-1.5">
          {source === 'fixture' ? (
            <>
              <Database className="h-3.5 w-3.5" aria-hidden="true" />
              Exempeldata · kör <code className="font-mono">npm run sync:wines</code>
            </>
          ) : (
            <>
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              Uppdaterad {formatRelative(generatedAt)}
            </>
          )}
          <span aria-hidden="true">·</span>
          {wineCount} viner, {releaseCount} provningar
        </p>
      </div>
    </footer>
  );
}

/** Shown when neither a synced store nor the seed fixture is available. */
function NoData() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-lg flex-col items-center justify-center px-6 text-center">
      <Database className="h-10 w-10 text-ink-faint" aria-hidden="true" />
      <h1 className="mt-4 text-lg font-semibold text-ink">Ingen vindata hittades</h1>
      <p className="mt-2 text-sm text-ink-muted">
        Kör en synkronisering för att hämta de senaste bedömningarna från Munskänkarna:
      </p>
      <pre className="mt-4 w-full overflow-x-auto rounded-lg border border-line bg-surface px-4 py-3 text-left text-sm">
        <code className="font-mono">npm run sync:wines</code>
      </pre>
      <p className="mt-3 text-xs text-ink-faint">
        Exempeldata finns normalt i <code className="font-mono">seeds/sample-reviews.json</code>.
      </p>
    </main>
  );
}
