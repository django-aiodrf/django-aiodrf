# Project assets

The `aiodrf-logo/` directory contains the supplied project artwork. The README
and documentation index use the horizontal SVG wordmark, with a light-lettered
variant selected for dark backgrounds. Keep the original aspect ratio and
provide descriptive alternative text.

| Asset | Intended use |
| --- | --- |
| `aiodrf-logo/svg/aiodrf-logo.svg` | Wordmark on a light background |
| `aiodrf-logo/svg/aiodrf-logo-dark-bg.svg` | Wordmark on a dark background |
| `aiodrf-logo/svg/aiodrf-mark.svg` | Compact icon where the project name is already visible |
| `aiodrf-logo/svg/*padded.svg` | Layouts requiring built-in clear space |
| `aiodrf-logo/png/` | Raster exports for clients without SVG support |
| `aiodrf-logo/ico/favicon.ico` | Favicon for a future documentation site |
| `aiodrf-logo/jpg/` | Supplied opaque raster export |

Do not add all export sizes to documentation pages. Favicon files are retained
for site deployment; a repository Markdown page cannot install a browser favicon.

The generated [documentation index](../llms.txt) lives at the repository root,
separately from the artwork; [CONTRIBUTING.md](../CONTRIBUTING.md) describes how
to regenerate it.
