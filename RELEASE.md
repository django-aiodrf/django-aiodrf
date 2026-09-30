# Release procedure

This document is for maintainers. A release publishes the wheel and source
distribution that the test workflow already built and verified for the commit
being released; publishing neither rebuilds the package nor reruns the tests.
Packages are uploaded to PyPI with trusted publishing, so no long-lived upload
token is needed.

## Branches

Work is merged into `dev`. A release goes from `dev` to `main` by pull request;
the push to `main` runs the full test workflow and builds the release
artifact, and the release tag is created on that `main` commit. Nothing is
committed to `main` directly.

## One-time setup steps

Before the first release, on GitHub ([django-aiodrf/django-aiodrf](https://github.com/django-aiodrf/django-aiodrf)):

- Create the `dev` branch from `main` and make `dev` the default branch.
- Protect `main`: changes only by pull request, the `quality-gate` check of
  the test workflow required, and merges restricted to the maintainers.
  Protect `dev` the same way, with the same check, without the restriction.
  The `main-pull-requests.yml` workflow closes pull requests into `main` that
  are not a maintainer's from `dev`.
- Require approval for the workflows of pull requests from forks, for all
  external contributors (Settings → Actions → General).
- Publish the documentation site: Settings → Pages → Source "GitHub Actions",
  and allow `main` to deploy to the `github-pages` environment. The
  `docs.yml` workflow publishes each push to `main` at
  <https://django-aiodrf.github.io/django-aiodrf/>.
- Restrict who can create version tags (`v*`) with a tag ruleset.
- Create the `pypi` deployment environment with required reviewers.
- Enable private vulnerability reporting (Settings → Security).
- Enable Dependabot alerts and version updates (`.github/dependabot.yml`).

On PyPI, add a trusted publisher for `django-aiodrf` (a pending publisher
before the first upload): owner `django-aiodrf`, repository `django-aiodrf`,
workflow `release.yml`, environment `pypi`.

These settings live on GitHub and PyPI; the workflow files cannot create them.

## Preparing a release

1. Review the user-visible changes, API compatibility and known limitations,
   and update the settings reference, examples and the
   [API inventory](docs/guides/releasing.md#api-inventory) where needed. When
   the release supports a new Django, DRF or asgiref version, follow
   [MAINTAINING.md](MAINTAINING.md) first.
2. In [CHANGELOG.md](CHANGELOG.md), date the release section as
   `## [X.Y.Z] - YYYY-MM-DD` and add an empty `Unreleased` section. The version
   must match `pyproject.toml` and the tag.
3. Run the checks in [CONTRIBUTING.md](CONTRIBUTING.md), regenerate `llms.txt`,
   and verify the affected [Docker examples](examples/CONTAINERS.md) if their
   setup changed.
4. Wait for the full test workflow to pass on the release commit, including the
   integration, example and free-threaded sessions and the coverage minimum.
   Its `package` job builds the wheel from the source distribution, tests the
   installed wheel and uploads an artifact named `release-<commit SHA>` with a
   manifest of the version, release notes and checksums. The artifact expires
   after 30 days; rerun the workflow for the same commit if it has.

A commit that changes only documentation or examples produces no release
artifact.

## Publishing

Create the tag `vX.Y.Z` on the tested commit. The release workflow then:

1. finds the artifact of that commit's successful test run and verifies the
   version, release notes and every file hash, stopping on any mismatch;
2. waits for approval of the `pypi` environment and publishes with attestations;
3. creates the GitHub release from the same notes and attaches the manifest.

Afterwards, check the files, attestations and release notes on PyPI and install
the published package.

If a publication stops halfway, compare the files already uploaded with the
artifact before retrying. Never overwrite a published file or reuse a version
number for different files: correct a faulty release with a new version.

See also the [versioning policy](docs/guides/releasing.md) and the
[security policy](SECURITY.md).
