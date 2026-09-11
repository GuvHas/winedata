/**
 * Parser tests run against HTML captured from munskankarna.se, so a change in
 * their markup shows up here rather than as an empty store after a sync.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import {
  parseReleaseIndex,
  parseReleasePage,
  parseWineDetail,
} from '../lib/ingest/munskankarna.ts';
import type { Release } from '../types/wine.ts';

const fixture = (name: string) =>
  readFileSync(join(process.cwd(), 'tests/fixtures', name), 'utf8');

function releaseStub(id: string, title: string): Release {
  return {
    id,
    title,
    kind: 'ovrigt',
    kindLabel: 'Övrigt',
    date: null,
    url: `https://www.munskankarna.se/sv/vinlocus/${id}`,
    summary: null,
    wineCount: 0,
  };
}

test('parseReleaseIndex finds releases and classifies them', () => {
  const releases = parseReleaseIndex(fixture('release-index.html'));
  assert.ok(releases.length >= 20, `expected many releases, got ${releases.length}`);

  const weekly = releases.find((r) => r.id === 'tillfalligt-sortiment-11-september-2026');
  assert.ok(weekly, 'the newest Tillfälligt sortiment release should be listed');
  assert.equal(weekly.kind, 'tillfalligt-sortiment');
  assert.equal(weekly.date, '2026-09-11');
  assert.equal(weekly.url, 'https://www.munskankarna.se/sv/vinlocus/tillfalligt-sortiment-11-september-2026');

  // Facet pages must never be mistaken for releases.
  for (const segment of ['land', 'druva', 'importor', 'provningstyp']) {
    assert.ok(
      !releases.some((r) => r.id === segment),
      `"${segment}" is a facet, not a release`,
    );
  }
});

test('parseReleasePage extracts complete wines from a listing', () => {
  const result = parseReleasePage(
    fixture('release-tillfalligt-sortiment.html'),
    releaseStub('tillfalligt-sortiment-11-september-2026', 'Tillfälligt sortiment 11 september 2026'),
  );

  // The fixture is trimmed to the first ten cards.
  assert.equal(result.wines.length, 10);
  assert.equal(result.release.date, '2026-09-11');
  assert.ok(result.release.summary?.includes('urval'), 'the editorial summary should be captured');

  const cava = result.wines.find((w) => w.name === 'Brut Nature Gran Reserva');
  assert.ok(cava, 'expected the Cava card to parse');
  assert.equal(cava.vintage, 2017);
  assert.equal(cava.review.score, 14);
  assert.equal(cava.review.scoreLabel, '14');
  assert.equal(cava.review.valueRating, 'mer-an-prisvart');
  assert.equal(cava.review.typical, true);
  assert.equal(cava.color, 'sparkling');
  assert.equal(cava.colorLabel, 'Mousserande vin');
  assert.equal(cava.producer, 'Heretat Mas Tinell, S.L.');
  assert.equal(cava.country, 'Spanien');
  assert.equal(cava.region, 'Navarra');
  assert.equal(cava.appellation, 'Cava');
  assert.deepEqual(cava.grapes, ['xarel-lo', 'macabeo', 'parellada']);
  assert.equal(cava.priceSek, 199);
  assert.equal(cava.volumeMl, 750);
  assert.equal(cava.alcoholPercent, 12);
  assert.equal(cava.pricePerLitre, 265.33);
  assert.ok(cava.review.tastingNote?.startsWith('Utvecklad doft'));

  // The Systembolaget match is the whole point of the join.
  assert.equal(cava.systembolaget.articleNumber, '9049001');
  assert.equal(cava.systembolaget.productUrl, 'https://www.systembolaget.se/produkt/vin/9049001/');
  assert.equal(cava.systembolaget.matchMethod, 'artikelnummer');

  // Every wine on this release is sold at Systembolaget.
  for (const wine of result.wines) {
    assert.ok(wine.systembolaget.articleNumber, `${wine.fullName} should carry an article number`);
    assert.ok(wine.review.score !== null, `${wine.fullName} should have a score`);
    assert.ok(wine.id.startsWith('tillfalligt-sortiment-11-september-2026/'));
  }

  // Ids must be unique, or React keys and the store would collide.
  const ids = result.wines.map((w) => w.id);
  assert.equal(new Set(ids).size, ids.length, 'wine ids should be unique');
});

test('parseReleasePage reads the smaller Hitlista layout', () => {
  const result = parseReleasePage(
    fixture('release-hitlista.html'),
    releaseStub('hitlista-3-september-2026', 'Hitlista 3 september 2026'),
  );

  assert.equal(result.wines.length, 5);
  assert.equal(result.release.summary, 'Fem fina fynd.');
  assert.equal(result.warnings.length, 0);

  const wine = result.wines.find((w) => w.name === 'Temjanika Luda Mara');
  assert.ok(wine);
  assert.equal(wine.review.score, 13.5);
  assert.equal(wine.review.valueRating, 'fynd');
  assert.equal(wine.country, 'Nordmakedonien');
  assert.equal(wine.systembolaget.articleNumber, '9339801');
});

test('parseReleasePage warns instead of throwing on unrecognised markup', () => {
  const result = parseReleasePage(
    '<html><body><h1>Tom provning</h1></body></html>',
    releaseStub('tom-provning', 'Tom provning'),
  );

  assert.equal(result.wines.length, 0);
  assert.equal(result.release.wineCount, 0);
  assert.ok(result.warnings.length > 0, 'an empty page should produce a warning');
});

test('parseWineDetail adds the importer that listings omit', () => {
  const detail = parseWineDetail(fixture('wine-detail.html'));

  assert.equal(detail.importer, 'Verissima AB');
  assert.equal(detail.producer, 'Az. Ferruccio Deiana');
  assert.deepEqual(detail.grapes, ['cannonau']);
  assert.equal(detail.volumeMl, 750);
  assert.equal(detail.alcoholPercent, 14);
});
