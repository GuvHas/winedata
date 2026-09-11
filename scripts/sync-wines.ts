#!/usr/bin/env tsx
/**
 * Ingestion CLI — `npm run sync:wines`.
 *
 * Crawls Munskänkarna's Vinlocus release index, parses each release page into
 * typed wines, optionally enriches them from Systembolaget, and merges the
 * result into the JSON store.
 *
 * Usage:
 *   npm run sync:wines                      # newest release of every type
 *   npm run sync:wines -- --all             # every release on the index
 *   npm run sync:wines -- --latest          # newest release overall, per type
 *   npm run sync:wines -- --kind tillfalligt-sortiment --limit 3
 *   npm run sync:wines -- --release tillfalligt-sortiment-11-september-2026
 *   npm run sync:wines -- --details         # also fetch per-wine detail pages
 *   npm run sync:wines -- --out seeds/sample-reviews.json
 *   npm run sync:wines -- --dry-run
 */

import { resolve } from 'node:path';
import type { AssortmentKind, IngestResult, Release, Wine, WineStore } from '@/types/wine';
import { HttpClient } from '@/lib/ingest/http';
import {
  DEFAULT_BASE_URL,
  enrichFromDetail,
  fetchRelease,
  fetchReleaseIndex,
} from '@/lib/ingest/munskankarna';
import { enrichWine } from '@/lib/ingest/systembolaget';
import {
  DATA_PATH,
  compareReleases,
  emptyStore,
  mergeStore,
  readStore,
  writeStore,
} from '@/lib/ingest/store';

interface Options {
  all: boolean;
  latest: boolean;
  details: boolean;
  dryRun: boolean;
  kinds: AssortmentKind[];
  releaseIds: string[];
  limit: number | null;
  outPath: string;
}

function parseArgs(argv: string[]): Options {
  const options: Options = {
    all: false,
    latest: false,
    details: false,
    dryRun: false,
    kinds: [],
    releaseIds: [],
    limit: null,
    outPath: DATA_PATH,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => argv[++i];

    switch (arg) {
      case '--all': options.all = true; break;
      case '--latest': options.latest = true; break;
      case '--details': options.details = true; break;
      case '--dry-run': options.dryRun = true; break;
      case '--kind': options.kinds.push(next() as AssortmentKind); break;
      case '--release': options.releaseIds.push(next()); break;
      case '--limit': options.limit = Number.parseInt(next(), 10); break;
      case '--out': options.outPath = resolve(process.cwd(), next()); break;
      case '--help':
      case '-h':
        printHelp();
        process.exit(0);
        break;
      default:
        if (arg.startsWith('--')) {
          console.error(`Unknown flag: ${arg}\nRun with --help for usage.`);
          process.exit(1);
        }
    }
  }

  return options;
}

function printHelp(): void {
  console.log(`
Sync Munskänkarna wine reviews into the local store.

  --all                 Ingest every release listed on the index
  --latest              Ingest only the single newest release per tasting type
  --kind <kind>         Restrict to a tasting type (repeatable), e.g.
                        tillfalligt-sortiment, fast-sortiment, hitlistan,
                        lokalt-och-smaskaligt, bestallningssortimentet,
                        webbviner, temaprovning
  --release <slug>      Ingest one specific release (repeatable)
  --limit <n>           Cap the number of releases fetched
  --details             Also fetch each wine's detail page (adds importer;
                        one extra request per wine — use sparingly)
  --out <path>          Write somewhere other than data/wines.json
  --dry-run             Parse and report, but write nothing

Environment (see .env.example):
  MUNSKANKARNA_COOKIE     Session cookie for member-only pages
  MUNSKANKARNA_BASE_URL   Override the site base URL
  INGEST_DELAY_MS         Delay between requests (default 750)
  SYSTEMBOLAGET_API_KEY   Enables live catalog enrichment
`);
}

/** Pick which releases to fetch, given the index and the flags. */
function selectReleases(index: Release[], options: Options): Release[] {
  const sorted = [...index].sort(compareReleases);

  if (options.releaseIds.length > 0) {
    const wanted = new Set(options.releaseIds);
    const found = sorted.filter((release) => wanted.has(release.id));
    // Allow syncing a release that the index does not list.
    for (const id of options.releaseIds) {
      if (!found.some((release) => release.id === id)) {
        found.push(synthesizeRelease(id));
      }
    }
    return found;
  }

  let selected = sorted;

  if (options.kinds.length > 0) {
    const kinds = new Set(options.kinds);
    selected = selected.filter((release) => kinds.has(release.kind));
  }

  if (options.latest) {
    // Newest release of each tasting type.
    const seen = new Set<AssortmentKind>();
    selected = selected.filter((release) => {
      if (seen.has(release.kind)) return false;
      seen.add(release.kind);
      return true;
    });
  } else if (!options.all) {
    // Default: the two newest of each type — enough for a useful app without
    // hammering the site.
    const counts = new Map<AssortmentKind, number>();
    selected = selected.filter((release) => {
      const count = counts.get(release.kind) ?? 0;
      if (count >= 2) return false;
      counts.set(release.kind, count + 1);
      return true;
    });
  }

  if (options.limit !== null && Number.isFinite(options.limit)) {
    selected = selected.slice(0, Math.max(0, options.limit));
  }

  return selected;
}

