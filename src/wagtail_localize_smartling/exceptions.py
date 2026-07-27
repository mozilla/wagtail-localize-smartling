# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


class IncapableVisualContextCallback(Exception):
    """
    To be raised by the settings.VISUAL_CONTEXT_CALLBACK func
    if it cannot provide the appropriate values.
    """

    pass
