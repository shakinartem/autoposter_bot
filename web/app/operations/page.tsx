"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getOperationsOverview, type OperationsOverview } from "@/lib/api";
import styles from "./operations.module.css";

function seconds(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 60) return `${value}с`;
  if (value < 3600) return `${Math.floor(value / 60)}м`;
  if (value < 86400) return `${Math.floor(value / 3600)}ч`;
  return `${Math.floor(value / 86400)}д`;
}

function date(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export default function OperationsPage() {
  const [overview, setOverview] = useState<OperationsOverview | null>(null);
  const [notice, setNotice] = useState("Загружаю состояние системы…");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const payload = await getOperationsOverview();
      setOverview(payload);
      setNotice(`Срез обновлён ${date(payload.generated_at)}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось получить состояние системы");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const statusRows = useMemo(
    () => Object.entries(overview?.publication_statuses ?? {}).sort(([a], [b]) => a.localeCompare(b)),
    [overview],
  );

  return (
    <main className={styles.page}>
      <section className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>OPERATIONS CONTROL</p>
          <h1>Здоровье публикационного контура</h1>
          <p>
            Очередь, provider processing, неизвестные исходы, ошибки попыток и свежесть аналитики — в одном workspace-scoped срезе.
          </p>
        </div>
        <button className={styles.refresh} type="button" disabled={busy} onClick={() => void load()}>
          {busy ? "Обновляю…" : "Обновить"}
        </button>
      </section>

      <div className={styles.healthRow}>
        <div className={`${styles.healthBadge} ${styles[overview?.health ?? "loading"]}`}>
          <span className={styles.pulse} />
          {overview?.health ?? "loading"}
        </div>
        <span className={styles.notice}>{notice}</span>
      </div>

      <section className={styles.kpis}>
        <article className={styles.kpi}>
          <span>Queue lag</span>
          <strong>{seconds(overview?.queue.lag_seconds)}</strong>
          <small>{overview?.queue.due_count ?? 0} просрочено · {overview?.queue.retry_scheduled ?? 0} retry</small>
        </article>
        <article className={styles.kpi}>
          <span>Provider processing</span>
          <strong>{overview?.reconciliation.processing ?? 0}</strong>
          <small>{overview?.reconciliation.unknown_outcomes ?? 0} unknown outcome</small>
        </article>
        <article className={styles.kpi}>
          <span>Failure rate · 24ч</span>
          <strong>{overview ? `${Math.round(overview.attempts_24h.failure_rate * 100)}%` : "—"}</strong>
          <small>{overview?.attempts_24h.failed ?? 0} из {overview?.attempts_24h.total ?? 0} попыток</small>
        </article>
        <article className={styles.kpi}>
          <span>Analytics lag</span>
          <strong>{seconds(overview?.analytics.lag_seconds)}</strong>
          <small>последний snapshot {date(overview?.analytics.latest_snapshot_at)}</small>
        </article>
      </section>

      <div className={styles.grid}>
        <section className={styles.panel}>
          <header>
            <div>
              <p className={styles.eyebrow}>PUBLICATION STATE</p>
              <h2>Состояния публикаций</h2>
            </div>
            <span>{statusRows.reduce((sum, [, value]) => sum + value, 0)}</span>
          </header>
          <div className={styles.statusList}>
            {statusRows.map(([status, count]) => (
              <div className={styles.statusRow} key={status}>
                <span>{status}</span>
                <strong>{count}</strong>
              </div>
            ))}
            {!statusRows.length ? <p className={styles.empty}>Публикаций пока нет.</p> : null}
          </div>
        </section>

        <section className={styles.panel}>
          <header>
            <div>
              <p className={styles.eyebrow}>RECOVERY</p>
              <h2>Что требует внимания</h2>
            </div>
          </header>
          <div className={styles.attentionList}>
            <article>
              <div>
                <strong>Unknown publish outcome</strong>
                <span>Автоповтор заблокирован до reconciliation, чтобы не создать дубль.</span>
              </div>
              <b>{overview?.reconciliation.unknown_outcomes ?? 0}</b>
            </article>
            <article>
              <div>
                <strong>Старейшая просроченная задача</strong>
                <span>{date(overview?.queue.oldest_due_at)}</span>
              </div>
              <b>{seconds(overview?.queue.lag_seconds)}</b>
            </article>
            <article>
              <div>
                <strong>Опубликовано за 24ч</strong>
                <span>Последняя: {date(overview?.publishing.latest_published_at)}</span>
              </div>
              <b>{overview?.publishing.published_24h ?? 0}</b>
            </article>
          </div>
        </section>
      </div>
    </main>
  );
}