/** Build a minimal Release for a slug supplied directly via --release. */
function synthesizeRelease(id: string): Release {
  const title = id.replace(/-/g, ' ');
  return {
    id,
    title,
    kind: 'ovrigt',
    kindLabel: 'Övrigt',
    date: null,
    url: `${baseUrl()}/sv/vinlocus/${id}`,
    summary: null,
    wineCount: 0,
  };
}

function baseUrl(): string {
  return (process.env.MUNSKANKARNA_BASE_URL || DEFAULT_BASE_URL).replace(/\/+$/, '');
}

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2));
  const base = baseUrl();
  const log = (message: string) => console.log(message);

  const client = new HttpClient({
    cookie: process.env.MUNSKANKARNA_COOKIE ?? null,
    delayMs: Number.parseInt(process.env.INGEST_DELAY_MS ?? '750', 10) || 750,
    onLog: log,
  });

  console.log(`Munskänkarna sync — ${base}`);
  console.log(
    client.authenticated
      ? 'Auth: session cookie supplied'
      : 'Auth: anonymous (public Vinlocus pages)',
  );

  const apiKey = process.env.SYSTEMBOLAGET_API_KEY?.trim() || null;
  console.log(
    apiKey
      ? 'Systembolaget: enrichment enabled'
      : 'Systembolaget: link-only (set SYSTEMBOLAGET_API_KEY to enrich)',
  );

  let index: Release[] = [];
  try {
    index = await fetchReleaseIndex(client, base);
    console.log(`\nFound ${index.length} releases on the index.`);
  } catch (error) {
    console.error(`\nCould not read the release index: ${describe(error)}`);
    if (options.releaseIds.length === 0) {
      console.error(
        'Nothing to do. Pass --release <slug> to sync a known release directly,\n' +
          'or run the app against seeds/sample-reviews.json.',
      );
      process.exitCode = 1;
      return;
    }
  }

  const selected = selectReleases(index, options);
  if (selected.length === 0) {
    console.error('No releases matched the given filters.');
    process.exitCode = 1;
    return;
  }

  console.log(`Ingesting ${selected.length} release(s):\n`);

  const results: IngestResult[] = [];
  const warnings: string[] = [];
  let failures = 0;

  for (const release of selected) {
    process.stdout.write(`  ${release.title} … `);
    try {
      const result = await fetchRelease(client, release, base);
      results.push(result);
      warnings.push(...result.warnings);
      console.log(`${result.wines.length} wines`);
    } catch (error) {
      failures += 1;
      console.log(`FAILED (${describe(error)})`);
      warnings.push(`${release.id}: ${describe(error)}`);
    }
  }

  let wines: Wine[] = results.flatMap((result) => result.wines);

  if (options.details && wines.length > 0) {
    console.log(`\nFetching ${wines.length} detail pages …`);
    const detailed: Wine[] = [];
    for (const wine of wines) {
      try {
        detailed.push(await enrichFromDetail(client, wine));
      } catch (error) {
        warnings.push(`detail ${wine.id}: ${describe(error)}`);
        detailed.push(wine);
      }
    }
    wines = detailed;
  }

  if (apiKey && wines.length > 0) {
    console.log(`\nEnriching ${wines.length} wines from Systembolaget …`);
    const enriched: Wine[] = [];
    for (const wine of wines) {
      enriched.push(await enrichWine(wine, { apiKey, client, onLog: log }));
    }
    wines = enriched;
    const ok = wines.filter((w) => w.systembolaget.enrichment === 'enriched').length;
    console.log(`  enriched ${ok}/${wines.length}`);
  }

  printSummary(results, wines, warnings);

  if (options.dryRun) {
    console.log('\n--dry-run: nothing written.');
    return;
  }

  const previous = (await readStore(options.outPath)) ?? emptyStore('munskankarna');
  const store: WineStore = mergeStore(
    previous,
    results.map((result) => result.release),
    wines,
  );

  await writeStore(options.outPath, store);
  console.log(
    `\nWrote ${store.wines.length} wines across ${store.releases.length} releases ` +
      `to ${relativize(options.outPath)}`,
  );

  if (failures > 0) process.exitCode = 1;
}

function printSummary(results: IngestResult[], wines: Wine[], warnings: string[]): void {
  const withArticle = wines.filter((wine) => wine.systembolaget.articleNumber).length;
  const withScore = wines.filter((wine) => wine.review.score !== null).length;
  const withPrice = wines.filter((wine) => wine.priceSek !== null).length;

  console.log('\nSummary');
  console.log(`  releases parsed     ${results.length}`);
  console.log(`  wines parsed        ${wines.length}`);
  console.log(`  with artikelnummer  ${withArticle}${pct(withArticle, wines.length)}`);
  console.log(`  with score          ${withScore}${pct(withScore, wines.length)}`);
  console.log(`  with price          ${withPrice}${pct(withPrice, wines.length)}`);

  if (warnings.length > 0) {
    console.log(`\nWarnings (${warnings.length}):`);
    for (const warning of warnings.slice(0, 10)) console.log(`  - ${warning}`);
    if (warnings.length > 10) console.log(`  … and ${warnings.length - 10} more`);
  }
}

function pct(part: number, total: number): string {
  if (total === 0) return '';
  return `  (${Math.round((part / total) * 100)}%)`;
}

function relativize(path: string): string {
  return path.startsWith(process.cwd()) ? path.slice(process.cwd().length + 1) : path;
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

main().catch((error) => {
  console.error(`\nSync failed: ${describe(error)}`);
  process.exitCode = 1;
});
