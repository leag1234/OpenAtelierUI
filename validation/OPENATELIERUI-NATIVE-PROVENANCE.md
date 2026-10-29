# Optional native foreground launcher

This opt-in prototype prepares a private copy and starts one foreground Uvicorn process on loopback. It does not change the Docker lifecycle, install packages, create a service, or claim CPU/memory/network isolation. Ctrl-C is the direct stop control; the private workspace and pre-session database remain for manual reopen and comparison.

The launcher reuses the existing closed-tree validator, profile-schema classifier, session-epoch copy tool, and qualified environment flags from `openatelierui_instance.py`. It checks the source before and after copying, retains a pre-session database, merges the candidate static tree with the exact supplied frontend static tree, and records source, upload, pre-session, and prepared-database hashes. Only `WEBUI_SECRET_KEY` is accepted from the mode-0600 operator env file; the value is never included in the report.

Local tests mock the Python version response and final Uvicorn process. They execute the existing standard-library SQLite copy helper against a synthetic database but do not import or start the application, bind a listening port, or test a real venv. One initial test exposed that this host's Python did not satisfy the launcher’s required 3.11/3.12 range; the focused command was corrected to mock that version check rather than weakening the requirement.

The candidate checkout has no compiled `build/` directory. A future native qualification must provide the reviewed build and a preinstalled compatible venv, then verify startup, health, saved-work reopen, normal Ctrl-C exit, original-copy integrity, RSS/disk use, and failure cleanup on the intended Linux host. No native launch or UI render is claimed here.
