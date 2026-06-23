# Repository Instructions

- After completing code, documentation, or build-artifact changes in this repository, run the relevant checks, create a git commit, and push it to the current upstream automatically unless the user explicitly asks not to commit or push.
- Before committing, inspect `git status` and stage only files related to the current task.
- For every task that modifies the software, update the application version number. Question-only tasks do not require a version update.
- The application version is stored in `APP_VERSION` in `main.py`; keep user-facing documentation such as `README.md` in sync when the displayed version changes.
- When the user asks to upload the program or a version to the remote GitHub project, publish it as a GitHub Releases asset, not as a file committed to the repository tree.
- Before uploading a release asset, compress `dist/COM串口调试工具.exe` into `Serial-port-debugging-tools.zip`, then upload that zip archive to the matching GitHub Release. Do not upload the `.exe` directly, and do not commit the release zip to the repository.
