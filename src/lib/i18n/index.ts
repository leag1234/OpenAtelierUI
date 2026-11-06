import i18next from 'i18next';
import resourcesToBackend from 'i18next-resources-to-backend';
import LanguageDetector from 'i18next-browser-languagedetector';
import type { i18n as i18nType } from 'i18next';
import { writable } from 'svelte/store';
import languages from './locales/languages.json';
import { resolveLocale } from './locale';

const localeCodes = languages.map(({ code }) => code);
const catalogueLocale = (value: string) => resolveLocale(value, localeCodes);

const createI18nStore = (i18n: i18nType) => {
	const i18nWritable = writable(i18n);

	i18n.on('initialized', () => {
		i18nWritable.set(i18n);
	});
	i18n.on('loaded', () => {
		i18nWritable.set(i18n);
	});
	i18n.on('added', () => i18nWritable.set(i18n));
	i18n.on('languageChanged', () => {
		i18nWritable.set(i18n);
	});
	return i18nWritable;
};

const createIsLoadingStore = (i18n: i18nType) => {
	const isLoading = writable(false);

	// if loaded resources are empty || {}, set loading to true
	i18n.on('loaded', (resources) => {
		// console.log('loaded:', resources);
		Object.keys(resources).length !== 0 && isLoading.set(false);
	});

	// if resources failed loading, set loading to true
	i18n.on('failedLoading', () => {
		isLoading.set(true);
	});

	return isLoading;
};

export const initI18n = (defaultLocale?: string | undefined) => {
	let detectionOrder = defaultLocale
		? ['querystring', 'localStorage']
		: ['querystring', 'localStorage', 'navigator'];
	let fallbackDefaultLocale = defaultLocale ? [defaultLocale] : ['en-US'];

	const loadResource = (language: string, namespace: string) =>
		import(`./locales/${catalogueLocale(language)}/${namespace}.json`);

	i18next
		.use(resourcesToBackend(loadResource))
		.use(LanguageDetector)
		.init({
			debug: false,
			detection: {
				order: detectionOrder,
				caches: ['localStorage'],
				lookupQuerystring: 'lang',
				lookupLocalStorage: 'locale',
				convertDetectedLanguage: catalogueLocale
			},
			fallbackLng: {
				default: fallbackDefaultLocale
			},
			ns: 'translation',
			returnEmptyString: false,
			interpolation: {
				escapeValue: false // not needed for svelte as it escapes by default
			}
		});

	const lang = i18next?.language || defaultLocale || 'en-US';
	document.documentElement.setAttribute('lang', catalogueLocale(lang));
};

const i18n = createI18nStore(i18next);
const isLoadingStore = createIsLoadingStore(i18next);

const i18nReady = new Promise<void>((resolve) => {
	if (i18next.isInitialized) {
		resolve();
		return;
	}
	i18next.on('initialized', () => resolve());
});

export const waitForI18n = () => i18nReady;

// Stable, consumer-neutral browser bridge. Integrations should use the
// documented static module rather than depending on generated chunk names.
if (typeof window !== 'undefined') {
	const globalWindow = window as typeof window & {
		OpenAtelierUI?: { i18nStore?: typeof i18n; i18next?: i18nType };
	};
	globalWindow.OpenAtelierUI = {
		...globalWindow.OpenAtelierUI,
		i18nStore: i18n,
		i18next
	};
	window.dispatchEvent(new CustomEvent('openatelierui:i18n-ready'));
}

export const getLanguages = async () => {
	const languages = (await import(`./locales/languages.json`)).default;
	return languages;
};
export const changeLanguage = (lang: string) => {
	return i18next.changeLanguage(catalogueLocale(lang));
};

i18next.on('languageChanged', (lang) => {
	if (typeof document !== 'undefined') document.documentElement.lang = catalogueLocale(lang);
});

export default i18n;
export const isLoading = isLoadingStore;
