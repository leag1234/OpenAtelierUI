import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { renderNotices, scanPackages } from '../scripts/generate-third-party-notices.mjs';

const makePackage = (root, path, metadata, files = {}) => {
	const directory = join(root, 'node_modules', path);
	mkdirSync(directory, { recursive: true });
	writeFileSync(join(directory, 'package.json'), `${JSON.stringify(metadata)}\n`);
	for (const [name, content] of Object.entries(files)) {
		writeFileSync(join(directory, name), content);
	}
};

test('notice generation is deterministic, sorted and deduplicated', () => {
	const root = mkdtempSync(join(tmpdir(), 'openatelierui-notices-'));
	try {
		makePackage(root, 'zeta', { name: 'zeta', version: '1.0.0', license: 'MIT' }, {
			LICENSE: 'shared license text\n'
		});
		makePackage(root, '@scope/alpha', { name: '@scope/alpha', version: '2.0.0' }, {
			COPYING: 'shared license text\n'
		});
		makePackage(root, 'middle', { name: 'middle', version: '3.0.0', license: 'Apache-2.0' });
		const first = renderNotices(scanPackages(join(root, 'node_modules')));
		const second = renderNotices(scanPackages(join(root, 'node_modules')));
		assert.equal(first, second);
		assert.match(first, /Packages: 3/);
		assert.match(first, /Unique bundled license\/notice texts: 1/);
		assert.ok(first.indexOf('@scope/alpha@2.0.0') < first.indexOf('middle@3.0.0'));
		assert.ok(first.indexOf('middle@3.0.0') < first.indexOf('zeta@1.0.0'));
		assert.equal(first.match(/shared license text/g)?.length, 1);
	} finally {
		rmSync(root, { recursive: true, force: true });
	}
});

test('notice generation fails closed without a declaration or notice file', () => {
	const root = mkdtempSync(join(tmpdir(), 'openatelierui-notices-'));
	try {
		makePackage(root, 'unknown', { name: 'unknown', version: '1.0.0' });
		assert.throws(
			() => scanPackages(join(root, 'node_modules')),
			/no declared license or notice file/
		);
	} finally {
		rmSync(root, { recursive: true, force: true });
	}
});
