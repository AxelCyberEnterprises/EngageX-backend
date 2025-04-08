import boto3
from botocore.exceptions import ClientError
from django.conf import settings

def send_email_via_ses(subject, body, to_emails, from_email=None):
    from_email = from_email or settings.DEFAULT_FROM_EMAIL
    if isinstance(to_emails, str):
        to_emails = [to_emails]

    ses_client = boto3.client(
        'ses',
        region_name=settings.AWS_SES_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )

    try:
        response = ses_client.send_email(
            Source=from_email,
            Destination={'ToAddresses': to_emails},
            Message={
                'Subject': {'Data': subject},
                'Body': {
                    'Text': {'Data': body}
                }
            }
        )
        return response
    except ClientError as e:
        print(f"Error sending email via SES: {e}")
        return None
