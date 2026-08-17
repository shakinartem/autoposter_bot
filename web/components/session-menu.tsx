"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import styles from "./session-menu.module.css";

type SessionProfile = {
  authenticated: boolean;
  workspace_id?: number;
  user_id?: number | null;
  role?: string;
};

export function SessionMenu() {
  const [profile, setProfile] = useState<SessionProfile | null>(null);

  useEffect(() => {
    let active = true;
    void fetch("/api/session", { cache: "no-store" })
      .then(async (response) => {
        const payload = (await response.json().catch(() => ({}))) as SessionProfile;
        if (active) setProfile(response.ok ? payload : { authenticated: false });
      })
      .catch(() => {
        if (active) setProfile({ authenticated: false });
      });
    return () => {
      active = false;
    };
  }, []);

  async function logout() {
    await fetch("/api/session", { method: "DELETE" }).catch(() => undefined);
    window.location.assign("/login");
  }

  if (!profile) return <span className={styles.loading}>session…</span>;
  if (!profile.authenticated) {
    return <Link className={styles.login} href="/login">Войти</Link>;
  }

  return (
    <div className={styles.menu}>
      <div className={styles.identity}>
        <strong>{profile.role ?? "member"}</strong>
        <span>workspace #{profile.workspace_id ?? "—"}</span>
      </div>
      <button type="button" className={styles.logout} onClick={() => void logout()}>
        Выйти
      </button>
    </div>
  );
}
