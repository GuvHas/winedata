import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  parseAlcohol,
  parseAssortmentKind,
  parseColor,
  parsePrice,
  parseReleaseDate,
  parseScore,
  parseValueRating,
  parseVolumeMl,
  pricePerLitre,
  scoreBand,
  slugify,
  splitVintage,
  normalizeArticleNumber,
} from '../lib/ingest/normalize.ts';

test('parseScore handles the Swedish decimal comma', () => {
  assert.equal(parseScore('14,5'), 14.5);
  assert.equal(parseScore('15'), 15);
  assert.equal(parseScore(' 17,0 '), 17);
  assert.equal(parseScore(''), null);
  assert.equal(parseScore('abc'), null);
  // Out of the 0–20 range, so not a Munskänkarna score.
  assert.equal(parseScore('92'), null);
});

test('parsePrice reads the Swedish price formats', () => {
  assert.equal(parsePrice('199:-'), 199);
  assert.equal(parsePrice('1 250 kr'), 1250);
  assert.equal(parsePrice('159:50'), 159.5);
  assert.equal(parsePrice('37:-'), 37);
  assert.equal(parsePrice(''), null);
});

test('parseVolumeMl normalises to millilitres', () => {
  assert.equal(parseVolumeMl('75 cl'), 750);
  assert.equal(parseVolumeMl('750 ml'), 750);
  assert.equal(parseVolumeMl('1,5 l'), 1500);
  assert.equal(parseVolumeMl('62 cl'), 620);
  assert.equal(parseVolumeMl('okänd'), null);
});

test('parseAlcohol reads percentages', () => {
  assert.equal(parseAlcohol('12% vol.'), 12);
  assert.equal(parseAlcohol('13,5 %'), 13.5);
  assert.equal(parseAlcohol('14 %'), 14);
  assert.equal(parseAlcohol('inget'), null);
});

test('splitVintage separates a trailing vintage only when plausible', () => {
  assert.deepEqual(splitVintage('Brut Nature Gran Reserva 2017'), {
    name: 'Brut Nature Gran Reserva',
    vintage: 2017,
  });
  // Non-vintage bottlings keep their full name.
  assert.deepEqual(splitVintage('Saint-Saveur Cuvée Frédéric T Brut NV'), {
    name: 'Saint-Saveur Cuvée Frédéric T Brut NV',
    vintage: null,
  });
  // A number that is not a year must not be mistaken for one.
  assert.deepEqual(splitVintage('Cuvée 21'), { name: 'Cuvée 21', vintage: null });
  // A name that is nothing but a year stays intact.
  assert.deepEqual(splitVintage('2020'), { name: '2020', vintage: null });
});

test('parseColor maps the Swedish category labels', () => {
  assert.equal(parseColor('Rött vin'), 'red');
  assert.equal(parseColor('Vitt vin'), 'white');
  assert.equal(parseColor('Mousserande vin'), 'sparkling');
  assert.equal(parseColor('Rosévin'), 'rose');
  assert.equal(parseColor('Specialvin'), 'fortified');
  assert.equal(parseColor(''), 'other');
});

test('parseValueRating checks the negative form before the positive one', () => {
  assert.equal(parseValueRating('Fynd i sin prisklass'), 'fynd');
  assert.equal(parseValueRating('Mer än prisvärt'), 'mer-an-prisvart');
  assert.equal(parseValueRating('Prisvärt'), 'prisvart');
  // "Ej prisvärt" contains "prisvärt", so ordering matters here.
  assert.equal(parseValueRating('Ej prisvärt'), 'ej-prisvart');
  assert.equal(parseValueRating(''), null);
});

test('scoreBand follows the published 20-point bands', () => {
  assert.equal(scoreBand(18), 'exceptionellt');
  assert.equal(scoreBand(17.5), 'hogklassigt');
  assert.equal(scoreBand(15), 'hogklassigt');
  assert.equal(scoreBand(14.5), 'bra');
  assert.equal(scoreBand(12), 'bra');
  assert.equal(scoreBand(11.5), 'medelbra');
  assert.equal(scoreBand(8.5), 'enkelt');
  assert.equal(scoreBand(null), null);
});

test('pricePerLitre normalises across bottle sizes', () => {
  assert.equal(pricePerLitre(199, 750), 265.33);
  assert.equal(pricePerLitre(599, 620), 966.13);
  assert.equal(pricePerLitre(null, 750), null);
  assert.equal(pricePerLitre(199, null), null);
});

test('parseReleaseDate understands Swedish month names', () => {
  assert.equal(parseReleaseDate('Tillfälligt sortiment 11 september 2026'), '2026-09-11');
  assert.equal(parseReleaseDate('Ordervaror oktober 2026'), '2026-10-01');
  assert.equal(parseReleaseDate('Hitlista 3 september 2026'), '2026-09-03');
  // No month/year pair to read.
  assert.equal(parseReleaseDate('Temaprovning champagne'), null);
  // Impossible dates are rejected rather than rolled over.
  assert.equal(parseReleaseDate('31 februari 2026'), null);
});

test('parseAssortmentKind classifies release slugs', () => {
  assert.equal(parseAssortmentKind('tillfalligt-sortiment-11-september-2026').kind, 'tillfalligt-sortiment');
  assert.equal(parseAssortmentKind('fast-sortiment-nya-viner-1-september-2026').kind, 'fast-sortiment');
  assert.equal(parseAssortmentKind('hitlista-3-september-2026').kind, 'hitlistan');
  assert.equal(parseAssortmentKind('webbviner-oktober-2026').kind, 'webbviner');
  // A misspelled slug that exists upstream ("odervaror").
  assert.equal(parseAssortmentKind('odervaror-oktober-2026').kind, 'bestallningssortimentet');
  assert.equal(parseAssortmentKind('nagot-helt-annat').kind, 'ovrigt');
});

test('slugify folds Swedish characters', () => {
  assert.equal(slugify('Tillfälligt sortiment'), 'tillfalligt-sortiment');
  assert.equal(slugify('Château Mas Tinell'), 'chateau-mas-tinell');
  assert.equal(slugify('Rosé & Co.'), 'rose-co');
});

test('normalizeArticleNumber keeps only plausible digit strings', () => {
  assert.equal(normalizeArticleNumber('9049001'), '9049001');
  assert.equal(normalizeArticleNumber(' 258701 '), '258701');
  assert.equal(normalizeArticleNumber('ab'), null);
  assert.equal(normalizeArticleNumber(''), null);
});
