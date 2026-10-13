# Lavati Compare

A desktop comparison tool (Python + PySide6) for folders, text, merges, tables, binaries, pictures, media tags,
version info, the Windows registry, documents and Git revisions - with synchronisation, reports and scripting.

## Run
```
pip install -r requirements.txt            # optional extras: pip install -r requirements-optional.txt
python main.py                             # home screen
python main.py LEFT RIGHT                  # compare two files / folders / archives / URLs
python main.py A B C                       # three-way merge (ours, base, theirs)
python main.py --git REPO [REV_A [REV_B]]  # compare two revisions of a repository
python main.py --script job.lcs            # run a script with no window
python main.py --help
```
Drag and drop two items onto the window to compare them (three items open a merge).

## What it does
| Area | Features |
|---|---|
| **Folder Compare** | Two-pane tree with status colours (different, left newer, left older, left only, right only, identical); All / Differences / Same / Orphans and per-status filters; include / exclude patterns; compare by size + time, CRC32, byte-by-byte or text rules; copy, move, delete (Recycle Bin / Trash), rename, new folder, touch; flatten; select by status; manual alignment of differently named files; case-insensitive matching; zip / tar / gz / jar opened as folders |
| **Folder Sync** | Update right / left / both, Mirror either way; full preview of every action; backups (inside the destination or a folder you choose), Recycle Bin, saved sync sessions, export to .bat / .sh |
| **Text Compare** | Side-by-side or over/under; character-level highlights; edit in place with undo/redo; copy a difference either way; ignore whitespace / case / line endings; encoding and line-ending detection and conversion; unimportant-text and regex-replacement rules; syntax colours for ~35 languages; word wrap; manual line alignment; line-detail pane; unified patch view; diffs-only with N lines of context; find |
| **Merge** | Two- and three-way text merge with automatic merging, conflict navigation and per-change revert; two- and three-way **folder merge** |
| **Other compare types** | Table (CSV, Excel sheets, HTML tables, key column, numeric tolerance), Hex (large files), Picture (side by side, overlay, slider, blink, pixel-tolerance difference, zoom), Media (MP3 / FLAC / MP4 / AAC tags + audio fingerprint), Version (EXE / DLL), Registry (live or .reg), Document text (Word, PDF, OpenDocument, RTF, PowerPoint) |
| **Connectivity** | FTP, FTPS, SFTP, WebDAV, Amazon S3 (and compatible), Dropbox, OneDrive, Subversion, snapshots (saved file listings), Git |
| **Automation** | Saved sessions, per-file-type rules and converters, HTML / text / XML reports, scripting language, command line, Explorer / Linux file-manager context menus |
| **App** | Tabs, toolbar, menus, shortcuts (F1), light / dark / system themes, Windows, macOS and Linux |

## Git
*Level 1 - difftool / mergetool.* Settings > **Configure Git now** writes your global git config. It sets
```
difftool.lavati.cmd    = lavati "$LOCAL" "$REMOTE" --title1 "$MERGED (before)" --title2 "$MERGED (after)"
mergetool.lavati.cmd   = lavati --merge "$LOCAL" "$REMOTE" "$BASE" --output "$MERGED" --title1 ... --title2 ... --title3 ...
mergetool.lavati.trustExitCode = true
```
The process stays open until you close the window; a merge exits `0` only after the merged file was saved (otherwise `1`),
so git never accepts an unresolved conflict. `git difftool --dir-diff` opens straight into Folder Compare.

*Level 2 - built-in git browser* (File > Compare Git revisions, or the **Git Compare** card). Each side can be the working
tree, the staged index, HEAD, a branch, remote branch, tag, commit / revision (`main~3`, `v2.0`) or a stash. Trees are
read lazily with the installed git (honouring your credentials, hooks and LFS setup); changed files are found by comparing
blob ids, so no file is read until you open it. Renames are detected with `git -M`, a commit history picker supports
"commit vs parent" and "commit A vs commit B", and *three-way folder merge* uses `git merge-base` as the base. Needs git 2.30+.
Location syntax for paths and scripts: `git:<repo>#<revision>` (e.g. `git:C:\repo#HEAD~1`; revisions `WORKTREE`, `INDEX`).

