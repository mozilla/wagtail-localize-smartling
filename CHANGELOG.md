# Localize Smartling Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Add `PendingTranslationLockMixin`, which locks a page in the Wagtail admin while its content is awaiting translation in Smartling, so that edits made in the meantime aren't overwritten when the translation is imported.
- Add `Order` model for batching pages sent to Smartling as one job. A `Job` now has a `JobFile` per translatable object and every job belongs to an order.
- Deprecate `Job.translation_source`, `Job.file_uri` and `Job.content_hash`. Nothing writes to them any more; read the job's `files` instead. They are removed in the following release.
- **Breaking:** `JOB_DESCRIPTION_CALLBACK` is now called with `(default_description, job)`. It previously received the source instance and the translations, which the setting's own type annotation described incorrectly. The pages a job carries are `job.files`, each with a `translation_source`.
- **Breaking:** `VISUAL_CONTEXT_CALLBACK` is now called with `(job, translation_source)`, once per file of the job. Raising `IncapableVisualContextCallback` now skips that one file rather than the whole job.
- **Breaking:** groups holding `wagtail_localize.submit_translation` are granted the new `Order` permissions by migration. Projects that manage their groups in code must grant those permissions themselves.
- Record why a job failed to sync on the job itself: `last_sync_error`, `last_sync_error_at`, `consecutive_sync_failures` and `sync_blocked`. Only a failed push blocks a job. The fields are cleared by the next successful sync of a job with nothing left to push.
- Add the `job_sync_failed` signal, sent with the job, the exception and whether the failure is permanent each time a sync attempt fails, as the hook for notifying a host project.
- Add `Job.needs_a_new_job`, true when the content can only be translated by sending it again
- Add `Job.push_is_incomplete`, true while a job still has files that never reached Smartling.
- the report at `/smartling-jobs/` lists orders instead of jobs. The URL and its `wagtail_localize_smartling_jobs` namespace are unchanged, but the menu item is now "Smartling orders", the columns show the order's name, page count and state, and reading the report takes `view_order` rather than `view_job` permission. A migration grants `view_order` to groups that already hold `view_job`; projects that manage their groups in code must grant it themselves.
- A submitted order can no longer be deleted from the admin. Its jobs reference it with `PROTECT`, so the delete view answered with a database error rather than a refusal.

## [0.13.0] - 2026-08-03

- Support optional Slack notifications when jobs are synced to Smartling and are awaiting authorization (#75) - @stevejalim
- Add `SEND_TO_SMARTLING_BY_DEFAULT` setting to pre-tick the "Send to Smartling" checkbox on the submit-translations and update-translation forms. Defaults to on unless `settings.DEV` is truthy (#63) - @stevejalim
- Notify the Job submitter alongside the Translation Approver group when a Smartling import lands, and include per-locale direct edit links in the notification email (partial #68) - @stevejalim
- Fix the dashboard "landed translations" panel showing the truncated count instead of the total number of pages awaiting approval (#64) - @stevejalim

## [0.12.2] - 2026-04-20

- Fix blow-up when `post_save` fires with `raw=True` (e.g. during `loaddata`) — skip snippet-published handler early and log at DEBUG - @stevejalim
- Fix `_model_is_registered_as_snippet` being called with an instance instead of a class - @stevejalim

## [0.12.1] - 2026-04-06

- Fix blow-up when invisible line breaks are in PO contnet - @stevejalim

## [0.12.0] - 2026-03-12

- Support explicitly excluding locales from Smartling sync - @dchukhin

## [0.11.0] - 2026-01-26

- Add per-locale translation imports and job expansion by @stevejalim
- Update minimum Python, Django and Wagtail versions by @stevejalim

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
