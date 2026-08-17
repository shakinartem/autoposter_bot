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

type WorkspaceAccess = {
  id: number;
  name: string;
  owner_user_id: number;
  role: string;
  created_at: string;
};

export function SessionMenu() {
  const [profile, setProfile] = useState<SessionProfile | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceAccess[]>([]);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    let active = true;
    void fetch("/api/session", { cache: "no-store" })
      .then(async (response) => {
        const payload = (await response.json().catch(() => ({}))) as SessionProfile;
        if (!active) return;
        if (!response.ok || !payload.authenticated) {
          setProfile({ authenticated: false });
          return;
        }
        setProfile(payload);
        const workspaceResponse = await fetch("/api/autoposter/auth/workspaces", { cache: "no-store" });
        const workspacePayload = (await workspaceResponse.json().catch(() => [])) as WorkspaceAccess[];
        if (active && workspaceResponse.ok && Array.isArray(workspacePayload)) {
          setWorkspaces(workspacePayload);
        }
      })
      .catch(() => {
        if (active) setProfile({ authenticated: false });
      });
    return () => {
      active = false;
    };
  }, []);

  async function switchWorkspace(workspaceId: number) {
    if (!profile?.workspace_id || workspaceId === profile.workspace_id) return;
    setSwitching(true);
    try {
      const response = await fetch(`/api/autoposter/auth/workspaces/${workspaceId}/switch`, {
        method: "POST",
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(typeof payload?.detail === "string" ? payload.detail : "Workspace switch failed");
      }
      window.location.reload();
    } catch (error) {
      console.error(error);
      setSwitching(false);
    }
  }

  async function logout() {
    await fetch("/api/session", { method: "DELETE" }).catch(() => undefined);
    window.location.assign("/login");
  }

  if (!profile) return <span className={styles.loading}>session…</span>;
  if (!profile.authenticated) {
    return <Link className={styles.login} href="/login">Войти</Link>;
  }

  const current = workspaces.find((workspace) => workspace.id === profile.workspace_id);

  return (
    <div className={styles.menu}>
      {workspaces.length > 1 ? (
        <select
          className={styles.workspaceSelect}
          aria-label="Активный workspace"
          value={profile.workspace_id ?? ""}
          disabled={switching}
          onChange={(event) => void switchWorkspace(Number(event.target.value))}
        >
          {workspaces.map((workspace) => (
            <option key={workspace.id} value={workspace.id}>
              {workspace.name}
            </option>
          ))}
        </select>
      ) : (
        <div className={styles.identity}>
          <strong>{profile.role ?? "member"}</strong>
          <span>{current?.name ?? `workspace #${profile.workspace_id ?? "—"}`}</span>
        </div>
      )}
      <button type="button" className={styles.logout} onClick={() => void logout()}>
        Выйти
      </button>
    </div>
  );
}
