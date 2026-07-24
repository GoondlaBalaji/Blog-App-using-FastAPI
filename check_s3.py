import sys
import asyncio
import boto3
from botocore.exceptions import ClientError, NoCredentialsError

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from config import settings


def check_s3_connection():
    print("====================================================")
    print("AWS S3 Connection Diagnostic Tool")
    print("====================================================")
    print(f"Bucket Name: {settings.s3_bucket_name}")
    print(f"Region:      {settings.s3_region}")
    print(f"Endpoint:    {settings.s3_endpoint_url or 'Default AWS S3'}")
    
    access_key = settings.s3_access_key_id.get_secret_value() if settings.s3_access_key_id else None
    if access_key:
        masked_key = access_key[:5] + "*" * (len(access_key) - 9) + access_key[-4:]
        print(f"Access Key:  {masked_key}")
    else:
        print("Access Key:  None")
        
    print("----------------------------------------------------")
    print("Attempting to connect to AWS S3...")

    try:
        s3 = boto3.client(
            "s3",
            region_name=settings.s3_region,
            aws_access_key_id=access_key,
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value() if settings.s3_secret_access_key else None,
            endpoint_url=settings.s3_endpoint_url,
        )
        
        # Test connection by requesting bucket metadata
        s3.head_bucket(Bucket=settings.s3_bucket_name)
        print("SUCCESS: Successfully connected to S3 bucket!")
        
        # Try to list objects
        print("Attempting to list objects in the bucket...")
        response = s3.list_objects_v2(Bucket=settings.s3_bucket_name, MaxKeys=5)
        contents = response.get("Contents", [])
        print(f"SUCCESS: Listed {len(contents)} objects (Max 5 shown):")
        for obj in contents:
            print(f" - {obj['Key']} ({obj['Size']} bytes)")
            
    except NoCredentialsError:
        print("ERROR: No AWS credentials found. Please check your Access Key ID and Secret Access Key.")
    except ClientError as e:
        error_code = str(e.response.get("Error", {}).get("Code"))
        status_code = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        
        if error_code == "404" or status_code == 404:
            print(f"ERROR: Bucket '{settings.s3_bucket_name}' was not found. Please verify the bucket name.")
        elif error_code == "403" or status_code == 403:
            print(f"ERROR: Access Denied (403 Forbidden).")
            print(f"Your AWS IAM credentials do not have permission to access the bucket '{settings.s3_bucket_name}'.")
            print("Please ensure your IAM policy grants s3:ListBucket and s3:GetObject/s3:PutObject permissions on this bucket.")
        else:
            print(f"ERROR: Connection failed: {e}")
    except Exception as e:
        print(f"ERROR: An unexpected error occurred: {e}")
        
    print("====================================================")


if __name__ == "__main__":
    check_s3_connection()
