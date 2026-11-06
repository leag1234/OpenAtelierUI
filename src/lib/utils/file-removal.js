// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026 Gaël Duval

export const detachFileForRemoval = (files, index) => {
	if (!Array.isArray(files) || !Number.isInteger(index) || index < 0 || index >= files.length) {
		return { files, removed: null };
	}

	return {
		files: [...files.slice(0, index), ...files.slice(index + 1)],
		removed: files[index]
	};
};
