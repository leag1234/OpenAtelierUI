// SPDX-License-Identifier: BSD-3-Clause
export function availableIntegrations(config, user, servers = [], tools = [], selected = []) {
	if (!user) return false;
	if (servers.length || (tools || []).some((tool) => selected.includes(tool.id))) return true;
	return [
		['enable_web_search', 'web_search'],
		['enable_image_generation', 'image_generation'],
		['enable_code_interpreter', 'code_interpreter']
	].some(([feature, permission]) => config?.features?.[feature] &&
		(user.role === 'admin' || user.permissions?.features?.[permission]));
}
