import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import {
  DEFAULT_FILTERS,
  filterWines,
  foldText,
  searchIndex,
  sortWines,
} from '../lib/filters.ts';
import { mergeStore, compareReleases, emptyStore } from '../lib/ingest/store.ts';
import type { Release, WineStore } from '../types/wine.ts';

/** The committed fixture doubles as realistic test data. */
const store = JSON.parse(
  readFileSync(join(process.cwd(), 'seeds/sample-reviews.json'), 'utf8'),
) as WineStore;

const wines = store.wines;

test('the seed fixture is well-formed', () => {
  assert.ok(wines.length > 100, 'fixture should carry a useful number of wines');
  assert.equal(new Set(wines.map((w) => w.id)).size, wines.length, 'ids unique');
  for (const wine of wines) {
    assert.ok(wine.fullName.length > 0);
    assert.ok(store.releases.some((r) => r.id === wine.releaseId), 'wine belongs to a release');
    if (wine.systembolaget.articleNumber) {
      assert.match(wine.systembolaget.productUrl ?? '', /^https:\/\/www\.systembolaget\.se\/produkt\/vin\/\d+\/$/);
    } else {
      assert.equal(wine.systembolaget.productUrl, null);
    }
  }
});

test('foldText makes search accent- and case-insensitive', () => {
  assert.equal(foldText('Château'), 'chateau');
  assert.equal(foldText('Rosé & Co'), 'rose co');
  assert.equal(foldText('  Spätburgunder '), 'spatburgunder');
});

test('search matches across name, producer, grape, region and article number', () => {
  const sample = wines.find((w) => w.grapes.length > 0 && w.producer && w.country)!;
  const index = new Map(wines.map((w) => [w.id, searchIndex(w)]));

  const byGrape = filterWines(wines, { ...DEFAULT_FILTERS, search: sample.grapes[0] }, index);
  assert.ok(byGrape.some((w) => w.id === sample.id), 'grape search should match');

  const byProducer = filterWines(wines, { ...DEFAULT_FILTERS, search: sample.producer! }, index);
  assert.ok(byProducer.some((w) => w.id === sample.id), 'producer search should match');

  const withArticle = wines.find((w) => w.systembolaget.articleNumber)!;
  const byArticle = filterWines(
    wines,
    { ...DEFAULT_FILTERS, search: withArticle.systembolaget.articleNumber! },
    index,
  );
  assert.ok(byArticle.some((w) => w.id === withArticle.id), 'article number search should match');
});

test('multiple search terms narrow rather than widen', () => {
  const country = 'Frankrike';
  const one = filterWines(wines, { ...DEFAULT_FILTERS, search: country });
  const two = filterWines(wines, { ...DEFAULT_FILTERS, search: `${country} riesling` });
  assert.ok(two.length <= one.length, 'adding a term must not add results');
  for (const wine of two) {
    assert.equal(wine.country, country);
  }
});

test('filters compose and each one actually narrows', () => {
  const reds = filterWines(wines, { ...DEFAULT_FILTERS, colors: ['red'] });
  assert.ok(reds.length > 0);
  assert.ok(reds.every((w) => w.color === 'red'));

  const bargains = filterWines(wines, { ...DEFAULT_FILTERS, values: ['fynd'] });
  assert.ok(bargains.every((w) => w.review.valueRating === 'fynd'));

  const cheap = filterWines(wines, { ...DEFAULT_FILTERS, brackets: ['under-100'] });
  assert.ok(cheap.every((w) => w.priceSek !== null && w.priceSek < 100));

  const good = filterWines(wines, { ...DEFAULT_FILTERS, minScore: 15 });
  assert.ok(good.every((w) => (w.review.score ?? 0) >= 15));

  const stocked = filterWines(wines, { ...DEFAULT_FILTERS, systembolagetOnly: true });
  assert.ok(stocked.every((w) => w.systembolaget.articleNumber));
  assert.ok(stocked.length < wines.length, 'some reviewed wines are not sold at Systembolaget');

  // Combining must be an intersection.
  const combined = filterWines(wines, {
    ...DEFAULT_FILTERS,
    colors: ['red'],
    minScore: 15,
    systembolagetOnly: true,
  });
  assert.ok(combined.length <= Math.min(reds.length, good.length, stocked.length));
  assert.ok(
    combined.every((w) => w.color === 'red' && (w.review.score ?? 0) >= 15 && w.systembolaget.articleNumber),
  );
});

