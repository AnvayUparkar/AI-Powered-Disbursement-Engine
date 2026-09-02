import os
import asyncio
from typing import Optional, Union
from idp.core.config import settings
from idp.core.exceptions import S3Error
from idp.core.logging import logger, format_doc_log


class S3Storage:
    """Provider-agnostic S3 Storage service wrapper with boto3 / local fallback."""

    def __init__(self, region: str = settings.AWS_REGION, bucket: str = settings.S3_BUCKET):
        self.region = region
        self.default_bucket = bucket
        self._boto_client = None

    def _get_client(self):
        if self._boto_client is None:
            try:
                import boto3
                kwargs = {"region_name": self.region}
                if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
                    kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
                    kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
                if settings.S3_ENDPOINT_URL:
                    kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
                self._boto_client = boto3.client("s3", **kwargs)
            except Exception as e:
                logger.warning(f"boto3 client initialization failed or AWS credentials unavailable: {e}. Operating in local/mock mode.")
                self._boto_client = "MOCK"
        return self._boto_client

    async def download(
        self,
        key: str,
        dest_path: str,
        bucket: Optional[str] = None,
        doc_id: str = "DOC"
    ) -> str:
        """Download object from S3 to local filesystem dest_path."""
        target_bucket = bucket or self.default_bucket
        logger.info(format_doc_log(doc_id, f"Downloading s3://{target_bucket}/{key} -> {dest_path}"))

        client = self._get_client()
        if client == "MOCK" or not os.getenv("AWS_ACCESS_KEY_ID"):
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            import shutil

            local_mock_dir = os.path.join(settings.TEMP_DIR, "s3_mock", target_bucket)
            mock_path = os.path.join(local_mock_dir, key)

            if os.path.exists(mock_path):
                shutil.copyfile(mock_path, dest_path)
                logger.info(format_doc_log(doc_id, f"[Mock] Copied uploaded s3_mock file: {mock_path} -> {dest_path}"))
                return dest_path
            elif os.path.exists(key):
                shutil.copyfile(key, dest_path)
                return dest_path
            elif os.path.exists(dest_path):
                return dest_path
            else:
                if not os.path.exists(dest_path):
                    with open(dest_path, "wb") as f:
                        f.write(b"%PDF-1.4 Mock Document Content for Testing")
                return dest_path

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: client.download_file(target_bucket, key, dest_path)
            )
            logger.info(format_doc_log(doc_id, f"Successfully downloaded s3://{target_bucket}/{key}"))
            return dest_path
        except Exception as e:
            logger.error(format_doc_log(doc_id, f"S3 Download Error for s3://{target_bucket}/{key}: {e}"))
            raise S3Error(f"Failed to download s3://{target_bucket}/{key}", details=str(e))

    async def upload(
        self,
        key: str,
        content: Union[str, bytes],
        bucket: Optional[str] = None,
        content_type: str = "application/json",
        doc_id: str = "DOC"
    ) -> str:
        """Upload content string or bytes to S3 key location."""
        target_bucket = bucket or self.default_bucket
        logger.info(format_doc_log(doc_id, f"Uploading to s3://{target_bucket}/{key}"))

        if isinstance(content, str):
            body_bytes = content.encode("utf-8")
        else:
            body_bytes = content

        client = self._get_client()
        if client == "MOCK" or not os.getenv("AWS_ACCESS_KEY_ID"):
            # Local filesystem mock store
            local_mock_dir = os.path.join(settings.TEMP_DIR, "s3_mock", target_bucket)
            os.makedirs(os.path.dirname(os.path.join(local_mock_dir, key)), exist_ok=True)
            mock_path = os.path.join(local_mock_dir, key)
            with open(mock_path, "wb") as f:
                f.write(body_bytes)
            output_url = f"s3://{target_bucket}/{key}"
            logger.info(format_doc_log(doc_id, f"[Mock] Saved uploaded content to local mock path: {mock_path}"))
            return output_url

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: client.put_object(
                    Bucket=target_bucket,
                    Key=key,
                    Body=body_bytes,
                    ContentType=content_type
                )
            )
            output_url = f"s3://{target_bucket}/{key}"
            logger.info(format_doc_log(doc_id, f"Successfully uploaded {output_url}"))
            return output_url
        except Exception as e:
            logger.error(format_doc_log(doc_id, f"S3 Upload Error for s3://{target_bucket}/{key}: {e}"))
            raise S3Error(f"Failed to upload to s3://{target_bucket}/{key}", details=str(e))
