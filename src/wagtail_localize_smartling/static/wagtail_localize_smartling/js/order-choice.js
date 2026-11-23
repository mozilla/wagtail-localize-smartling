// Show only the order fields that the chosen "How to send this" option uses,
// on wagtail-localize's submit- and update-translation forms, and take
// "Send now" away once the subtree is included.
//
// wagtail-localize renders every field of a component whatever the choice is,
// so an editor sending a page on its own is shown an order name, notes textarea
// and an order dropdown that will all be ignored, and an editor checking the
// "Include subtree" checkbox only learns that a subtree cannot be sent on its
// own after pressing the button. To make the UX better, we hide the fields that
// should not be filled in, based on the user's choice.
//
// The markup below belongs to wagtail-localize and to Wagtail, so every lookup
// says what it is looking for and gives up quietly when it is not there.
(function () {
    var FIELD_PREFIX = 'component-wagtail_localize_smartling_job-';
    var CHOICE_SELECTOR =
        'input[type="radio"][name="' + FIELD_PREFIX + 'how_to_send_choice"]';
    var SUBTREE_SELECTOR = 'input[type="checkbox"][name="include_subtree"]';
    var NOTE_SELECTOR = '[data-subtree-order-note]';
    var ERROR_SELECTOR = '.error-message';
    var SEND_NOW = 'now';
    var NEW_ORDER = 'new';
    var STASH_KEY = 'orderChoiceValue';

    // Due date is in none of these: it belongs to all three options.
    var ORDER_FIELDS = ['new_order_name', 'new_order_notes', 'order'];
    var FIELDS_USED_BY = {
        now: [],
        new: ['new_order_name', 'new_order_notes'],
        existing: ['order']
    };

    // True while the script, rather than the editor, is what moved the choice
    // off "Send now", so that unticking the subtree box puts the choice back
    // without overwriting one the editor made deliberately.
    var scriptMovedChoice = false;

    // True until the editor changes something. The errors on screen describe
    // the submission that was refused, so the rows carrying them start out
    // visible however the choice would have them; once the form has moved on,
    // they are about a submission that no longer exists.
    var errorsStillDescribeTheForm = true;

    function chosenOption() {
        var checked = document.querySelector(CHOICE_SELECTOR + ':checked');
        return checked ? checked.value : SEND_NOW;
    }

    function stashValue(input) {
        if (input.value) {
            input.dataset[STASH_KEY] = input.value;
            input.value = '';
        }
    }

    function restoreValue(input) {
        if (STASH_KEY in input.dataset) {
            input.value = input.dataset[STASH_KEY];
            delete input.dataset[STASH_KEY];
        }
    }

    function rowOf(input) {
        // Wagtail wraps each field, and wagtail-localize puts that wrapper in
        // a list item, so hiding the row takes the label with the input.
        return input.closest('li') || input.closest('.w-field__wrapper');
    }

    function showOrderFieldsForChoice() {
        var fieldsInUse = FIELDS_USED_BY[chosenOption()] || [];

        ORDER_FIELDS.forEach(function (fieldName) {
            var input = document.getElementById('id_' + FIELD_PREFIX + fieldName);
            var row = input && rowOf(input);
            if (!row) {
                return;
            }

            // A row carrying its own error is on screen to be fixed, so the
            // choice does not get to hide it until the editor has moved on.
            var keepOnScreen =
                fieldsInUse.indexOf(fieldName) !== -1 ||
                (errorsStillDescribeTheForm && !!row.querySelector(ERROR_SELECTOR));

            row.style.display = keepOnScreen ? '' : 'none';
            if (keepOnScreen) {
                restoreValue(input);
            } else {
                stashValue(input);
            }
        });
    }

    function offerSendNowUnlessSubtreeIncluded() {
        var subtreeCheckbox = document.querySelector(SUBTREE_SELECTOR);
        var sendNowRadio = document.querySelector(CHOICE_SELECTOR + '[value="' + SEND_NOW + '"]');
        if (!subtreeCheckbox || !sendNowRadio) {
            return;
        }

        // Each option is wrapped on its own. If that ever stops being true,
        // leave the wrapper alone rather than take every option off screen
        // and strand the editor with nothing to choose.
        var sendNowRow = sendNowRadio.closest('div');
        if (sendNowRow && sendNowRow.querySelectorAll(CHOICE_SELECTOR).length > 1) {
            sendNowRow = null;
        }
        var note = document.querySelector(NOTE_SELECTOR);

        if (subtreeCheckbox.checked) {
            if (sendNowRow) {
                sendNowRow.style.display = 'none';
            }
            // Hiding is not enough: a radio decides what happens, so a
            // keyboard user must not be able to reach one that is off screen.
            sendNowRadio.disabled = true;
            if (note) {
                note.hidden = false;
            }

            if (sendNowRadio.checked) {
                var newOrderRadio = document.querySelector(
                    CHOICE_SELECTOR + '[value="' + NEW_ORDER + '"]'
                );
                if (newOrderRadio) {
                    newOrderRadio.checked = true;
                    scriptMovedChoice = true;
                }
            }
        } else {
            if (sendNowRow) {
                sendNowRow.style.display = '';
            }
            sendNowRadio.disabled = false;
            if (note) {
                note.hidden = true;
            }

            if (scriptMovedChoice) {
                sendNowRadio.checked = true;
                scriptMovedChoice = false;
            }
        }
    }

    function refresh() {
        offerSendNowUnlessSubtreeIncluded();
        showOrderFieldsForChoice();
    }

    function onEditorChanged() {
        errorsStillDescribeTheForm = false;
        refresh();
    }

    function onChoiceChanged() {
        // A choice the editor makes is theirs to keep.
        scriptMovedChoice = false;
        onEditorChanged();
    }

    function hideTheFieldsTheChoiceDoesNotUse() {
        var choiceRadios = document.querySelectorAll(CHOICE_SELECTOR);
        if (!choiceRadios.length) {
            return;
        }

        Array.prototype.forEach.call(choiceRadios, function (radio) {
            radio.addEventListener('change', onChoiceChanged);
            // Clicking the option that is already selected fires no change
            // event, and re-picking what the script picked is still a choice
            // the editor has made.
            radio.addEventListener('click', onChoiceChanged);
        });

        var subtreeCheckbox = document.querySelector(SUBTREE_SELECTOR);
        if (subtreeCheckbox) {
            subtreeCheckbox.addEventListener('change', onEditorChanged);
        }

        refresh();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', hideTheFieldsTheChoiceDoesNotUse);
    } else {
        hideTheFieldsTheChoiceDoesNotUse();
    }
})();
