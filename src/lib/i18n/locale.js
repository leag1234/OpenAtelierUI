// SPDX-License-Identifier: BSD-3-Clause
// Resolve language-only preferences to a catalogue that actually exists.
export function resolveLocale(value, available) {
	const requested = String(value || '').trim().replaceAll('_', '-');
	const exact = available.find((code) => code.toLowerCase() === requested.toLowerCase());
	if (exact) return exact;
	const language = requested.split('-')[0].toLowerCase();
	const preferred = { en: 'en-US', fr: 'fr-FR', pt: 'pt-PT', zh: 'zh-CN' }[language];
	if (preferred && available.includes(preferred)) return preferred;
	return available.find((code) => code.split('-')[0].toLowerCase() === language) || 'en-US';
}
