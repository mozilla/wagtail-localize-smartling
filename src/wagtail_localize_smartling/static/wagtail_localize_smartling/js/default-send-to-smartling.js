// Pre-tick the "Send to Smartling" toggle on wagtail-localize's
// submit/update-translation forms when the host project has opted in
// via SmartlingSettings.SEND_TO_SMARTLING_BY_DEFAULT.
//
// The widget upstream renders looks like a pair of "Click to mark"
// /"Click to skip" buttons sitting alongside a checkbox input. The
// visual state is driven by a `component-form--disabled` class on the
// surrounding `<section>`; the underlying form submission state is
// driven by the checkbox's `checked` property. The upstream JS toggles
// both when the buttons are clicked. To flip the default reliably we
// have to do the same two things ourselves — setting `checked` alone
// leaves the UI showing the "off" state even though a submission would
// carry `enabled=true`.
//
// The Python hook only loads this script when the setting is on, so
// the script unconditionally attempts to flip the toggle. It bails
// out when the checkbox is already ticked or when an error-message
// element is present on the page (so we don't override a user who
// deliberately unticked the toggle on a re-render after a validation
// error elsewhere on the form). The error check is a presence check,
// not a visibility check — Wagtail/Django only emit
// `.error-message`/`.errorlist` markup when there's an actual error to
// render, so presence is the signal.
(function () {
    var CHECKBOX_SELECTOR =
        'input[type="checkbox"][name="component-wagtail_localize_smartling_job-enabled"]';
    var ERROR_SELECTOR = '.error-message, .errorlist';
    var SECTION_SELECTOR = '.component-form';
    var DISABLED_CLASS = 'component-form--disabled';

    function enableSendToSmartling() {
        var cb = document.querySelector(CHECKBOX_SELECTOR);
        if (!cb || cb.checked) {
            return;
        }
        if (document.querySelector(ERROR_SELECTOR)) {
            return;
        }

        cb.checked = true;

        var section = cb.closest(SECTION_SELECTOR);
        if (section) {
            section.classList.remove(DISABLED_CLASS);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', enableSendToSmartling);
    } else {
        enableSendToSmartling();
    }
})();
