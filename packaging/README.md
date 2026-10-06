# Building the installer

The installer ships the source files in this repo as they are, plus:

- the official **embeddable Python** (pinned version and SHA-256 in `build.ps1`), and
- the exact packages in **`requirements-lock.txt`**, installed as wheels, and
- **`FatimaImageStudio.exe`**, a small launcher (`launcher/Launcher.cs`, compiled by the build with the C#
  compiler built into Windows) that loads the bundled Python into its own process and runs `-m studio`. Windows
  shows the app under its own name, icon and version, never as `pythonw.exe`. With no arguments it starts
  the tray app; any arguments go to `python -m studio` (e.g. `--tray --no-browser`, `--check`).

The engine (stable-diffusion.cpp), models and upscalers are not bundled. The app downloads them on its Setup
and Models pages, which keeps the installer at about 20 MB.

## Build locally

Needs Python 3.13 with pip and Pillow on PATH, and [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```bash
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The output is `dist\FatimaImageStudio-Setup-<version>.exe` plus a `.sha256` file. The staged app is in
`build\app`; `build\app\python\python.exe -m studio` runs it straight from there (as an installed copy, so
its data goes to `%LOCALAPPDATA%\Fatima Image Studio`).

## Release

1. Set `__version__` in `studio/__init__.py` (e.g. `1.0.1`) and commit.
2. Tag and push: `git tag v1.0.1` then `git push origin main v1.0.1`.
3. The **Release** workflow builds the installer on GitHub's Windows runner and publishes a release with the
   `.exe`, its `.sha256`, and generated notes. It refuses a tag that doesn't match `__version__`.

Run the workflow by hand (Actions → Release → Run workflow) to get a test build as a downloadable artifact
without publishing a release.

## Updating dependencies

Edit `requirements.txt`, install it into a clean Python 3.13 environment, then regenerate the lock file with the
exact versions installed (every package `pip freeze` lists, minus pip itself), and rebuild. The build's smoke
test fails if the bundled runtime can't import the app.

## How the installed app finds its data

The installer puts an `installed` marker file next to the code. With it, the app keeps settings, models and the
engine in `%LOCALAPPDATA%\Fatima Image Studio` and images in `Pictures\Fatima Image Studio`, so updates and
uninstalls never touch them. Without it (running from source), everything stays in the project folder.
