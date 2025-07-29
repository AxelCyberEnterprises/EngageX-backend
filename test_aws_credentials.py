"""
This script helps diagnose AWS SES credential issues.
Run it with: python test_aws_credentials.py
"""
import os
import boto3
import logging
from pathlib import Path
from dotenv import load_dotenv
from botocore.exceptions import (
    NoCredentialsError, 
    PartialCredentialsError, 
    ClientError,
    NoRegionError
)

# Load environment variables from .env file
env_path = Path('.') / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    print("✅ Loaded .env file")
else:
    print("⚠️  No .env file found, using system environment variables")

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_aws_credentials():
    """Test AWS credentials by attempting to make a simple SES API call."""
    # Get credentials from environment
    access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    region = os.environ.get('AWS_SES_REGION', 'us-west-1')
    
    print("\n=== AWS Credentials Test ===\n")
    print(f"AWS_ACCESS_KEY_ID: {'*' * 8 + access_key[-4:] if access_key else 'Not set'}")
    print(f"AWS_SECRET_ACCESS_KEY: {'*' * 8 + secret_key[-4:] if secret_key else 'Not set'}")
    print(f"AWS_SES_REGION: {region}\n")
    
    if not all([access_key, secret_key]):
        print("❌ Error: Missing AWS credentials")
        if not access_key:
            print("  - AWS_ACCESS_KEY_ID is not set")
        if not secret_key:
            print("  - AWS_SECRET_ACCESS_KEY is not set")
        return False
    
    if not region:
        print("❌ Error: AWS_SES_REGION is not set")
        return False
    
    try:
        # Try to create an SES client
        ses = boto3.client(
            'ses',
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key
        )
        
        # Make a simple API call to verify credentials
        response = ses.get_send_quota()
        print("✅ Successfully authenticated with AWS SES!")
        print("\nAccount Send Quota:")
        print(f"  Max 24 Hour Send: {response.get('Max24HourSend')}")
        print(f"  Max Send Rate: {response.get('MaxSendRate')}/second")
        print(f"  Sent Last 24 Hours: {response.get('SentLast24Hours')}")
        return True
        
    except NoCredentialsError:
        print("❌ Error: No AWS credentials found")
        print("  - Make sure AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are set")
    except PartialCredentialsError as e:
        print(f"❌ Error: Incomplete AWS credentials: {str(e)}")
    except NoRegionError:
        print("❌ Error: No AWS region configured")
        print(f"  - Make sure AWS_SES_REGION is set to a valid AWS region")
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_msg = e.response.get('Error', {}).get('Message', str(e))
        
        print(f"❌ AWS Error ({error_code}): {error_msg}")
        
        if error_code == 'InvalidClientTokenId':
            print("\nThis usually means one of the following:")
            print("1. The AWS_ACCESS_KEY_ID is incorrect")
            print("2. The AWS_SECRET_ACCESS_KEY doesn't match the access key")
            print("3. The IAM user doesn't have the necessary permissions")
        elif error_code == 'SignatureDoesNotMatch':
            print("\nThis usually means the AWS_SECRET_ACCESS_KEY is incorrect")
        elif error_code == 'AccessDenied':
            print("\nThe IAM user exists but doesn't have permission to use SES")
            print("Make sure the IAM user has the 'ses:SendEmail' permission")
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
    
    return False

if __name__ == "__main__":
    test_aws_credentials()
