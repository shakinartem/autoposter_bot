"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type AccountConnectionSpec,
  type SocialAccount,
  type Workspace,
  createAccount,
  getWorkspace,
  listAccountConnectionSpecs,
  listAccounts,
} from "@/lib/api";
import styles from "./accounts.module.css";

export default function AccountsPage() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [specs, setSpecs] = useState<Record<string, AccountConnectionSpec>>({});
  const [platform, setPlatform] = useState("");
  const [name, setName] = useState("");
  const [destination, setDestination] = useState("");
  const [options, setOptions] = useState<Record<string, unknown>>({});
  const [notice, setNotice] = useState("Загружаю подключения…");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [workspaceData, accountData, specData] = await Promise.all([
        getWorkspace(),
        listAccounts(),
        listAccountConnectionSpecs(),
      ]);
      setWorkspace(workspaceData);
      setAccounts(accountData);
      setSpecs(specData);
      const first = Object.keys(specData)[0] ?? "";
      setPlatform((current) => current || first);
      setNotice("Подключения синхронизированы");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось загрузить подключения");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const spec = specs[platform];

  useEffect(() => {
    if (!spec) return;
    setName("");
    setDestination("");
    setOptions(
      Object.fromEntries(
        Object.entries(spec.fields).map(([key, field]) => [key, field.default ?? ""]),
      ),
    );
  }, [platform, spec]);

  const grouped = useMemo(() => {
    const result: Record<string, SocialAccount[]> = {};
    for (const account of accounts) {
      (result[account.platform] ??= []).push(account);
    }
    return result;
  }, [accounts]);

  async function submit() {
    if (!spec) return;
    setBusy(true);
    try {
      const account = await createAccount({
        name,
        platform,
        destination: destination || null,
        options,
      });
      setAccounts((items) => [...items, account]);
      setName("");
      setDestination("");
      setOptions(
        Object.fromEntries(
          Object.entries(spec.fields).map(([key, field]) => [key, field.default ?? ""]),
        ),
      );
      setNotice(`${spec.title}: аккаунт подключён и credentials сохранены`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось подключить аккаунт");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>{workspace?.name ?? "Workspace"}</p>
          <h1>Social connections</h1>
          <p>Подключайте площадки один раз. Секреты шифруются на backend и никогда не возвращаются в браузер после сохранения.</p>
        </div>
      </section>

      <p className={styles.notice}>{notice}</p>

      <div className={styles.grid}>
        <section className={styles.panel}>
          <header className={styles.panelHeader}>
            <h2>Подключённые аккаунты</h2>
            <p>{accounts.length} подключений в текущем workspace</p>
          </header>
          {accounts.length ? (
            <div className={styles.list}>
              {Object.entries(grouped).flatMap(([key, items]) =>
                items.map((account) => (
                  <article className={styles.account} key={account.id}>
                    <div className={styles.icon}>{key.slice(0, 2)}</div>
                    <div>
                      <strong>{account.name}</strong>
                      <small>{account.destination || `Account #${account.id}`}</small>
                      {account.public_options && Object.keys(account.public_options).length ? (
                        <div className={styles.public}>{safeOptions(account.public_options)}</div>
                      ) : null}
                    </div>
                    <span className={styles.badge}>Connected</span>
                  </article>
                )),
              )}
            </div>
          ) : (
            <div className={styles.empty}>Пока нет подключённых площадок.</div>
          )}
        </section>

        <section className={styles.panel}>
          <header className={styles.panelHeader}>
            <h2>Новое подключение</h2>
            <p>Connection spec приходит с backend и определяет поля формы.</p>
          </header>
          <div className={styles.form}>
            <div className={styles.platformTabs}>
              {Object.values(specs).map((item) => (
                <button
                  type="button"
                  className={platform === item.platform ? styles.active : ""}
                  key={item.platform}
                  onClick={() => setPlatform(item.platform)}
                >
                  {item.title}
                </button>
              ))}
            </div>

            {spec ? (
              <>
                {spec.notes ? <div className={styles.note}>{spec.notes}</div> : null}
                <label className={styles.field}>
                  Название подключения
                  <input
                    className={styles.input}
                    value={name}
                    placeholder={`${spec.title} — основной`}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>
                <label className={styles.field}>
                  {spec.destination.label}
                  <input
                    className={styles.input}
                    value={destination}
                    placeholder={spec.destination.placeholder}
                    onChange={(event) => setDestination(event.target.value)}
                  />
                </label>

                {Object.entries(spec.fields).map(([fieldName, field]) => (
                  <ConnectionField
                    key={fieldName}
                    name={fieldName}
                    field={field}
                    value={options[fieldName]}
                    onChange={(value) => setOptions((current) => ({ ...current, [fieldName]: value }))}
                  />
                ))}

                <button
                  className={styles.submit}
                  type="button"
                  disabled={busy || !name.trim()}
                  onClick={() => void submit()}
                >
                  {busy ? "Сохраняю…" : `Подключить ${spec.title}`}
                </button>
                <div className={styles.security}>
                  <strong>Security boundary.</strong> Secret-поля уходят через server-side BFF, шифруются перед записью и не входят в AccountView.
                </div>
              </>
            ) : (
              <div className={styles.empty}>Нет доступных connection specs.</div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

function ConnectionField({
  name,
  field,
  value,
  onChange,
}: {
  name: string;
  field: AccountConnectionSpec["fields"][string];
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (field.type === "select") {
    return (
      <label className={styles.field}>
        {field.label}
        <select className={styles.select} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
          {(field.options ?? []).map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      </label>
    );
  }

  if (field.type === "secret") {
    return (
      <label className={styles.field}>
        {field.label}
        <div className={styles.secretWrap}>
          <input
            className={styles.input}
            type="password"
            autoComplete="off"
            value={String(value ?? "")}
            placeholder={field.placeholder}
            onChange={(event) => onChange(event.target.value)}
          />
          <span className={styles.secretMark}>ENC</span>
        </div>
      </label>
    );
  }

  return (
    <label className={styles.field}>
      {field.label}
      <input
        className={styles.input}
        name={name}
        value={String(value ?? "")}
        placeholder={field.placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function safeOptions(options: Record<string, unknown>) {
  return Object.entries(options)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" · ");
}
