"""Tests for the Cloudflare R2 storage migration.

Nothing here talks to Cloudflare: R2 is off in the test environment, so these
tests pin down the *wiring* (settings / storage backend / URL selection) and the
branching in ``UserUpdateForm.save`` and ``migrate_media_to_r2`` with the
storage layer mocked out or swapped for Django's in-memory backend.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, InMemoryStorage, default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from users.forms import UserUpdateForm
from users.models import AvatarBlob, avatar_object_key

User = get_user_model()

R2_SETTINGS = dict(
    R2_BUCKET='guwu-oj-media',
    R2_ACCESS_KEY_ID='test-access-key',
    R2_SECRET_ACCESS_KEY='test-secret-key',
    R2_ENDPOINT_URL='https://testacct.r2.cloudflarestorage.com',
    R2_CUSTOM_DOMAIN='media.guwu.camluni.cn',
)


class R2DisabledWiringTests(TestCase):
    """With no R2_* environment the app must keep using local storage."""

    def test_r2_is_disabled_by_default(self):
        from django.conf import settings

        self.assertFalse(settings.R2_ENABLED)

    def test_default_storage_is_filesystem(self):
        self.assertIsInstance(default_storage, FileSystemStorage)


class R2MediaStorageTests(TestCase):
    """The backend itself, configured purely through settings."""

    def _make_storage(self, **overrides):
        from oj_project.storage import R2MediaStorage

        values = dict(R2_SETTINGS)
        values.update(overrides)
        with override_settings(**values):
            return R2MediaStorage()

    def test_reads_configuration_from_settings(self):
        storage = self._make_storage()

        self.assertEqual(storage.bucket_name, 'guwu-oj-media')
        self.assertEqual(
            storage.endpoint_url, 'https://testacct.r2.cloudflarestorage.com'
        )
        self.assertEqual(storage.region_name, 'auto')
        self.assertEqual(storage.custom_domain, 'media.guwu.camluni.cn')

    def test_public_urls_are_unsigned_cdn_urls(self):
        storage = self._make_storage()

        self.assertFalse(storage.querystring_auth)
        self.assertIsNone(storage.default_acl)
        self.assertEqual(
            storage.url('problem-images/1/diagram.png'),
            'https://media.guwu.camluni.cn/problem-images/1/diagram.png',
        )

    def test_storage_without_custom_domain_still_builds(self):
        storage = self._make_storage(R2_CUSTOM_DOMAIN='')
        self.assertIsNone(storage.custom_domain)


class AvatarUrlTests(TestCase):
    """``User.avatar_url`` picks the right representation for each state."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='avatar-user', email='avatar@example.com', password='x',
        )

    def test_no_avatar_at_all(self):
        self.assertIsNone(self.user.avatar_url)
        self.assertFalse(self.user.has_avatar)
        self.assertFalse(self.user.avatar_is_legacy_blob)

    def test_legacy_blob_uses_captcha_view_url(self):
        AvatarBlob.objects.create(
            user=self.user, content_type='image/png', data=b'\x89PNG',
        )
        user = User.objects.get(pk=self.user.pk)

        ts = int(user.avatar_blob.updated_at.timestamp())
        self.assertEqual(
            user.avatar_url,
            f'{reverse("avatar", kwargs={"username": user.username})}?v={ts}',
        )
        self.assertTrue(user.has_avatar)
        self.assertTrue(user.avatar_is_legacy_blob)

    @override_settings(
        STORAGES={
            'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
            'staticfiles': {
                'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
            },
        },
    )
    def test_object_storage_avatar_returns_direct_url(self):
        user = self.user
        user.avatar.save('pic.png', ContentFile(b'\x89PNG'), save=False)
        user.save(update_fields=['avatar'])
        user.refresh_from_db()

        self.assertTrue(user.avatar.name)
        # Object-storage avatars are published straight from storage — never
        # through the captcha-gated ``avatar`` view.
        self.assertEqual(user.avatar_url, user.avatar.url)
        self.assertTrue(user.has_avatar)
        self.assertFalse(user.avatar_is_legacy_blob)


class AvatarObjectKeyTests(TestCase):
    def test_key_has_random_suffix_and_keeps_extension(self):
        user = User(username='key-user')
        key = avatar_object_key(user, 'photo.PNG')

        self.assertTrue(key.startswith('avatars/key-user/'))
        self.assertTrue(key.endswith('.png'))

    def test_unknown_extension_falls_back_to_jpg(self):
        user = User(username='key-user')
        key = avatar_object_key(user, 'payload.exe')
        self.assertTrue(key.endswith('.jpg'))


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)
class UserUpdateFormR2Tests(TestCase):
    """``UserUpdateForm.save`` must write to object storage when R2 is on."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='form-user', email='form@example.com', password='x',
        )
        AvatarBlob.objects.create(
            user=self.user, content_type='image/png', data=b'old',
        )

    def _submit(self):
        upload = ContentFile(b'\x89PNG\r\n\x1a\n', name='me.png')
        upload.content_type = 'image/png'
        form = UserUpdateForm(
            data={'nickname': 'n', 'bio': ''},
            files={'avatar': upload},
            instance=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    @override_settings(R2_ENABLED=True)
    @patch('users.forms.Image.open')
    def test_r2_path_writes_to_storage_and_drops_legacy_blob(self, _open):
        user = self._submit()

        self.assertTrue(user.avatar.name)
        self.assertFalse(AvatarBlob.objects.filter(user=user).exists())

    @patch('users.forms.Image.open')
    def test_local_path_keeps_blob_in_postgres(self, _open):
        user = self._submit()

        self.assertFalse(user.avatar.name)
        self.assertTrue(AvatarBlob.objects.filter(user=user).exists())


class MigrateMediaCommandTests(TestCase):
    def test_refuses_to_run_when_r2_is_disabled(self):
        with self.assertRaises(CommandError):
            call_command('migrate_media_to_r2')

    def test_avatars_already_in_storage_are_skipped(self):
        from devlog.management.commands import migrate_media_to_r2 as command_module

        user = User.objects.create_user(
            username='migrate-user', email='migrate@example.com', password='x',
        )
        user.avatar.name = 'avatars/1/already-there.png'
        user.save(update_fields=['avatar'])
        AvatarBlob.objects.create(
            user=user, content_type='image/png', data=b'bytes',
        )

        storage = MagicMock()
        storage.exists.return_value = True
        with patch.object(command_module, 'default_storage', storage):
            command_module.Command()._migrate_avatars(
                dry_run=False, delete_blobs=False,
            )

        storage.save.assert_not_called()
        self.assertTrue(AvatarBlob.objects.filter(user=user).exists())
