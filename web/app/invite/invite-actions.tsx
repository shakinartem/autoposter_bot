"use client";

import { useEffect, useState } from "react";
import styles from "./invite.module.css";

type InviteActionsProps = {
  token: string;
};

export function InviteActions({ token }: InviteActionsProps) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void fetch("/api/session", { cache: "no-store" })
      .then((response) => {
        if (active) setAuthenticated(response.ok);
      })
      .catch(() => {
        if (active) setAuthenticated(false);
      });
    return () => {
      active = false;
    };
  }, []);

  function login() {
    const returnPath = `/invite?token=${encodeURIComponent(token)}`;
    window.location.assign(`/auth/telegram?return_path=${encodeURIComponent(returnPath)}`);
  }

  async function accept() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/autoposter/invitations/accept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
        cache: "no-store",
      });
      if (response.status === 401 || response.status === 409) {
        const payload = await response.json().catch(() => ({}));
        if (response.status === 401 || payload?.detail === "User session authentication required") {
          login();
          return;
        }
      }
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof payload?.detail === "string" ? payload.detail : "Не удалось принять приглашение");
      }
      window.history.replaceState({}, "", "/");
      window.location.assign("/");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось принять приглашение");
      setBusy(false);
    }
  }

  if (authenticated === null) {
    return <button className={styles.primary} disabled>Проверяю сессию…</button>;
  }

  return (
    <div className={styles.actions}>
      {authenticated ? (
        <button className={styles.primary} disabled={busy} onClick={() => void accept()}>
          {busy ? "Добавляю в workspace…" : "Принять приглашение"}
        </button>
      ) : (
        <button className={styles.telegram} onClick={login}>
          Войти через Telegram и принять
        </button>
      )}
      {error ? <div className={styles.error}>{error}</div> : null}
      <p className={styles.security}>Приглашение одноразовое. После принятия эта ссылка перестанет работать.</p>
    </div>
  );
}
