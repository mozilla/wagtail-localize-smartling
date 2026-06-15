// Pre-tick the "Send to Smartling" checkbox on wagtail-localize's
// submit/update-translation forms when the host project has opted in
// via SmartlingSettings.SEND_TO_SMARTLING_BY_DEFAULT.
//
// The Python hook only loads this script when the setting is on, so the
// script itself unconditionally attempts to tick the box. It bails out
// when the checkbox is already ticked or when an error-message element
// is present on the page (so we don't override a user who deliberately
// unticked the box on a re-render after a validation error elsewhere
// on the form). The check is a presence check, not a visibility check
// — Wagtail/Django only emit `.error-message` / `.errorlist` markup
// when there's an actual error to render, so presence is the signal.
(function () {
    var CHECKBOX_SELECTOR =
        'input[type="checkbox"][name="component-wagtail_localize_smartling_job-enabled"]';
    var ERROR_SELECTOR = '.error-message, .errorlist';

    function tickSendToSmartling() {
        var cb = document.querySelector(CHECKBOX_SELECTOR);
        if (!cb || cb.checked) {
            return;
        }
        if (document.querySelector(ERROR_SELECTOR)) {
            return;
        }
        cb.checked = true;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', tickSendToSmartling);
    } else {
        tickSendToSmartling();
    }
})();
