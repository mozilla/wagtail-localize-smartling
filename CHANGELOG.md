# Localize Smartling Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2024-08-01

### Changed

- Small CI fixes (#24) - @zerolab
- Tooling updates (Python, Django, Wagtail versions), and coverage summary (#21) - @zerolab

### Added

- Support resubmit cancelled/deleted jobs (#29) - @zerolab
- Support unique jobs/job IDs (#27) - @zerolab

## [0.3.0] - 2024-08-01

### Changed

- The minimum required Wagtail version is now 6.1
- Switched the Smartling Jobs report to use Wagtail's `ModelViewSet` ([#17](https://github.com/mozilla/wagtail-localize-smartling/pull/14) @zerolab)
- Tidied up messaging for translations managed in Smartling
- Improved the translation component language
- Locale mapping is applied consistently ([#16](https://github.com/mozilla/wagtail-localize-smartling/pull/16) @bcdickinson)

### Added

- Added option to disable case change when converting from Smartling to project locale ([#17](https://github.com/mozilla/wagtail-localize-smartling/pull/18) @stevejalim)

## [0.2.3] - 2024-07-08

### Changed

- Bugfix to ensure sync.py does not not fail after successfully importing translation

## [0.2.2] - 2024-07-03

### Added

- Ability to map Wagtail [`WAGTAIL_CONTENT_LANGUAGES`](https://docs.wagtail.org/en/stable/reference/settings.html#wagtail-content-languages) locales to Smartling locales ([#6](https://github.com/mozilla/wagtail-localize-smartling/pull/6))

## [0.1.0] - 2024-06-17

Initial minimum lovable version.

<!-- TEMPLATE - keep below to copy for new releases -->
<!--

## [x.y.z] - YYYY-MM-DD

### Added

- ...

### Changed

- ...

### Removed

- ...

-->
