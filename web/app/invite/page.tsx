import Link from "next/link";
import { InviteActions } from "./invite-actions";
import styles from "./invite.module.css";

const BACKEND_URL = (process.env.AUTOPOSTER_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

type InvitePageProps = {
  searchParams: Promise<{ token?: string | string[] }>;
};

type InvitePreview = {
  workspace_id: number;
  workspace_name: string;
  role: string;
  expires_at: string;
};

export default async function InvitePage({ searchParams }: InvitePageProps) {
  const params = await searchParams;
  const token = Array.isArray(params.token) ? params.token[0] ?? "" : params.token ?? "";
  const preview = await loadPreview(token);

  if (!preview) {
    return (
      <main className={styles.page}>
        <section className={styles.card}>
          <div className={styles.mark}>A</div>
          <p className={styles.eyebrow}>WORKSPACE INVITATION</p>
          <h1>Приглашение недоступно</h1>
          <p className={styles.copy}>Ссылка уже использована, отозвана, истекла или была повреждена.</p>
          <Link className={styles.secondary} href="/login">Войти в Autoposter</Link>
        </section>
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <section className={styles.card}>
        <div className={styles.mark}>A</div>
        <p className={styles.eyebrow}>WORKSPACE INVITATION</p>
        <h1>{preview.workspace_name}</h1>
        <p className={styles.copy}>Вас пригласили в рабочее пространство Autoposter.</p>
        <div className={styles.details}>
          <div><span>Роль</span><strong>{preview.role}</strong></div>
          <div><span>Действует до</span><strong>{formatDate(preview.expires_at)}</strong></div>
        </div>
        <InviteActions token={token} />
      </section>
    </main>
  );
}

async function loadPreview(token: string): Promise<InvitePreview | null> {
  if (!token.startsWith("awi_") || token.length < 30) return null;
  try {
    const response = await fetch(
      `${BACKEND_URL}/auth/invitations/preview?token=${encodeURIComponent(token)}`,
      { cache: "no-store" },
    );
    if (!response.ok) return null;
    return (await response.json()) as InvitePreview;
  } catch {
    return null;
  }
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
