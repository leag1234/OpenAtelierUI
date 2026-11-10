#!/usr/bin/env node

import { createHash } from 'node:crypto';
import {
	existsSync,
	mkdirSync,
	readdirSync,
	readFileSync,
	statSync,
	writeFileSync
} from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const LICENSE_PREFIXES = ['license', 'licence', 'copying', 'notice'];
const MAX_LICENSE_BYTES = 2 * 1024 * 1024;

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

const packageDirectories = (nodeModules) => {
	const found = [];
	const visit = (directory) => {
		if (!existsSync(directory)) return;
		for (const entry of readdirSync(directory, { withFileTypes: true })) {
			if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
			const child = join(directory, entry.name);
			if (entry.name.startsWith('@')) {
				for (const scoped of readdirSync(child, { withFileTypes: true })) {
					if (scoped.isDirectory()) found.push(join(child, scoped.name));
				}
			} else {
				found.push(child);
			}
		}
	};
	visit(nodeModules);
	for (let index = 0; index < found.length; index += 1) {
		visit(join(found[index], 'node_modules'));
	}
	return found;
};

const licenseFiles = (packageDirectory) =>
	readdirSync(packageDirectory, { withFileTypes: true })
		.filter((entry) => {
			if (!LICENSE_PREFIXES.some((prefix) => entry.name.toLowerCase().startsWith(prefix))) {
				return false;
			}
			try {
				return statSync(join(packageDirectory, entry.name)).isFile();
			} catch {
				return false;
			}
		})
		.map((entry) => entry.name)
		.sort();

const normalizedLicense = (metadata) => {
	const value = metadata.license ?? metadata.licenses ?? null;
	return value === null ? null : typeof value === 'string' ? value : JSON.stringify(value);
};

export const scanPackages = (nodeModules) => {
	const packages = new Map();
	const texts = new Map();
	for (const packageDirectory of packageDirectories(nodeModules)) {
		const metadataPath = join(packageDirectory, 'package.json');
		if (!existsSync(metadataPath)) continue;
		let metadata;
		try {
			metadata = JSON.parse(readFileSync(metadataPath, 'utf8'));
		} catch {
			continue;
		}
		if (!metadata.name || !metadata.version) continue;
		const declaredLicense = normalizedLicense(metadata);
		const textHashes = [];
		for (const filename of licenseFiles(packageDirectory)) {
			const content = readFileSync(join(packageDirectory, filename));
			if (content.length > MAX_LICENSE_BYTES) {
				throw new Error(`${metadata.name}@${metadata.version}: license file exceeds 2 MiB`);
			}
			const hash = sha256(content);
			textHashes.push(hash);
			const existing = texts.get(hash) ?? { content: content.toString('utf8'), filenames: new Set() };
			existing.filenames.add(filename);
			texts.set(hash, existing);
		}
		if (!declaredLicense && textHashes.length === 0) {
			throw new Error(`${metadata.name}@${metadata.version}: no declared license or notice file`);
		}
		const key = `${metadata.name}@${metadata.version}`;
		const existing = packages.get(key);
		if (existing) {
			for (const hash of textHashes) existing.textHashes.add(hash);
			if (!existing.declaredLicense && declaredLicense) existing.declaredLicense = declaredLicense;
		} else {
			packages.set(key, {
				name: metadata.name,
				version: metadata.version,
				declaredLicense,
				textHashes: new Set(textHashes)
			});
		}
	}
	return { packages, texts };
};

export const renderNotices = ({ packages, texts }) => {
	const sortedPackages = [...packages.values()].sort((left, right) =>
		`${left.name}@${left.version}`.localeCompare(`${right.name}@${right.version}`, 'en')
	);
	const textUsers = new Map();
	for (const item of sortedPackages) {
		for (const hash of item.textHashes) {
			const users = textUsers.get(hash) ?? [];
			users.push(`${item.name}@${item.version}`);
			textUsers.set(hash, users);
		}
	}
	const lines = [
		'OpenAtelierUI frontend third-party notices',
		'',
		'This deterministic file is generated from the exact npm installation used by the build.',
		'Third-party packages retain their own licenses; no package is relicensed by OpenAtelierUI.',
		'',
		`Packages: ${sortedPackages.length}`,
		`Unique bundled license/notice texts: ${texts.size}`,
		'',
		'Package index',
		'-------------'
	];
	for (const item of sortedPackages) {
		const declared = item.declaredLicense ?? '(license file only)';
		const hashes = [...item.textHashes].sort().map((hash) => `sha256:${hash}`).join(', ');
		lines.push(`${item.name}@${item.version} | declared: ${declared} | texts: ${hashes || '(none)'}`);
	}
	lines.push('', 'License and notice texts', '------------------------');
	for (const [hash, item] of [...texts.entries()].sort(([left], [right]) => left.localeCompare(right))) {
		lines.push(
			'',
			`sha256:${hash}`,
			`Packages: ${(textUsers.get(hash) ?? []).sort().join(', ')}`,
			`Source filenames: ${[...item.filenames].sort().join(', ')}`,
			'',
			item.content.trimEnd()
		);
	}
	return `${lines.join('\n')}\n`;
};

const parseArguments = (arguments_) => {
	const result = {};
	for (let index = 0; index < arguments_.length; index += 2) {
		result[arguments_[index]] = arguments_[index + 1];
	}
	if (!result['--node-modules'] || !result['--output']) {
		throw new Error('usage: --node-modules PATH --output PATH');
	}
	return result;
};

export const main = (arguments_) => {
	const options = parseArguments(arguments_);
	const rendered = renderNotices(scanPackages(resolve(options['--node-modules'])));
	const output = resolve(options['--output']);
	mkdirSync(dirname(output), { recursive: true });
	writeFileSync(output, rendered, 'utf8');
	return { output, sha256: sha256(rendered), bytes: Buffer.byteLength(rendered) };
};

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
	const result = main(process.argv.slice(2));
	process.stdout.write(`${JSON.stringify(result)}\n`);
}
