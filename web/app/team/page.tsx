"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./team.module.css";

type SessionProfile = {
  authenticated: boolean;
  role?: string;
  workspace_id?: number;
};

type WorkspaceMember = {
  user_id: number;
  role: string;
  username?: string | null;
  full_name?: string | null;
  telegram_user_id?: number | null;
  created_at: string;
};

type Invitation = {
  id: string;
  workspace_id: number;
  workspace_name: string;
  role: string;
  created_by_user_id?: number | null;
  created_at: string;
  expires_at: string;
};

type CreatedInvitation = Invitation & { token: string };

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/autoposter${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload?.detail === "string" ? payload.detail : `Request failed: ${response.status}`);
  }
  return payload as T;
}

export default function TeamPage() {
  const [profile, setProfile] = useState<SessionProfile | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [role, setRole] = useState("editor");
  const [hours, setHours] = useState(168);
  const [createdLink, setCreatedLink] = useState("");
  const [notice, setNotice] = useState("Загружаю команду…");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const sessionResponse = await fetch("/api/session", { cache: "no-store" });
      const session = (await sessionResponse.json().catch(() => ({}))) as SessionProfile;
      setProfile(sessionResponse.ok ? session : { authenticated: false });
      if (!sessionResponse.ok) {
        setNotice("Войдите, чтобы управлять командой");
        return;
      }
      const [memberRows, invitationRows] = await Promise.all([
        api<WorkspaceMember[]>("/members"),
        api<Invitation[]>("/invitations"),
      ]);
      setMembers(memberRows);
      setInvitations(invitationRows);
      setNotice(`${memberRows.length} участников · ${invitationRows.length} активных приглашений`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось загрузить команду");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const canInviteAdmin = profile?.role === "owner" || profile?.role === "service";
  const roleOptions = useMemo(
    () => (canInviteAdmin ? ["viewer", "editor", "admin"] : ["viewer", "editor"]),
    [canInviteAdmin],
  );

  async function createInvite() {
    setBusy(true);
    setCreatedLink("");
    try {
      const created = await api<CreatedInvitation>("/invitations", {
        method: "POST",
        body: JSON.stringify({ role, expires_in_hours: hours }),
      });
      const link = `${window.location.origin}/invite?token=${encodeURIComponent(created.token)}`;
      setCreatedLink(link);
      setNotice(`Одноразовое приглашение ${created.role} создано до ${formatDate(created.expires_at)}`);
      await load();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось создать приглашение");
    } finally {
      setBusy(false);
    }
  }

  async function copyInvite() {
    if (!createdLink) return;
    try {
      await navigator.clipboard.writeText(createdLink);
      setNotice("Ссылка приглашения скопирована");
    } catch {
      setNotice("Не удалось скопировать автоматически — скопируйте ссылку из поля");
    }
  }

  async function revokeInvite(invitationId: string) {
    setBusy(true);
    try {
      await fetch(`/api/autoposter/invitations/${encodeURIComponent(invitationId)}`, {
        method: "DELETE",
        cache: "no-store",
      });
      setNotice("Приглашение отозвано");
      await load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>WORKSPACE ACCESS</p>
          <h1>Команда</h1>
          <p>Приглашения одноразовые: сервер хранит только hash токена, а после принятия membership начинает действовать в этой же revocable session.</p>
        </div>
      </section>

      <p className={styles.notice}>{notice}</p>

      <div className={styles.grid}>
        <section className={styles.panel}>
          <header className={styles.panelHeader}>
            <h2>Участники</h2>
            <span>{members.length}</span>
          </header>
          <div className={styles.list}>
            {members.map((member) => (
              <article className={styles.member} key={member.user_id}>
                <div className={styles.avatar}>{initials(member)}</div>
                <div className={styles.memberMeta}>
                  <strong>{member.full_name || member.username || `User #${member.user_id}`}</strong>
                  <span>{member.username ? `@${member.username.replace(/^@/, "")}` : `user #${member.user_id}`}</span>
                </div>
                <span className={`${styles.role} ${styles[member.role] ?? ""}`}>{member.role}</span>
              </article>
            ))}
            {!members.length ? <div className={styles.empty}>Нет доступных участников.</div> : null}
          </div>
        </section>

        <section className={styles.panel}>
          <header className={styles.panelHeader}>
            <h2>Новое приглашение</h2>
            <span>admin+</span>
          </header>
          <div className={styles.form}>
            <label>
              Роль
              <select value={role} onChange={(event) => setRole(event.target.value)}>
                {roleOptions.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
            <label>
              Срок действия
              <select value={hours} onChange={(event) => setHours(Number(event.target.value))}>
                <option value={24}>24 часа</option>
                <option value={72}>3 дня</option>
                <option value={168}>7 дней</option>
                <option value={336}>14 дней</option>
              </select>
            </label>
            <button className={styles.primary} type="button" disabled={busy} onClick={() => void createInvite()}>
              {busy ? "Создаю…" : "Создать одноразовую ссылку"}
            </button>
            {createdLink ? (
              <div className={styles.created}>
                <strong>Показывается только сейчас</strong>
                <input readOnly value={createdLink} onFocus={(event) => event.currentTarget.select()} />
                <button type="button" onClick={() => void copyInvite()}>Скопировать</button>
              </div>
            ) : null}
          </div>
        </section>
      </div>

      <section className={styles.panel}>
        <header className={styles.panelHeader}>
          <h2>Активные приглашения</h2>
          <span>{invitations.length}</span>
        </header>
        <div className={styles.invites}>
          {invitations.map((invitation) => (
            <article key={invitation.id} className={styles.invite}>
              <div>
                <strong>{invitation.role}</strong>
                <span>Действует до {formatDate(invitation.expires_at)}</span>
              </div>
              <button type="button" disabled={busy} onClick={() => void revokeInvite(invitation.id)}>Отозвать</button>
            </article>
          ))}
          {!invitations.length ? <div className={styles.empty}>Нет активных приглашений.</div> : null}
        </div>
      </section>
    </main>
  );
}

function initials(member: WorkspaceMember) {
  const value = member.full_name || member.username || String(member.user_id);
  return value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("");
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
