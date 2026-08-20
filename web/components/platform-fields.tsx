import type { PlatformCapability } from "@/lib/api";

type Props = {
  capability?: PlatformCapability;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
};

export function PlatformFields({ capability, value, onChange }: Props) {
  const entries = Object.entries(capability?.fields ?? {});
  if (!entries.length) return null;

  return (
    <div>
      <div className="sectionLabel">Настройки площадки</div>
      {entries.map(([name, spec]) => {
        const label = typeof spec.label === "string" ? spec.label : humanize(name);
        const type = typeof spec.type === "string" ? spec.type : "text";
        const current = value[name] ?? spec.default ?? (type === "boolean" ? false : "");

        if (type === "boolean") {
          return (
            <label className="fieldLabel" key={name}>
              <input
                type="checkbox"
                checked={Boolean(current)}
                onChange={(event) => onChange({ ...value, [name]: event.target.checked })}
              />{" "}
              {label}
            </label>
          );
        }

        if (type === "select") {
          const options = Array.isArray(spec.options) ? spec.options : [];
          return (
            <label className="fieldLabel" key={name}>
              {label}
              <select
                className="selectInput"
                value={String(current)}
                onChange={(event) => onChange({ ...value, [name]: event.target.value })}
              >
                {options.map((option) => (
                  <option key={String(option)} value={String(option)}>
                    {humanize(String(option))}
                  </option>
                ))}
              </select>
            </label>
          );
        }

        return (
          <label className="fieldLabel" key={name}>
            {label}
            <input
              className="variantTitleInput"
              value={String(current)}
              onChange={(event) => onChange({ ...value, [name]: event.target.value })}
            />
          </label>
        );
      })}
    </div>
  );
}

export function capabilityDefaults(capability?: PlatformCapability) {
  return Object.fromEntries(
    Object.entries(capability?.fields ?? {}).map(([name, spec]) => [
      name,
      spec.default ?? (spec.type === "boolean" ? false : ""),
    ]),
  );
}

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/^./, (match) => match.toUpperCase());
}