test('release filter partitions the set exactly', () => {
  let total = 0;
  for (const release of store.releases) {
    const subset = filterWines(wines, { ...DEFAULT_FILTERS, releaseId: release.id });
    assert.equal(subset.length, release.wineCount, `${release.id} count should match`);
    total += subset.length;
  }
  assert.equal(total, wines.length, 'releases should partition all wines');
});

test('price brackets do not overlap or drop wines', () => {
  const ids = new Set<string>();
  for (const bracket of ['under-100', '100-150', '150-250', '250-400', 'over-400']) {
    for (const wine of filterWines(wines, { ...DEFAULT_FILTERS, brackets: [bracket] })) {
      assert.ok(!ids.has(wine.id), `${wine.fullName} appears in two brackets`);
      ids.add(wine.id);
    }
  }
  const priced = wines.filter((w) => w.priceSek !== null).length;
  assert.equal(ids.size, priced, 'every priced wine falls into exactly one bracket');
});

test('sorting is correct and total', () => {
  const byScore = sortWines(wines, 'score');
  assert.equal(byScore.length, wines.length, 'sorting must not drop wines');
  for (let i = 1; i < byScore.length; i += 1) {
    const prev = byScore[i - 1].review.score ?? -Infinity;
    const curr = byScore[i].review.score ?? -Infinity;
    assert.ok(prev >= curr, 'scores descend');
  }

  const byPrice = sortWines(wines, 'price-asc');
  const pricedAsc = byPrice.filter((w) => w.priceSek !== null);
  for (let i = 1; i < pricedAsc.length; i += 1) {
    assert.ok(pricedAsc[i - 1].priceSek! <= pricedAsc[i].priceSek!, 'prices ascend');
  }
  // Wines without a price sort last, never first.
  const firstNullIndex = byPrice.findIndex((w) => w.priceSek === null);
  if (firstNullIndex !== -1) {
    assert.ok(byPrice.slice(firstNullIndex).every((w) => w.priceSek === null));
  }

  const byValue = sortWines(wines, 'value');
  const rank = { fynd: 0, 'mer-an-prisvart': 1, prisvart: 2, 'ej-prisvart': 3 } as const;
  for (let i = 1; i < byValue.length; i += 1) {
    const prev = byValue[i - 1].review.valueRating;
    const curr = byValue[i].review.valueRating;
    const prevRank = prev ? rank[prev] : 99;
    const currRank = curr ? rank[curr] : 99;
    assert.ok(prevRank <= currRank, 'value verdicts descend in attractiveness');
  }
});

test('sorting is stable and does not mutate its input', () => {
  const original = [...wines];
  const first = sortWines(wines, 'score').map((w) => w.id);
  const second = sortWines(wines, 'score').map((w) => w.id);
  assert.deepEqual(first, second, 'repeated sorts agree');
  assert.deepEqual(wines.map((w) => w.id), original.map((w) => w.id), 'input untouched');
});

test('mergeStore replaces a re-synced release and keeps the others', () => {
  const releaseA: Release = { ...store.releases[0], wineCount: 0 };
  const releaseB: Release = { ...store.releases[1], wineCount: 0 };
  const winesA = wines.filter((w) => w.releaseId === releaseA.id);
  const winesB = wines.filter((w) => w.releaseId === releaseB.id);

  const first = mergeStore(emptyStore(), [releaseA, releaseB], [...winesA, ...winesB]);
  assert.equal(first.wines.length, winesA.length + winesB.length);
  assert.equal(first.source, 'munskankarna');

  // Re-syncing A with a single wine should drop A's other wines but keep B.
  const second = mergeStore(first, [releaseA], [winesA[0]]);
  assert.equal(second.wines.filter((w) => w.releaseId === releaseA.id).length, 1);
  assert.equal(second.wines.filter((w) => w.releaseId === releaseB.id).length, winesB.length);
  assert.equal(second.releases.find((r) => r.id === releaseA.id)?.wineCount, 1);
});

test('compareReleases puts the newest first and undated last', () => {
  const make = (id: string, date: string | null): Release => ({
    ...store.releases[0], id, date, title: id,
  });
  const sorted = [
    make('old', '2026-01-01'),
    make('undated', null),
    make('new', '2026-09-11'),
  ].sort(compareReleases);
  assert.deepEqual(sorted.map((r) => r.id), ['new', 'old', 'undated']);
});
