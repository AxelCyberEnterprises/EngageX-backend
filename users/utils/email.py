import boto3
import json
import logging
import os
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

def send_email_via_ses(subject, body, to_emails, from_email=None, html_body=None):
    """
    Send an email using Amazon SES.
    
    Args:
        subject (str): Email subject
        body (str): Plain text email body
        to_emails (str or list): Single email address or list of email addresses
        from_email (str, optional): Sender email address. Defaults to settings.DEFAULT_FROM_EMAIL.
        html_body (str, optional): HTML version of the email. If None, only text will be sent.
        
    Returns:
        dict: SES response if successful, None otherwise
    """
    print("\n=== send_email_via_ses ===")
    print(f"To: {to_emails}")
    print(f"Subject: {subject}")
    print(f"From: {from_email}")
    
    # Log AWS configuration for debugging
    _log_aws_config()
    
    # Get AWS credentials
    aws_access_key_id, aws_secret_access_key, aws_region = _get_aws_credentials()
    
    # Check if AWS credentials are configured
    if not all([aws_access_key_id, aws_secret_access_key, aws_region]):
        print("\n!!! ERROR: AWS SES credentials not properly configured")
        print(f"AWS_ACCESS_KEY_ID: {'Set' if aws_access_key_id else 'Not set'}")
        print(f"AWS_SECRET_ACCESS_KEY: {'Set' if aws_secret_access_key else 'Not set'}")
        print(f"AWS_SES_REGION: {aws_region if aws_region else 'Not set'}")
        return None
    else:
        print("AWS credentials found and validated")
        
    from_email = from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    if not from_email:
        error_msg = "No sender email address provided and DEFAULT_FROM_EMAIL is not set"
        logger.error(error_msg)
        return None
        
    if isinstance(to_emails, str):
        to_emails = [to_emails]
    
    # Validate email addresses
    if not to_emails or not all(to_emails):
        error_msg = "No valid recipient email addresses provided"
        logger.error(error_msg)
        return None
    
    try:
        # Log the AWS credentials being used (mask sensitive data)
        logger.debug(f"Creating SES client with region: {settings.AWS_SES_REGION}")
        logger.debug(f"From email: {from_email}")
        logger.debug(f"To emails: {to_emails}")
        
        # Create a new SES client with explicit configuration
        print("\nCreating SES client with config:")
        print(f"Region: {aws_region}")
        print(f"AWS Access Key ID: {aws_access_key_id[:4]}...{aws_access_key_id[-4:] if aws_access_key_id else ''}")
        
        ses_config = {
            'region_name': aws_region,
            'aws_access_key_id': aws_access_key_id,
            'aws_secret_access_key': aws_secret_access_key,
            'config': boto3.session.Config(
                connect_timeout=10,
                read_timeout=10,
                retries={
                    'max_attempts': 3,
                    'mode': 'standard'
                }
            )
        }
        
        logger.debug("Creating SES client with config:")
        for key in ses_config:
            if key != 'aws_secret_access_key':  # Don't log the full secret key
                safe_value = ses_config[key]
                if key == 'aws_access_key_id' and safe_value:
                    safe_value = f"{safe_value[:4]}...{safe_value[-4:]}" if len(safe_value) > 8 else "[REDACTED]"
                logger.debug(f"  {key}: {safe_value}")
        
        ses = boto3.client('ses', **ses_config)
        logger.debug("Successfully created SES client")
        
        # Verify email identity (for debugging)
        try:
            identity = ses.get_identity_verification_attributes(
                Identities=[from_email]
            )
            logger.debug(f"Email verification status for {from_email}: {identity.get('VerificationAttributes', {}).get(from_email, {}).get('VerificationStatus', 'Not verified')}")
        except Exception as e:
            logger.warning(f"Could not verify email identity: {str(e)}")
        
        # Prepare the email data
        email_data = {
            'Destination': {
                'ToAddresses': to_emails if isinstance(to_emails, list) else [to_emails],
            },
            'Message': {
                'Body': {
                    'Text': {
                        'Charset': 'UTF-8',
                        'Data': body[:100] + '...' if len(body) > 100 else body,
                    },
                },
                'Subject': {
                    'Charset': 'UTF-8',
                    'Data': subject,
                },
            },
            'Source': from_email,
        }
        
        # Only add HTML body if provided
        if html_body:
            email_data['Message']['Body']['Html'] = {
                'Charset': 'UTF-8',
                'Data': html_body,
            }
        
        print("\nSending email with data:")
        print(json.dumps(email_data, indent=2))
        
        # Send the email
        response = ses.send_email(**email_data)
        
        logger.info(f"Email sent successfully to {', '.join(to_emails)}")
        logger.debug(f"SES Response: {response}")
        return response
        
    except NoCredentialsError as e:
        error_msg = "No AWS credentials found. Please check your AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        logger.error(error_msg)
        logger.exception(e)
        raise Exception(error_msg) from e
        
    except PartialCredentialsError as e:
        error_msg = f"Incomplete AWS credentials: {str(e)}. Please check your AWS configuration."
        logger.error(error_msg)
        logger.exception(e)
        raise Exception(error_msg) from e
        
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_msg = e.response.get('Error', {}).get('Message', str(e))
        error_details = f"AWS SES Error ({error_code}): {error_msg}"
        
        if error_code == 'InvalidClientTokenId':
            error_details += "\nThis usually means your AWS access key ID is invalid or not active."
        elif error_code == 'SignatureDoesNotMatch':
            error_details += "\nThis usually means your AWS secret access key is incorrect."
        elif error_code == 'AccessDenied':
            error_details += "\nThis usually means your IAM user doesn't have permission to use SES."
        
        logger.error(error_details)
        logger.exception(e)
        raise Exception(f"Failed to send email: {error_msg}") from e
        
    except Exception as e:
        error_msg = f"Unexpected error sending email: {str(e)}"
        logger.error(error_msg)
        logger.exception(e)
        raise Exception(error_msg) from e
