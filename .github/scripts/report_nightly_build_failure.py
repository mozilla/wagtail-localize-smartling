"""
Called by GH Actions when the nightly build fails.

This reports an error to the #nightly-build-failures Slack channel.
"""

import os

import requests


if SLACK_WEBHOOK_URL := os.getenv("SLACK_WEBHOOK_URL"):
    print("Reporting to #nightly-build-failures slack channel")
    response = requests.post(
        SLACK_WEBHOOK_URL,
        json={
            "text": (
                "A Nightly build failed. "
                f"See https://github.com/bcdickinson/wagtail-localize-smartling/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            )
        },
        timeout=30,
    )
    print("Slack responded with:", response)
else:
    print(
        "Unable to report to #nightly-build-failures slack channel because "
        "SLACK_WEBHOOK_URL is not set."
    )
