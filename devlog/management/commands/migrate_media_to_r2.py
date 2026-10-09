"""Migrate existing avatars and problem images onto Cloudflare R2.

Run this once after R2 has been activated and the ``R2_*`` variables are in
``.env`` (see ``scripts/setup_r2.sh``).  It is idempotent: objects already
present in the bucket are left untouched, so it is safe to re-run.
"""

from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from users.models import AvatarBlob

# AvatarBlob stores the upload's declared MIME type; map it back to an object
# key extension so the CDN serves the right Content-Type.
_CONTENT_TYPE_EXT = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
}


class Command(BaseCommand):
    help = '\u5c06\u5386\u53f2\u5934\u50cf\uff08PostgreSQL AvatarBlob\uff09\u4e0e\u672c\u5730 media/ \u6587\u4ef6\u8fc1\u79fb\u5230 Cloudflare R2\u3002'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='\u53ea\u5217\u51fa\u5c06\u8981\u4e0a\u4f20\u7684\u5bf9\u8c61\uff0c\u4e0d\u5199\u5165 R2\u3002',
        )
        parser.add_argument(
            '--delete-legacy-blobs',
            action='store_true',
            help='\u5934\u50cf\u8fc1\u79fb\u6210\u529f\u540e\u5220\u9664 PostgreSQL \u4e2d\u7684 AvatarBlob \u8bb0\u5f55\u3002',
        )
        parser.add_argument(
            '--delete-local',
            action='store_true',
            help='\u672c\u5730 media/ \u6587\u4ef6\u4e0a\u4f20\u6210\u529f\u540e\u5220\u9664\u672c\u5730\u526f\u672c\u3002',
        )

    def handle(self, *args, **options):
        if not settings.R2_ENABLED:
            raise CommandError(
                'R2 \u672a\u542f\u7528\uff1a\u8bf7\u5148\u5728 .env \u4e2d\u914d\u7f6e R2_ACCOUNT_ID / R2_BUCKET / '
                'R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY \u540e\u91cd\u8bd5\u3002'
            )

        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('=== DRY RUN\uff1a\u4e0d\u4f1a\u5199\u5165 R2 ==='))

        self._migrate_avatars(
            dry_run=dry_run,
            delete_blobs=options['delete_legacy_blobs'],
        )
        self._migrate_media_files(
            dry_run=dry_run,
            delete_local=options['delete_local'],
        )

    # -- avatars ------------------------------------------------------------
    def _migrate_avatars(self, *, dry_run: bool, delete_blobs: bool) -> None:
        migrated = skipped = 0
        for blob in AvatarBlob.objects.select_related('user').iterator():
            user = blob.user
            if user.avatar and default_storage.exists(user.avatar.name):
                skipped += 1
                continue

            ext = _CONTENT_TYPE_EXT.get((blob.content_type or '').lower(), '.jpg')
            if dry_run:
                self.stdout.write(f'  \u5934\u50cf {user.username}: \u5c06\u4e0a\u4f20 AvatarBlob ({ext})')
                migrated += 1
                continue

            user.avatar.save(
                f'avatar{ext}', ContentFile(blob.image_data), save=False
            )
            user.save(update_fields=['avatar'])
            if delete_blobs:
                blob.delete()
            self.stdout.write(f'  \u5934\u50cf {user.username} -> {user.avatar.name}')
            migrated += 1

        self.stdout.write(self.style.SUCCESS(
            f'\u5934\u50cf\uff1a{"\u5f85" if dry_run else "\u5df2"}\u8fc1\u79fb {migrated} \u4e2a\uff0c\u8df3\u8fc7 {skipped} \u4e2a\u3002'
        ))

    # -- local media/ files -------------------------------------------------
    def _migrate_media_files(self, *, dry_run: bool, delete_local: bool) -> None:
        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.is_dir():
            self.stdout.write(self.style.SUCCESS('media/ \u76ee\u5f55\u4e0d\u5b58\u5728\uff0c\u65e0\u9700\u8fc1\u79fb\u672c\u5730\u6587\u4ef6\u3002'))
            return

        migrated = skipped = 0
        for path in sorted(media_root.rglob('*')):
            if not path.is_file():
                continue
            rel = path.relative_to(media_root).as_posix()

            if dry_run:
                self.stdout.write(f'  \u6587\u4ef6 {rel}: \u5c06\u4e0a\u4f20')
                migrated += 1
                continue

            if default_storage.exists(rel):
                skipped += 1
                continue

            with path.open('rb') as fh:
                # file_overwrite=True, so the key is written as-is and the
                # relative path is preserved.
                default_storage.save(rel, fh)
            if delete_local:
                path.unlink()
            self.stdout.write(f'  \u6587\u4ef6 {rel}: \u5df2\u4e0a\u4f20')
            migrated += 1

        self.stdout.write(self.style.SUCCESS(
            f'media/ \u6587\u4ef6\uff1a{"\u5f85" if dry_run else "\u5df2"}\u8fc1\u79fb {migrated} \u4e2a\uff0c\u8df3\u8fc7 {skipped} \u4e2a\u3002'
        ))
