import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-line bg-panel shadow-sm ${className}`}>{children}</div>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  type = "button",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "ghost" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  const styles = {
    primary: "bg-brand text-white hover:opacity-90",
    ghost: "border border-line bg-panel text-ink hover:bg-brand-soft",
    danger: "border border-line text-bad hover:bg-bad/10",
  }[variant];
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex items-center justify-center rounded-lg px-3 py-2 text-sm font-medium transition disabled:opacity-40 ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-line bg-panel px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-muted">{hint}</div>}
    </div>
  );
}

export function Badge({ status }: { status: "completed" | "planned" | "skipped" | "extra" }) {
  const map = {
    completed: ["Completed", "bg-good/12 text-good"],
    planned: ["Planned", "bg-brand-soft text-brand"],
    skipped: ["Not recorded", "bg-warn/15 text-warn"],
    extra: ["Extra / unplanned", "bg-warn/15 text-warn"],
  }[status];
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${map[1]}`}>{map[0]}</span>;
}

export function AutoTag() {
  return (
    <span
      title="Placed automatically from the import folder, by date and distance. Reassign it in Upload if it belongs elsewhere."
      className="rounded-full border border-line px-2 py-0.5 text-[11px] font-medium text-muted"
    >
      auto
    </span>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="rounded-xl border border-bad/40 bg-bad/10 p-4 text-sm text-bad">
      <p className="font-medium">Could not load the data</p>
      <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-xs">{message}</pre>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-lg border border-bad/40 px-3 py-1.5 text-xs font-medium"
        >
          Retry
        </button>
      )}
    </div>
  );
}
