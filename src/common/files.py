"""The private S3 bucket holding uploaded photos. Every key starts with the account id, so a
request for a photo is checked against its account by key prefix before a short-lived presigned
GET URL is handed out."""

import boto3

EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png"}


class Files:
    """Writes photos under their account's prefix, reads them back, and signs read URLs for
    them."""

    def __init__(self, bucket: str):
        self._bucket = bucket
        self._s3 = boto3.client("s3")

    def put_image(self, account, seq, data: bytes, media_type: str) -> str:
        key = f"{account}/images/{seq}.{EXTENSIONS[media_type]}"
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=media_type)
        return key

    def get_image(self, key) -> tuple[bytes, str]:
        """The photo's bytes and content type."""
        stored = self._s3.get_object(Bucket=self._bucket, Key=key)
        return stored["Body"].read(), stored["ContentType"]

    def url(self, key, minutes) -> str:
        return self._s3.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=minutes * 60
        )
