from celery import shared_task
import boto3
from datetime import datetime, timedelta


@shared_task
def delete_video_from_s3(video_key):
    s3 = boto3.client('s3')
    # Assuming 'mybucket' is your S3 bucket name
    s3.delete_object(Bucket='mybucket', Key=video_key)
