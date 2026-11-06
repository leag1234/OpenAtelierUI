// SPDX-License-Identifier: BSD-3-Clause
import assert from 'node:assert/strict';
import { resolveLocale } from '../src/lib/i18n/locale.js';

const available = ['en-US', 'fr-CA', 'fr-FR', 'de-DE'];
assert.equal(resolveLocale('fr', available), 'fr-FR');
assert.equal(resolveLocale('fr-CA', available), 'fr-CA');
assert.equal(resolveLocale('FR_fr', available), 'fr-FR');
assert.equal(resolveLocale('de', available), 'de-DE');
assert.equal(resolveLocale('zz', available), 'en-US');
console.log('PASS: language aliases, explicit region, casing and unknown fallback');
