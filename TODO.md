# To-do list

## Features

- [ ] Job callbacks
- [ ] Django system checks
- [ ] Signals
- [ ] PyPI publishing from CI
- [ ] Indicate Smartling translation status on translation pages:
    - [ ] Synced translation edit page (target)
    - [ ] Normal Wagtail edit page (source)
- [ ] Job listing and job details in Wagtail admin
- [ ] Runtime checks/warnings for compatibility of
      `Locale`s/`WAGTAIL_CONTENT_LANGUAGES`/`LANGUAGE_CODE` with the configured
      Smartling project. Dashboard/overview page to surface errors.
- [ ] Images and other overridable segments
- [ ] Translatable related objects
- [ ] Updating translations (as opposed to initial submission for translation)
- [ ] Submission of whole subtrees for translation, particularly where pages in
      the subtree may already be translated

## Minor items

- [ ] Add a setting that always send jobs to Smartling (i.e.
      `@register_translation_component(required=True, ...)` on the `Job` model)
- [ ] Add a setting to control whether Smartling returns original strings for
      untranslated strings when downloading translated files.
- [ ] Handle 202 responses for PO file uploads
- [ ] Handle 202 responses for adding files to jobs (polling?)
- [ ] For page translations, add a link to the revision to the description sent
      to Smartling


## Known issues

- [ ] ...


## Future features

- [ ] Better use of Smartling's file URIs/namespaces to allow unchanged strings
      to be automatically translated. Will require some protection against the
      creation of jobs with no strings.
- [ ] Support for signed callbacks
- [ ] File callbacks
- [ ] Support for custom job fields
