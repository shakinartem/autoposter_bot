"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type Publication,
  type Workspace,
  getWorkspace,
  listPublications,
  publishPublication,
} from "@/lib/api";
import styles from "./calendar.module.css";

const FILTERS = ["all", "scheduled", "published", "failed"] as const;
type Filter = (typeof FILTERS)[number];

export default function CalendarPage() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [publications, setPublications] = useState<Publication[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState("Загружаю календарь…");

  const load = useCallback(async () => {
    try {
      const [workspaceData, publicationData] = await Promise.all([
        getWorkspace(),
        listPublications(),
      ]);
      setWorkspace(workspaceData);
      setPublications(publicationData);
      setNotice("Календарь синхронизирован");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось загрузить календарь");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const visible = useMemo(
    () => publications.filter((item) => filter === "all" || item.status === filter),
    [filter, publications],
  );

  const grouped = useMemo(() => {
    const groups = new Map<string, Publication[]>();
    for (const item of visible) {
      const rawDate = item.scheduled_at ?? item.published_at;
      const key = rawDate ? new Date(rawDate).toISOString().slice(0, 10) : "unscheduled";
      const rows = groups.get(key) ?? [];
      rows.push(item);
      groups.set(key, rows);
    }
    return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right));
  }, [visible]);

  const stats = useMemo(
    () => ({
      scheduled: publications.filter((item) => item.status === "scheduled").length,
      published: publications.filter((item) => item.status === "published").length,
      failed: publications.filter((item) => item.status === "failed").length,
      total: publications.length,
    }),
    [publications],
  );

  async function retry(publication: Publication) {
    setBusyId(publication.id);
    try {
      const result = await publishPublication(publication.id, false);
      setNotice(result.ok ? `${publication.platform}: публикация отправлена повторно` : result.error_message ?? "Retry failed");
      await load();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Retry failed");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>{workspace ? workspace.name : "Workspace"}</p>
          <h1>Publication calendar</h1>
          <p>Единый контроль расписания, опубликованных материалов и ошибок по всем площадкам.</p>
        </div>
        <button className={styles.refresh} type="button" onClick={() => void load()}>
          Обновить
        </button>
      </section>

      <section className={styles.stats} aria-label="Статистика публикаций">
        <Stat label="В расписании" value={stats.scheduled} />
        <Stat label="Опубликовано" value={stats.published} />
        <Stat label="Ошибки" value={stats.failed} />
        <Stat label="Всего" value={stats.total} />
      </section>

      <p className={styles.notice}>{notice}</p>

      <div className={styles.toolbar}>
        {FILTERS.map((item) => (
          <button
            key={item}
            type="button"
            className={`${styles.filter} ${filter === item ? styles.active : ""}`}
            onClick={() => setFilter(item)}
          >
            {filterLabel(item)}
          </button>
        ))}
      </div>

      {grouped.length === 0 ? (
        <div className={styles.empty}>В этом фильтре пока нет публикаций.</div>
      ) : (
        <section className={styles.days}>
          {grouped.map(([date, rows]) => (
            <article className={styles.day} key={date}>
              <header className={styles.dayHeader}>
                <strong>{date === "unscheduled" ? "Без даты" : formatDate(date)}</strong>
                <span>{rows.length} публикаций</span>
              </header>
              <div className={styles.items}>
                {rows.map((item) => (
                  <div className={styles.item} key={item.id}>
                    <div className={styles.time}>{formatTime(item.scheduled_at ?? item.published_at)}</div>
                    <div className={styles.platform}>{item.platform}</div>
                    <div className={styles.meta}>
                      <strong>{item.destination || `Account #${item.account_id}`}</strong>
                      <small>{item.last_error_message || item.external_post_id || item.id}</small>
                    </div>
                    <div className={styles.actions}>
                      <span className={`${styles.badge} ${statusClass(item.status)}`}>{item.status}</span>
                      {item.status === "failed" ? (
                        <button
                          className={styles.retry}
                          type="button"
                          disabled={busyId === item.id}
                          onClick={() => void retry(item)}
                        >
                          {busyId === item.id ? "Повтор…" : "Retry"}
                        </button>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            </article>
          ))}
        </section>
      )}
    </main>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className={styles.stat}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function filterLabel(filter: Filter) {
  if (filter === "all") return "Все";
  if (filter === "scheduled") return "Расписание";
  if (filter === "published") return "Опубликовано";
  return "Ошибки";
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(new Date(`${value}T12:00:00`));
}

function formatTime(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function statusClass(status: string) {
  if (status === "failed") return styles.failed;
  if (status === "published") return styles.published;
  if (status === "scheduled") return styles.scheduled;
  return "";
}
