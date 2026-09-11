/**
 * Persistence for the wine store.
 *
 * The store is a single JSON file. `data/wines.json` is the live output of
 * `npm run sync:wines` and is gitignored; `seeds/sample-reviews.json` is the
 * committed fixture so a fresh clone runs with no network access.
 */

import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import type { Release, Wine, WineStore } from '@/types/wine';

export const DATA_PATH = resolve(process.cwd(), 'data/wines.json');
export const SEED_PATH = resolve(process.cwd(), 'seeds/sample-reviews.json');

/** An empty, well-formed store. */
export function emptyStore(source: WineStore['source'] = 'fixture'): WineStore {
  return {
    version: 1,
    generatedAt: new Date().toISOString(),
    source,
    releases: [],
    wines: [],
  };
}

/**
 * Read and validate a store file.
 * Returns null when the file is missing or structurally unusable, so callers
 * can fall back rather than crash.
 */
export async function readStore(path: string): Promise<WineStore | null> {
  let raw: string;
  try {
    raw = await readFile(path, 'utf8');
  } catch {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as unknown;
    return isStore(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * Load the best available store: the synced file if present, else the seed
 * fixture, else an empty store.
 */
export async function loadStore(): Promise<WineStore> {
  return (await readStore(DATA_PATH)) ?? (await readStore(SEED_PATH)) ?? emptyStore();
}

/** Write a store atomically, so a crashed sync cannot truncate the file. */
export async function writeStore(path: string, store: WineStore): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  const temp = `${path}.${process.pid}.tmp`;
  await writeFile(temp, `${JSON.stringify(store, null, 2)}\n`, 'utf8');
  await rename(temp, path);
}

/**
 * Merge freshly ingested releases into an existing store.
 *
 * A re-synced release replaces its previous wines wholesale, which keeps the
 * store consistent when a release is corrected upstream. Releases not touched
 * by this sync are preserved, so incremental syncs accumulate history.
 */
export function mergeStore(
  previous: WineStore,
  releases: Release[],
  wines: Wine[],
): WineStore {
  const refreshed = new Set(releases.map((release) => release.id));

  const releaseById = new Map<string, Release>();
  for (const release of previous.releases) releaseById.set(release.id, release);
  for (const release of releases) releaseById.set(release.id, release);

  const keptWines = previous.wines.filter((wine) => !refreshed.has(wine.releaseId));
  const allWines = [...keptWines, ...wines];

  // Drop releases that ended up with no wines and contribute nothing.
  const wineCounts = new Map<string, number>();
  for (const wine of allWines) {
    wineCounts.set(wine.releaseId, (wineCounts.get(wine.releaseId) ?? 0) + 1);
  }

  const mergedReleases = [...releaseById.values()]
    .map((release) => ({ ...release, wineCount: wineCounts.get(release.id) ?? 0 }))
    .filter((release) => release.wineCount > 0)
    .sort(compareReleases);

  const keptIds = new Set(mergedReleases.map((release) => release.id));

  return {
    version: 1,
    generatedAt: new Date().toISOString(),
    source: 'munskankarna',
    releases: mergedReleases,
    wines: allWines.filter((wine) => keptIds.has(wine.releaseId)),
  };
}

/** Newest release first; undated releases sort last, then alphabetically. */
export function compareReleases(a: Release, b: Release): number {
  if (a.date && b.date) {
    if (a.date !== b.date) return a.date < b.date ? 1 : -1;
  } else if (a.date) {
    return -1;
  } else if (b.date) {
    return 1;
  }
  return a.title.localeCompare(b.title, 'sv');
}

/** Structural validation — enough to trust the file, not a full schema check. */
function isStore(value: unknown): value is WineStore {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Partial<WineStore>;
  return (
    candidate.version === 1 &&
    typeof candidate.generatedAt === 'string' &&
    Array.isArray(candidate.releases) &&
    Array.isArray(candidate.wines)
  );
}
