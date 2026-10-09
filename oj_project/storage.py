"""Cloudflare R2 object storage backend.

R2 exposes an S3-compatible API, so ``django-storages``' S3 backend with a
custom ``endpoint_url`` is all that is needed.  This module is only imported
when the ``R2_*`` environment variables are present (see ``settings.STORAGES``);
when they are absent Django keeps using local-filesystem storage, so
development and the test suite never need R2 credentials.

Reads are public: problem-statement images are embedded in Markdown and user
avatars are rendered straight from the CDN, so ``.url()`` returns the bucket's
custom domain with no query-string signing (``querystring_auth=False``).
"""

from django.conf import settings
from storages.backends.s3 import S3Storage


class R2MediaStorage(S3Storage):
    """S3 storage pointed at a Cloudflare R2 bucket.

    All configuration is sourced from Django settings (which in turn read the
    environment), so a single instance is enough for every media object —
    problem images under ``problem-images/`` and avatars under ``avatars/``.
    """

    # R2 does not implement S3 ACLs; asking for one makes boto3 raise.
    default_acl = None
    # Object keys are unique by construction (random hex suffixes), so the
    # extra HEAD request django-storages would issue to avoid overwriting an
    # existing key is pure latency.
    file_overwrite = True
    # Objects are public through the custom domain — never sign read URLs.
    querystring_auth = False

    def __init__(self, **kwargs):
        kwargs.setdefault('bucket_name', settings.R2_BUCKET)
        kwargs.setdefault('access_key', settings.R2_ACCESS_KEY_ID)
        kwargs.setdefault('secret_key', settings.R2_SECRET_ACCESS_KEY)
        kwargs.setdefault('endpoint_url', settings.R2_ENDPOINT_URL)
        # R2 has no regions; the S3 API expects the literal string ``auto``.
        kwargs.setdefault('region_name', 'auto')
        kwargs.setdefault('signature_version', 's3v4')
        kwargs.setdefault('addressing_style', 'virtual')
        if settings.R2_CUSTOM_DOMAIN:
            kwargs.setdefault('custom_domain', settings.R2_CUSTOM_DOMAIN)
        super().__init__(**kwargs)