*GitLens-style tools in Text Compare.* When a file is tracked by git, a **Git** button appears: **Show blame** adds a column
to each pane with the commit, author and age of every line (hover for the message, click to open the commit; it follows
your edits live), **File history** lists every commit that touched the file (compare any version with the current file, the
previous version, or another commit), **History of the selected lines** runs `git log -L`, and you can compare the file with
its HEAD or staged version. **Combine** keeps both sides of a difference (left lines first or right lines first), for one
difference or all of them.

## Scripting
```
set SRC="D:\work"
load "%SRC%" "E:\backup\work"
exclude "*.tmp; .git/; build/"
criteria timestamp
compare
folder-report output-to:"E:\backup\report-%DATE%.html" format=html show=differences
sync update:left->right backup=on trash=on
fail-if differences
```
Commands: `load exclude include criteria tolerance rules compare select copy move delete sync folder-report file-report snapshot log echo set fail-if exit`.
Run with `--script FILE` (exit code 0 ok, 1 differences or errors, 2 script error); Settings > Scripts & automation can edit,
run and schedule scripts. One-shot forms: `--sync MODE LEFT RIGHT` and `--report FORMAT OUTPUT LEFT RIGHT`.

## Locations
`ftp://user@host/dir`, `ftps://`, `sftp://` (needs paramiko; unknown host keys ask for confirmation), `dav://` / `davs://`,
`s3://bucket/prefix?region=...&endpoint=...`, `dropbox:///folder`, `onedrive:///folder` (OAuth access token), `svn://` or
`svn+https://...` (needs the svn client), `X.zip`, `X.lsnap` snapshots, `HKCU\Software\...` registry keys.
Passwords are held in memory only; they are never written to the settings file or sessions.

## Layout
- `main.py` - entry point / command line
- `lavati/engine.py` - folder, text, merge and table comparison (no Qt); `vfs.py`, `remote.py`, `gitvfs.py` - file systems
- `lavati/fsops.py` - copy / move / delete / sync plans; `script.py` - scripting; `reports.py`
- `lavati/editor.py`, `text_view.py`, `folder_view.py`, `merge_view.py`, `media_views.py`, `table_view.py`, `special_views.py`, `git_view.py`, `home.py`, `app.py` - UI
- `tests/` - `python -m unittest discover -s tests` (set `LAVATI_TESTDEPS` to a folder holding pyftpdlib / paramiko for the FTP and SFTP tests)

## Build
Windows: `build.bat` (PyInstaller, plus an installer when Inno Setup is present). macOS / Linux: `./build.sh`
(`lavati.spec`; on Apple Silicon the app is native arm64, `LAVATI_ARCH=universal2` for a universal build).

Settings and sessions live in `%APPDATA%\LavatiCompare` (`~/Library/Application Support/LavatiCompare`, `~/.config/LavatiCompare`).

## Safety
- Nothing is changed on disk until you copy, sync or save; deletes can go to the Recycle Bin and overwritten files can be backed up first.
- Downloads (portable Git) are verified against the publisher's SHA-256 before use; passwords are never written to disk;
  SFTP host keys must be confirmed; redirects never forward credentials to another host.
- The Windows installer is not code-signed yet, so SmartScreen may warn on first run. Maintainers can sign builds by
  setting `LAVATI_SIGNTOOL` before `build.bat` (see the comment there); open-source projects can apply for free signing
  through [SignPath Foundation](https://signpath.org/).

## License
Free and open source under the [MIT License](LICENSE.txt). Copyright (c) 2026 Lavati Softwares.
