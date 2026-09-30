"""S3 is explicitly selected and uses the standard AWS credential chain."""

import os

from .settings import *  # noqa: F403

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ["EXAMPLE_S3_BUCKET"],
            "region_name": os.environ.get("AWS_DEFAULT_REGION", "eu-west-1"),
            "default_acl": None,
            "file_overwrite": False,
            "location": "aiodrf-example",
        },
    }
}
