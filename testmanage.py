#!/usr/bin/env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
import sys

from django.core.management import execute_from_command_line
from dotenv import load_dotenv


load_dotenv()


def main():
    os.environ["DJANGO_SETTINGS_MODULE"] = "testapp.settings"
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
