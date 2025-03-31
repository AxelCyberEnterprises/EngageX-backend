from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage


class ProfilePicStorage(S3Boto3Storage):
    location = "profile-pic"
    bucket_name = settings.AWS_STORAGE_BUCKET_NAME


class SlidesStorage(S3Boto3Storage):
    location = "slides"
    bucket_name = settings.AWS_STORAGE_BUCKET_NAME


class StaticVideosStorage(S3Boto3Storage):
    location = "static-videos"
    bucket_name = settings.AWS_STORAGE_BUCKET_NAME


class UserVideosStorage(S3Boto3Storage):
    location = "user-videos"
    bucket_name = settings.AWS_STORAGE_BUCKET_NAME
