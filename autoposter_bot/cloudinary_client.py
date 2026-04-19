from __future__ import annotations

from pathlib import Path

from autoposter_bot.config import Settings


class CloudinaryClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_configured(self) -> bool:
        return all(
            [
                self.settings.cloudinary_cloud_name,
                self.settings.cloudinary_api_key,
                self.settings.cloudinary_api_secret,
            ]
        )

    def upload_media(self, source: str, media_type: str, public_id_prefix: str) -> str:
        if source.startswith("http://") or source.startswith("https://"):
            return source
        if not self.is_configured():
            raise ValueError("Cloudinary не настроен. Заполните CLOUDINARY_CLOUD_NAME/API_KEY/API_SECRET в .env")

        import cloudinary
        import cloudinary.uploader

        cloudinary.config(
            cloud_name=self.settings.cloudinary_cloud_name,
            api_key=self.settings.cloudinary_api_key,
            api_secret=self.settings.cloudinary_api_secret,
            secure=True,
        )

        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Файл не найден: {source}")

        resource_type = "video" if media_type == "video" else "image"
        result = cloudinary.uploader.upload(
            str(path),
            resource_type=resource_type,
            folder=self.settings.cloudinary_folder or "autoposter",
            public_id=f"{public_id_prefix}-{path.stem}",
            overwrite=True,
            unique_filename=False,
        )
        secure_url = result.get("secure_url")
        if not secure_url:
            raise RuntimeError(f"Cloudinary upload failed: {result}")
        return secure_url
