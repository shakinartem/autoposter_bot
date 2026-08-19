"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getOperationsOverview,
  listOperationsEvents,
  listReconciliationItems,
  resolveReconciliation,
  type OperationsEvent,
  type OperationsOverview,
  type ReconciliationItem,
} from "@/lib/api";
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

function age(value: string | null | undefined): string {
  if (!value) return "—";
  const delta = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  return seconds(delta);
}

function reasonLabel(code: string): string {
  const labels: Record<string, string> = {
    unknown_publish_outcome: "Неизвестный исход remote publish",
    queue_lag: "Очередь публикаций отстаёт",
    stale_provider_processing: "Provider processing завис",
    attempt_failure_rate: "Высокая доля ошибок публикации",
  };
  return labels[code] ?? code;
}

export default function OperationsPage() {
  const [overview, setOverview] = useState<OperationsOverview | null>(null);
  const [reconciliation, setReconciliation] = useState<ReconciliationItem[]>([]);
  const [events, setEvents] = useState<OperationsEvent[]>([]);
  const [notice, setNotice] = useState("Загружаю состояние системы…");
  const [busy, setBusy] = useState(false);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [remoteIds, setRemoteIds] = useState<Record<string, string>>({});
  const [remoteUrls, setRemoteUrls] = useState<Record<string, string>>({});
  const [retryAck, setRetryAck] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [overviewPayload, recoveryPayload, eventPayload] = await Promise.all([
        getOperationsOverview(),
        listReconciliationItems(),
        listOperationsEvents(30),
      ]);
      setOverview(overviewPayload);
      setReconciliation(recoveryPayload);
      setEvents(eventPayload);
      setNotice(`Срез обновлён ${date(overviewPayload.generated_at)}`);
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

  const resolve = useCallback(
    async (item: ReconciliationItem, action: "confirm_published" | "mark_failed" | "retry") => {
      const note = (notes[item.id] ?? "").trim();
      if (note.length < 3) {
        setNotice("Для ручного recovery нужна заметка: что именно было проверено.");
        return;
      }
      if (action === "confirm_published" && !(remoteIds[item.id] ?? item.external_post_id ?? "").trim()) {
        setNotice("Для confirm published нужен финальный remote post ID.");
        return;
      }
      if (action === "retry" && !retryAck[item.id]) {
        setNotice("Для retry нужно явно подтвердить, что платформа проверена и риск дубля принят.");
        return;
      }

      setActionBusy(item.id);
      try {
        await resolveReconciliation(item.id, {
          action,
          note,
          external_post_id: (remoteIds[item.id] ?? item.external_post_id ?? "").trim() || null,
          external_url: (remoteUrls[item.id] ?? item.external_url ?? "").trim() || null,
          acknowledge_duplicate_risk: action === "retry" ? Boolean(retryAck[item.id]) : false,
        });
        setNotes((current) => ({ ...current, [item.id]: "" }));
        setRetryAck((current) => ({ ...current, [item.id]: false }));
        await load();
        setNotice(`Recovery action «${action}» сохранён в audit log.`);
      } catch (error) {
        setNotice(error instanceof Error ? error.message : "Не удалось применить recovery action");
      } finally {
        setActionBusy(null);
      }
    },
    [load, notes, remoteIds, remoteUrls, retryAck],
  );

  return (
    <main className={styles.page}>
      <section className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>OPERATIONS CONTROL</p>
          <h1>Здоровье публикационного контура</h1>
          <p>
            Очередь, provider processing, неизвестные исходы, ошибки попыток и recovery — в одном workspace-scoped контуре.
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

      {overview?.health_reasons?.length ? (
        <section className={styles.reasons} aria-label="Причины состояния системы">
          {overview.health_reasons.map((reason) => (
            <article className={styles[reason.severity]} key={`${reason.code}-${reason.count ?? reason.value ?? 0}`}>
              <strong>{reasonLabel(reason.code)}</strong>
              <span>{reason.count ?? (reason.value != null ? reason.value : "требует внимания")}</span>
            </article>
          ))}
        </section>
      ) : null}

      <section className={styles.kpis}>
        <article className={styles.kpi}>
          <span>Queue lag</span>
          <strong>{seconds(overview?.queue.lag_seconds)}</strong>
          <small>{overview?.queue.due_count ?? 0} просрочено · {overview?.queue.retry_scheduled ?? 0} retry</small>
        </article>
        <article className={styles.kpi}>
          <span>Provider processing</span>
          <strong>{overview?.reconciliation.processing ?? 0}</strong>
          <small>{overview?.reconciliation.stale_processing ?? 0} stale · {overview?.reconciliation.unknown_outcomes ?? 0} unknown</small>
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

      <section className={`${styles.panel} ${styles.recoveryPanel}`}>
        <header>
          <div>
            <p className={styles.eyebrow}>MANUAL RECONCILIATION</p>
            <h2>Публикации, требующие решения</h2>
          </div>
          <span>{reconciliation.length}</span>
        </header>
        <div className={styles.recoveryList}>
          {reconciliation.map((item) => {
            const disabled = actionBusy === item.id;
            return (
              <article className={styles.recoveryCard} key={item.id}>
                <div className={styles.recoveryHead}>
                  <div>
                    <span className={styles.platform}>{item.platform}</span>
                    <h3>{item.content_title || "Без названия"}</h3>
                    <p>{item.last_error_message || item.last_error_code || "Provider ещё обрабатывает публикацию"}</p>
                  </div>
                  <div className={styles.stateStack}>
                    <b>{item.status}</b>
                    <span>в processing {age(item.processing_started_at ?? item.updated_at)}</span>
                  </div>
                </div>

                <dl className={styles.evidence}>
                  <div><dt>Tracking ID</dt><dd>{item.provider_tracking_id || "—"}</dd></div>
                  <div><dt>Remote post ID</dt><dd>{item.external_post_id || "—"}</dd></div>
                  <div><dt>Attempt</dt><dd>#{item.attempt_count}</dd></div>
                  <div><dt>Destination</dt><dd>{item.destination || "—"}</dd></div>
                </dl>

                <div className={styles.recoveryForm}>
                  <label>
                    <span>Что проверили</span>
                    <textarea
                      value={notes[item.id] ?? ""}
                      onChange={(event) => setNotes((current) => ({ ...current, [item.id]: event.target.value }))}
                      placeholder="Например: проверил профиль и provider dashboard — публикации нет"
                    />
                  </label>
                  <div className={styles.remoteFields}>
                    <label>
                      <span>Финальный remote post ID</span>
                      <input
                        value={remoteIds[item.id] ?? item.external_post_id ?? ""}
                        onChange={(event) => setRemoteIds((current) => ({ ...current, [item.id]: event.target.value }))}
                        placeholder="Нужен для confirm published"
                      />
                    </label>
                    <label>
                      <span>Remote URL · optional</span>
                      <input
                        value={remoteUrls[item.id] ?? item.external_url ?? ""}
                        onChange={(event) => setRemoteUrls((current) => ({ ...current, [item.id]: event.target.value }))}
                        placeholder="https://…"
                      />
                    </label>
                  </div>
                  {item.can_retry ? (
                    <label className={styles.riskCheck}>
                      <input
                        type="checkbox"
                        checked={Boolean(retryAck[item.id])}
                        onChange={(event) => setRetryAck((current) => ({ ...current, [item.id]: event.target.checked }))}
                      />
                      <span>Я проверил площадку и принимаю риск дубля при повторной отправке.</span>
                    </label>
                  ) : (
                    <p className={styles.retryBlocked}>Retry заблокирован: уже существует durable remote identity.</p>
                  )}
                </div>

                <div className={styles.actions}>
                  <button disabled={disabled} type="button" onClick={() => void resolve(item, "confirm_published")}>Confirm published</button>
                  <button disabled={disabled} type="button" onClick={() => void resolve(item, "mark_failed")}>Закрыть failed</button>
                  <button className={styles.danger} disabled={disabled || !item.can_retry || !retryAck[item.id]} type="button" onClick={() => void resolve(item, "retry")}>Разрешить retry</button>
                </div>
              </article>
            );
          })}
          {!reconciliation.length ? <p className={styles.empty}>Нет публикаций, требующих ручного reconciliation.</p> : null}
        </div>
      </section>

      <section className={`${styles.panel} ${styles.eventsPanel}`}>
        <header>
          <div>
            <p className={styles.eyebrow}>AUDIT TRAIL</p>
            <h2>Последние recovery events</h2>
          </div>
        </header>
        <div className={styles.eventList}>
          {events.slice(0, 12).map((event) => (
            <article key={event.id}>
              <div>
                <strong>{event.event_type.replace("publication_reconciliation.", "")}</strong>
                <span>{event.publication_id || "—"}</span>
              </div>
              <time>{date(event.created_at)}</time>
            </article>
          ))}
          {!events.length ? <p className={styles.empty}>Ручных recovery actions ещё не было.</p> : null}
        </div>
      </section>
    </main>
  );
}
