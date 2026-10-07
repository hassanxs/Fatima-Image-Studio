# Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org) *(applied for; until it is approved, releases are unsigned)*.

## Team roles

- Committers and reviewers: [Hassan (@hassanxs)](https://github.com/hassanxs)
- Approvers: [Hassan (@hassanxs)](https://github.com/hassanxs)

## What is signed

Every release is built by [GitHub Actions](.github/workflows/release.yml) from the tagged source in this
repository, and each signing request is approved by hand. Only this project's own files are signed: the
installer and `FatimaImageStudio.exe`. Bundled third-party components (Python and its packages) keep their
publishers' own signatures, or none.

## Privacy

This program will not transfer any information to other networked systems unless specifically requested by
the user or the person installing or operating it. See [Privacy](README.md#privacy) for details.
