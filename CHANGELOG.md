# Localize Smartling Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No changes

## [0.10.3] - 2025-05-07

Bugfix: avoid 500ing if a LandedTranslationTask references a now-deleted content object.

## [0.10.2] - 2025-01-10

(Note: The deprecated 0.10.0 and 0.10.1 were code-identical to 0.10.2 but we had some packaging niggles)

### Added

- Automatically generate Tasks for the dashboard to remind editors to publish pages once translations land - @stevejalim
  - BREAKING CHANGE: requires `django.contrib.humanize` to be added to `settings.INSTALLED_APPS`
- Automatically email admins when translations are imported - @stevejalim
  - BREAKING CHANGE: `signals.translation_imported` renamed to `signals.individual_translation_imported`

## [0.9.0] - 2024-12-12

### Changed

- Change how we generate unique Job names (#50) - @stevejalim
- Backfill changelog to fill in missing releases - @stevejalim

## [0.8.0] - 2024-12-09

### Changed

- Amend how we upload .po files to avoid "File Locked" API error - @stevejalim

## [0.7.0] - 2024-11-28

### Changed

- Improve default job name and description - @stevejalim

## [0.6.1] - 2024-11-26

### Changed

- Do not fail when syncing objects that have no visual context available - @stevejalim

## [0.6.0] - 2024-11-19

### Added

- Add support for Smartling CAT (#35) - @stevejalim

## [0.5.0] - 2024-10-22

### Added

- Adds Job description callback (#32) - @zerolab

## [0.4.0] - 2024-10-14

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
