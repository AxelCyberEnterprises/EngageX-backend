import boto3
import json
import logging
import os
import traceback
from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError
from django.conf import settings

logger = logging.getLogger(__name__)

def _get_aws_credentials():
    """Get AWS credentials from environment variables or Django settings."""
    # Try to get from environment variables first (like the test script does)
    aws_access_key_id = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    aws_region = os.environ.get('AWS_SES_REGION', 'us-west-1')
    
    # Fall back to Django settings if not in environment
    if not all([aws_access_key_id, aws_secret_access_key]):
        aws_access_key_id = getattr(settings, 'AWS_ACCESS_KEY_ID', None)
        aws_secret_access_key = getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)
        aws_region = getattr(settings, 'AWS_SES_REGION', aws_region)
    
    return aws_access_key_id, aws_secret_access_key, aws_region

def _log_aws_config():
    """Log AWS configuration for debugging purposes."""
    # Get credentials using the same method as the actual function
    aws_access_key_id, aws_secret_access_key, aws_region = _get_aws_credentials()
    
    config = {
        'AWS_ACCESS_KEY_ID': f"{'*' * 8 + aws_access_key_id[-4:] if aws_access_key_id else 'Not set'}",
        'AWS_SECRET_ACCESS_KEY': f"{'*' * 8 + aws_secret_access_key[-4:] if aws_secret_access_key else 'Not set'}",
        'AWS_SES_REGION': aws_region if aws_region else 'Not set',
        'DEFAULT_FROM_EMAIL': getattr(settings, 'DEFAULT_FROM_EMAIL', 'Not set'),
        'EMAIL_BACKEND': getattr(settings, 'EMAIL_BACKEND', 'Not set'),
        'Source': 'Environment' if os.environ.get('AWS_ACCESS_KEY_ID') else 'Django Settings'
    }
    
    logger.debug("=== AWS SES Configuration ===")
    for key, value in config.items():
        logger.debug(f"{key}: {value}")
    logger.debug("============================")

def send_email_via_ses(subject, body, to_emails, from_email=None, html_body=None, attachments=None):
    """
    Send an email using Django's email backend (console for testing)
    
    Args:
        subject (str): Email subject
        body (str): Plain text email body
        to_emails (str or list): Single email address or list of email addresses
        from_email (str, optional): Sender email address. Defaults to settings.DEFAULT_FROM_EMAIL.
        html_body (str, optional): HTML version of the email body.
        attachments (list, optional): List of dicts with 'filename' and 'content' keys.
            'content' can be a file-like object or bytes.
            
    Returns:
        dict: Email sending status
    """
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings
    import logging
    import os
    from email.mime.base import MIMEBase
    from email import encoders
    
    logger = logging.getLogger(__name__)
    
    # Ensure to_emails is a list
    if isinstance(to_emails, str):
        to_emails = [to_emails]
    
    logger = logging.getLogger(__name__)
    
    try:
        # Set default from email if not provided
        from_email = from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com')
        
        # Log email details
        logger.info(f"Preparing to send email to: {to_emails}")
        logger.info(f"Subject: {subject}")
        
        # Create email message
        email = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=from_email,
            to=to_emails
        )
        
        # Attach HTML version if provided
        if html_body:
            email.attach_alternative(html_body, 'text/html')
        
        # Add attachments if any
        if attachments:
            for attachment in attachments:
                filename = attachment.get('filename', 'attachment')
                content = attachment.get('content')
                
                if hasattr(content, 'read'):
                    # If it's a file-like object, read its content
                    content = content.read()
                
                email.attach(filename, content, attachment.get('mimetype', 'application/octet-stream'))
        
        # Send the email
        email.send(fail_silently=False)
        
        logger.info(f"Email sent successfully to {', '.join(to_emails)}")
        return {
            'status': 'success',
            'message': 'Email sent successfully',
            'to': to_emails
        }
        
    except Exception as e:
        error_msg = f"Error sending email: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        return {
            'status': 'error',
            'message': str(e),
            'type': type(e).__name__
        }
