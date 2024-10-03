# To-do list

## Features

- [x] Django system checks
- [x] Signals
- [x] PyPI publishing from CI
- [x] Indicate Smartling translation status on translation pages:
    - [x] Synced translation edit page (target)
- [x] Job listing in Wagtail admin (Reports > Smartling jobs)
  - [x] and job details in Wagtail admin
- [x] Runtime checks/warnings for compatibility of
      `Locale`s/`WAGTAIL_CONTENT_LANGUAGES`/`LANGUAGE_CODE` with the configured
      Smartling project. Dashboard/overview page to surface errors.


## Known issues

### Upstream `wagtail-localize` issues

- [ ] `wagtail-localize` doesn't provide a way to hide/omit a translation component on a per-instance basis
- [ ] `wagtail-localize` doesn't let you show non-field errors in translation component forms
- [ ] `wagtail-localize` translation components behave differently when submitting or updating translations

### `wagtail-localize-smartling` issues

- [x] Duplicate jobs get created under some circumstances:
    - submitting parent pages including subtree when child pages are already translated
    - translating a page, converting it back to an alias, submitting it for translation again
- [x] It's possible for multiple Smartling jobs to end up with identical content
      if translations are submitted/updated more than once in between runs of
      `sync_smartling`. This seems to be OK for a single target language. Is it OK for
multiple target languages?
- [ ] Cases where uploading files or adding them to jobs take a long time and return 202 responses from the Smartling API (rather than 200 for actions that complete synchronously) aren't handled:
    - [ ] Handle 202 responses for PO file uploads
    - [ ] Handle 202 responses for adding files to jobs (polling?)

## Future features

- [ ] Job callbacks
- [ ] File callbacks
- [ ] Support for signed callbacks
- [ ] Prevent translation from languages other than the Smartling project source language
- [ ] Better use of Smartling's file URIs/namespaces to allow unchanged strings
      to be automatically translated. Will require some protection against the
      creation of jobs with no strings, but should reduce duplicate strings in jobs.
- [ ] For page translations, add a link to the revision to the description sent
      to Smartling
