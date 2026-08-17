"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type ContentItem,
  type PlatformCapability,
  type PlatformVariant,
  type SocialAccount,
  type Workspace,
  createContent,
  createPublication,
  getWorkspace,
  listAccounts,
  listContent,
  listPlatforms,
  listVariants,
  publishPublication,
  updateContent,
  upsertVariant,
} from "@/lib/api";
import { PlatformFields, capabilityDefaults } from "@/components/platform-fields";

const PLATFORM_LABELS: Record<string, string> = {
  telegram: "Telegram",
  vk: "VK",
  instagram: "Instagram",
  tiktok: "TikTok",
  youtube: "YouTube",
  linkedin: "LinkedIn",
};

export function Composer() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [platforms, setPlatforms] = useState<Record<string, PlatformCapability>>({});
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [content, setContent] = useState<ContentItem[]>([]);
  const [activeContent, setActiveContent] = useState<ContentItem | null>(null);
  const [variants, setVariants] = useState<Record<string, PlatformVariant>>({});
  const [activePlatform, setActivePlatform] = useState("telegram");
  const [masterTitle, setMasterTitle] = useState("");
  const [masterBody, setMasterBody] = useState("");
  const [variantTitle, setVariantTitle] = useState("");
  const [variantText, setVariantText] = useState("");
  const [variantFields, setVariantFields] = useState<Record<string, unknown>>({});
  const [selectedAccount, setSelectedAccount] = useState<number | null>(null);
  const [scheduledAt, setScheduledAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("Подключение к API…");
  const [apiOnline, setApiOnline] = useState(false);

  const loadVariants = useCallback(async (item: ContentItem) => {
    const rows = await listVariants(item.id);
    setVariants(Object.fromEntries(rows.map((variant) => [variant.platform, variant])));
  }, []);

  const selectContent = useCallback(async (item: ContentItem) => {
    setActiveContent(item);
    setMasterTitle(item.title);
    setMasterBody(item.body);
    await loadVariants(item);
  }, [loadVariants]);

  const bootstrap = useCallback(async () => {
    try {
      const [workspaceData, platformData, accountData, contentData] = await Promise.all([
        getWorkspace(),
        listPlatforms(),
        listAccounts(),
        listContent(),
      ]);
      setWorkspace(workspaceData);
      setPlatforms(platformData);
      setAccounts(accountData);
      setContent(contentData);
      const firstPlatform = Object.keys(platformData)[0];
      if (firstPlatform) setActivePlatform(firstPlatform);
      if (contentData[0]) await selectContent(contentData[0]);
      setApiOnline(true);
      setNotice("API подключён");
    } catch (error) {
      setApiOnline(false);
      setNotice(error instanceof Error ? error.message : "API недоступен");
    }
  }, [selectContent]);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    const variant = variants[activePlatform];
    const capability = platforms[activePlatform];
    if (variant) {
      setVariantTitle(variant.title);
      setVariantText(variant.text);
      setVariantFields({ ...capabilityDefaults(capability), ...variant.fields });
    } else {
      setVariantTitle(masterTitle);
      setVariantText(masterBody);
      setVariantFields(capabilityDefaults(capability));
    }
    const firstAccount = accounts.find((account) => account.platform.toLowerCase() === activePlatform);
    setSelectedAccount(firstAccount?.id ?? null);
  }, [activePlatform, accounts, masterBody, masterTitle, platforms, variants]);

  const platformAccounts = useMemo(
    () => accounts.filter((account) => account.platform.toLowerCase() === activePlatform),
    [accounts, activePlatform],
  );
  const activeVariant = variants[activePlatform];
  const capability = platforms[activePlatform];

  async function saveMaster() {
    if (!apiOnline) return;
    setBusy(true);
    try {
      let saved: ContentItem;
      if (activeContent) {
        saved = await updateContent(activeContent.id, { title: masterTitle, body: masterBody });
        setContent((items) => items.map((item) => (item.id === saved.id ? saved : item)));
      } else {
        saved = await createContent({ title: masterTitle, body: masterBody });
        setContent((items) => [saved, ...items]);
      }
      setActiveContent(saved);
      await loadVariants(saved);
      setNotice("Master content сохранён");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось сохранить content");
    } finally {
      setBusy(false);
    }
  }

  async function savePlatformVariant() {
    if (!activeContent) {
      setNotice("Сначала сохраните Master content");
      return null;
    }
    setBusy(true);
    try {
      const variant = await upsertVariant(activeContent.id, activePlatform, {
        title: variantTitle,
        text: variantText,
        fields: variantFields,
        sync_with_master: false,
      });
      setVariants((current) => ({ ...current, [activePlatform]: variant }));
      setNotice(`${label(activePlatform)} сохранён как platform-native версия`);
      return variant;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось сохранить variant");
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function toggleSync() {
    if (!activeContent) return;
    setBusy(true);
    try {
      const variant = await upsertVariant(activeContent.id, activePlatform, {
        sync_with_master: !(activeVariant?.sync_with_master ?? true),
      });
      setVariants((current) => ({ ...current, [activePlatform]: variant }));
      setVariantTitle(variant.title);
      setVariantText(variant.text);
      setVariantFields({ ...capabilityDefaults(capability), ...variant.fields });
      setNotice(variant.sync_with_master ? "Синхронизация с Master включена" : "Версия отвязана от Master");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось изменить sync");
    } finally {
      setBusy(false);
    }
  }

  async function ensureVariant() {
    if (!activeContent) return null;
    if (activeVariant) return activeVariant;
    const variant = await upsertVariant(activeContent.id, activePlatform, { fields: variantFields });
    setVariants((current) => ({ ...current, [activePlatform]: variant }));
    return variant;
  }

  async function handlePublish(mode: "now" | "schedule" | "dry") {
    if (!selectedAccount) {
      setNotice(`Нет подключённого аккаунта ${label(activePlatform)}`);
      return;
    }
    if (mode === "schedule" && !scheduledAt) {
      setNotice("Укажите дату и время публикации");
      return;
    }
    setBusy(true);
    try {
      const variant = await ensureVariant();
      if (!variant) {
        setNotice("Сначала создайте и сохраните content");
        return;
      }
      const publication = await createPublication(variant.id, {
        account_id: selectedAccount,
        scheduled_at: mode === "schedule" ? new Date(scheduledAt).toISOString() : null,
      });
      if (mode === "schedule") {
        setNotice(`${label(activePlatform)} поставлен в расписание`);
        return;
      }
      const result = await publishPublication(publication.id, mode === "dry");
      setNotice(
        result.ok
          ? mode === "dry" ? "Dry-run прошёл успешно" : `${label(activePlatform)} опубликован`
          : result.error_message || "Площадка вернула ошибку публикации",
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось создать публикацию");
    } finally {
      setBusy(false);
    }
  }

  function newContent() {
    setActiveContent(null);
    setVariants({});
    setMasterTitle("");
    setMasterBody("");
    setVariantTitle("");
    setVariantText("");
    setVariantFields(capabilityDefaults(capability));
    setNotice("Новый Master content");
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brandMark">A</div>
          <div><strong>Autoposter</strong><span>{workspace?.name ?? "Content Distribution OS"}</span></div>
        </div>
        <button className="createButton" onClick={newContent}>+ Создать контент</button>
        <nav className="nav">
          <button className="navItem active">Composer <span>⌘K</span></button>
          <Link className="navItem" href="/calendar">Календарь</Link>
        </nav>
        <div className="library">
          <div className="sectionLabel">Последний контент</div>
          {content.slice(0, 7).map((item) => (
            <button key={item.id} className={`libraryItem ${activeContent?.id === item.id ? "selected" : ""}`} onClick={() => void selectContent(item)}>
              <span className="libraryDot" /><span>{item.title || firstLine(item.body) || "Без названия"}</span>
            </button>
          ))}
          {!content.length ? <p className="emptySmall">Здесь появятся ваши материалы.</p> : null}
        </div>
        <div className="sidebarFooter">
          <span className={`statusDot ${apiOnline ? "online" : ""}`} />
          <div><strong>{apiOnline ? "Backend online" : "Backend offline"}</strong><span>workspace #{workspace?.id ?? "—"}</span></div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div><span className="eyebrow">CONTENT / COMPOSER</span><h1>{activeContent ? masterTitle || "Без названия" : "Новый материал"}</h1></div>
          <div className="topActions">
            <span className={`connectionBadge ${apiOnline ? "ok" : "error"}`}>{notice}</span>
            <button className="ghostButton" onClick={() => void handlePublish("dry")} disabled={busy || !activeContent}>Проверить</button>
            <button className="primaryButton" onClick={() => void handlePublish("now")} disabled={busy || !activeContent}>Опубликовать</button>
          </div>
        </header>

        <div className="workspace">
          <section className="editorPanel">
            <div className="masterCard">
              <div className="cardHeader">
                <div><span className="eyebrow">MASTER CONTENT</span><h2>Одна идея — разные площадки</h2></div>
                <button className="saveButton" onClick={() => void saveMaster()} disabled={busy || !apiOnline}>{busy ? "Сохраняю…" : "Сохранить master"}</button>
              </div>
              <input className="titleInput" value={masterTitle} onChange={(event) => setMasterTitle(event.target.value)} placeholder="Название материала" />
              <textarea className="masterTextarea" value={masterBody} onChange={(event) => setMasterBody(event.target.value)} placeholder="Исходный материал для адаптации под площадки…" />
              <div className="masterFooter"><span>{masterBody.length} символов</span><span>Master source</span></div>
            </div>

            <div className="platformSection">
              <div className="platformTabs" role="tablist">
                {Object.keys(platforms).map((platform) => (
                  <button key={platform} className={`platformTab ${activePlatform === platform ? "active" : ""}`} onClick={() => setActivePlatform(platform)}>
                    <span className={`platformIcon ${platform}`}>{label(platform).slice(0, 2)}</span>{label(platform)}
                    {variants[platform] ? <span className={`syncMini ${variants[platform].sync_with_master ? "synced" : "locked"}`} /> : null}
                  </button>
                ))}
              </div>

              <div className="variantCard">
                <div className="variantHeader">
                  <div><span className="eyebrow">PLATFORM VARIANT</span><h2>{label(activePlatform)}</h2></div>
                  <button className={`syncButton ${activeVariant?.sync_with_master !== false ? "synced" : ""}`} onClick={() => void toggleSync()} disabled={!activeContent || busy}>
                    {activeVariant?.sync_with_master !== false ? "↻ Синхронизирован" : "◇ Отдельная версия"}
                  </button>
                </div>
                <div className="capabilities">{(capability?.content_types ?? []).slice(0, 5).map((type) => <span key={type}>{type}</span>)}</div>
                <label className="fieldLabel">Заголовок / служебное название</label>
                <input className="variantTitleInput" value={variantTitle} onChange={(event) => setVariantTitle(event.target.value)} placeholder={`${label(activePlatform)} title`} />
                <label className="fieldLabel">Текст публикации</label>
                <textarea className="variantTextarea" value={variantText} onChange={(event) => setVariantText(event.target.value)} placeholder={`Версия для ${label(activePlatform)}`} />
                <PlatformFields capability={capability} value={variantFields} onChange={setVariantFields} />
                <div className="variantFooter">
                  <div className="metaRow"><span>{variantText.length} символов</span><span>revision {activeVariant?.revision ?? 1}</span></div>
                  <button className="secondaryButton" onClick={() => void savePlatformVariant()} disabled={!activeContent || busy}>Сохранить версию</button>
                </div>
              </div>
            </div>
          </section>

          <aside className="publishPanel">
            <div className="publishCard">
              <span className="eyebrow">DISTRIBUTION</span><h3>Публикация</h3>
              <label className="fieldLabel">Аккаунт</label>
              <select className="selectInput" value={selectedAccount ?? ""} onChange={(event) => setSelectedAccount(event.target.value ? Number(event.target.value) : null)}>
                <option value="">Выберите аккаунт</option>
                {platformAccounts.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}
              </select>
              {!platformAccounts.length ? <p className="hint warning">Для {label(activePlatform)} нет аккаунта в этом workspace.</p> : null}
              <label className="fieldLabel">Дата и время</label>
              <input className="selectInput" type="datetime-local" value={scheduledAt} onChange={(event) => setScheduledAt(event.target.value)} />
              <button className="primaryButton full" onClick={() => void handlePublish("schedule")} disabled={busy || !activeContent || !selectedAccount}>Поставить в календарь</button>
              <button className="ghostButton full" onClick={() => void handlePublish("now")} disabled={busy || !activeContent || !selectedAccount}>Опубликовать сейчас</button>
            </div>
            <div className="insightCard">
              <span className="eyebrow">PLATFORM CAPABILITIES</span>
              <div className="capabilityList">
                {Object.entries(capability?.features ?? {}).map(([feature, enabled]) => <div key={feature}><span>{humanize(feature)}</span><strong>{enabled ? "Да" : "Нет"}</strong></div>)}
              </div>
            </div>
            <div className="architectureNote"><strong>Content → Variant → Publication</strong><p>Каждая площадка имеет свой формат, настройки и независимый lifecycle.</p></div>
          </aside>
        </div>
      </main>
    </div>
  );
}

function label(platform: string) {
  return PLATFORM_LABELS[platform] ?? platform.charAt(0).toUpperCase() + platform.slice(1);
}

function firstLine(value: string) {
  return value.split("\n")[0]?.trim().slice(0, 42) ?? "";
}

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/^./, (match) => match.toUpperCase());
}
