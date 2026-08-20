"use client";

import { useEffect, useRef, useState } from "react";
import { type MediaAsset, updateContent, uploadMedia } from "@/lib/api";

type Props = {
  contentId: string | null;
  initialMedia: MediaAsset[];
  onChanged?: (media: MediaAsset[]) => void;
  onNotice?: (message: string) => void;
};

export function MasterMedia({ contentId, initialMedia, onChanged, onNotice }: Props) {
  const [media, setMedia] = useState<MediaAsset[]>(initialMedia);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setMedia(initialMedia);
  }, [contentId, initialMedia]);

  async function handleFiles(files: FileList | null) {
    if (!contentId) {
      onNotice?.("Сначала сохраните Master content, затем добавляйте медиа");
      return;
    }
    if (!files?.length) return;
    setUploading(true);
    try {
      const uploaded: MediaAsset[] = [];
      for (const file of Array.from(files)) {
        uploaded.push(await uploadMedia(file));
      }
      const next = [...media, ...uploaded];
      const saved = await updateContent(contentId, { media: next });
      setMedia(saved.media);
      onChanged?.(saved.media);
      onNotice?.(`Загружено файлов: ${uploaded.length}`);
    } catch (error) {
      onNotice?.(error instanceof Error ? error.message : "Не удалось загрузить медиа");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function remove(index: number) {
    if (!contentId) return;
    const next = media.filter((_, itemIndex) => itemIndex !== index);
    try {
      const saved = await updateContent(contentId, { media: next });
      setMedia(saved.media);
      onChanged?.(saved.media);
      onNotice?.("Медиа удалено из Master");
    } catch (error) {
      onNotice?.(error instanceof Error ? error.message : "Не удалось удалить медиа");
    }
  }

  return (
    <div className="masterMedia">
      <div className="masterMediaHeader">
        <div>
          <span className="sectionLabel">MEDIA</span>
          <strong>{media.length ? `${media.length} файлов` : "Фото и видео"}</strong>
        </div>
        <label className={`mediaUploadButton ${!contentId || uploading ? "disabled" : ""}`}>
          {uploading ? "Загрузка…" : "+ Добавить"}
          <input
            ref={inputRef}
            type="file"
            accept="image/*,video/*"
            multiple
            disabled={!contentId || uploading}
            onChange={(event) => void handleFiles(event.target.files)}
          />
        </label>
      </div>
      {!contentId ? <p className="mediaHint">Сохраните Master, чтобы включить загрузку медиа.</p> : null}
      {media.length ? (
        <div className="mediaGrid">
          {media.map((asset, index) => (
            <div className="mediaTile" key={asset.id ?? `${asset.source}-${index}`}>
              <MediaPreview asset={asset} />
              <div className="mediaTileMeta">
                <span>{String(asset.metadata?.original_name ?? asset.media_type)}</span>
                <small>{formatBytes(Number(asset.metadata?.size_bytes ?? 0))}</small>
              </div>
              <button type="button" onClick={() => void remove(index)} aria-label="Удалить медиа">×</button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function MediaPreview({ asset }: { asset: MediaAsset }) {
  const localPreview = asset.source.startsWith("http://") || asset.source.startsWith("https://");
  if (asset.media_type === "image" && localPreview) {
    return <img src={asset.source} alt={asset.alt_text || "Uploaded media"} />;
  }
  return <div className="mediaPlaceholder">{asset.media_type === "video" ? "VIDEO" : "MEDIA"}</div>;
}

function formatBytes(bytes: number) {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
