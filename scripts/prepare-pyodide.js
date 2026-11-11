const packages = [
	'micropip',
	'packaging',
	'requests',
	'beautifulsoup4',
	'numpy',
	'pandas',
	'matplotlib',
	'scikit-learn',
	'scipy',
	'regex',
	'sympy',
	'tiktoken',
	'seaborn',
	'pytz'
];

import { loadPyodide } from 'pyodide';
import { createHash } from 'crypto';
import { setGlobalDispatcher, ProxyAgent } from 'undici';
import { writeFile, readFile, copyFile, readdir, rmdir, mkdir } from 'fs/promises';

const licenseDocuments = [
	{
		source: 'licenses/third-party/PYODIDE-0.27.3-LICENSE.txt',
		target: 'PYODIDE-0.27.3-LICENSE.txt',
		sha256: '1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5'
	},
	{
		source: 'licenses/third-party/PYODIDE-JS-APACHE-2.0-LICENSE.txt',
		target: 'PYODIDE-JS-APACHE-2.0-LICENSE.txt',
		sha256: 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'
	},
	{
		source: 'licenses/third-party/PYODIDE-0.27.3-SOURCE.txt',
		target: 'PYODIDE-0.27.3-SOURCE.txt',
		sha256: 'de0695eeb599bb6a99d65284499cc6385bd6372624a43d16f75be8f8ae7fe8a6'
	},
	{
		source: 'licenses/third-party/CPYTHON-3.12.7-LICENSE.txt',
		target: 'CPYTHON-3.12.7-LICENSE.txt',
		sha256: '3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf'
	},
	{
		source: 'licenses/third-party/OPENBLAS-0.3.26-LICENSE.txt',
		target: 'OPENBLAS-0.3.26-LICENSE.txt',
		sha256: '190b5a9c8d9723fe958ad33916bd7346d96fab3c5ea90832bb02d854f620fcff'
	}
];

/**
 * Loading network proxy configurations from the environment variables.
 * And the proxy config with lowercase name has the highest priority to use.
 */
function initNetworkProxyFromEnv() {
	// we assume all subsequent requests in this script are HTTPS:
	// https://cdn.jsdelivr.net
	// https://pypi.org
	// https://files.pythonhosted.org
	const allProxy = process.env.all_proxy || process.env.ALL_PROXY;
	const httpsProxy = process.env.https_proxy || process.env.HTTPS_PROXY;
	const httpProxy = process.env.http_proxy || process.env.HTTP_PROXY;
	const preferedProxy = httpsProxy || allProxy || httpProxy;
	/**
	 * use only http(s) proxy because socks5 proxy is not supported currently:
	 * @see https://github.com/nodejs/undici/issues/2224
	 */
	if (!preferedProxy || !preferedProxy.startsWith('http')) return;
	let preferedProxyURL;
	try {
		preferedProxyURL = new URL(preferedProxy).toString();
	} catch {
		console.warn(`Invalid network proxy URL: "${preferedProxy}"`);
		return;
	}
	const dispatcher = new ProxyAgent({ uri: preferedProxyURL });
	setGlobalDispatcher(dispatcher);
	console.log(`Initialized network proxy "${preferedProxy}" from env`);
}

async function downloadPackages() {
	console.log('Setting up pyodide + micropip');

	let pyodide;
	try {
		pyodide = await loadPyodide({
			packageCacheDir: 'static/pyodide'
		});
	} catch (err) {
		console.error('Failed to load Pyodide:', err);
		return;
	}

	const packageJson = JSON.parse(await readFile('package.json'));
	const pyodideVersion = packageJson.dependencies.pyodide.replace('^', '');

	try {
		const pyodidePackageJson = JSON.parse(await readFile('static/pyodide/package.json'));
		const pyodidePackageVersion = pyodidePackageJson.version.replace('^', '');

		if (pyodideVersion !== pyodidePackageVersion) {
			console.log('Pyodide version mismatch, removing static/pyodide directory');
			await rmdir('static/pyodide', { recursive: true });
		}
	} catch (e) {
		console.log('Pyodide package not found, proceeding with download.');
	}

	try {
		console.log('Loading micropip package');
		await pyodide.loadPackage('micropip');

		const micropip = pyodide.pyimport('micropip');
		console.log('Downloading Pyodide packages:', packages);

		try {
			for (const pkg of packages) {
				console.log(`Installing package: ${pkg}`);
				await micropip.install(pkg);
			}
		} catch (err) {
			console.error('Package installation failed:', err);
			return;
		}

		console.log('Pyodide packages downloaded, freezing into lock file');

		try {
			const lockFile = await micropip.freeze();
			await writeFile('static/pyodide/pyodide-lock.json', lockFile);
		} catch (err) {
			console.error('Failed to write lock file:', err);
		}
	} catch (err) {
		console.error('Failed to load or install micropip:', err);
	}
}

async function copyPyodide() {
	console.log('Copying Pyodide files into static directory');
	// Copy all files from node_modules/pyodide to static/pyodide
	for await (const entry of await readdir('node_modules/pyodide')) {
		await copyFile(`node_modules/pyodide/${entry}`, `static/pyodide/${entry}`);
	}
}

async function copyPyodideLicenseBundle() {
	const targetDirectory = 'static/pyodide/licenses';
	await mkdir(targetDirectory, { recursive: true });
	for (const document of licenseDocuments) {
		const content = await readFile(document.source);
		const actual = createHash('sha256').update(content).digest('hex');
		if (actual !== document.sha256) {
			throw new Error(`${document.source}: expected SHA-256 ${document.sha256}, got ${actual}`);
		}
		await copyFile(document.source, `${targetDirectory}/${document.target}`);
	}
}

initNetworkProxyFromEnv();
await downloadPackages();
await copyPyodide();
await copyPyodideLicenseBundle();
