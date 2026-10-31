# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Backward-compatible alias for the OpenAtelierUI lifecycle helper."""
try:
    from .openatelierui_instance import main, operate, start
except ImportError:
    from openatelierui_instance import main, operate, start

if __name__ == "__main__": raise SystemExit(main())
