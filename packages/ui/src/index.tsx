import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";

export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export function buttonClassName({
  variant = "primary",
  size = "md",
  className,
}: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
} = {}): string {
  return cx("ui-button", `ui-button--${variant}`, `ui-button--${size}`, className);
}

export function Button({
  variant = "primary",
  size = "md",
  className,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
}): ReactNode {
  return (
    <button className={buttonClassName({ variant, size, className })} type={type} {...props} />
  );
}

export type BadgeTone = "neutral" | "accent" | "success" | "warning" | "danger";

export function Badge({
  tone = "neutral",
  dot = false,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  tone?: BadgeTone;
  dot?: boolean;
}): ReactNode {
  return (
    <span className={cx("ui-badge", `ui-badge--${tone}`, className)} {...props}>
      {dot ? <span className="ui-badge__dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function Panel({
  elevated = false,
  interactive = false,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  elevated?: boolean;
  interactive?: boolean;
}): ReactNode {
  return (
    <div
      className={cx(
        "ui-panel",
        elevated && "ui-panel--elevated",
        interactive && "ui-panel--interactive",
        className,
      )}
      {...props}
    />
  );
}

export function Eyebrow({ className, ...props }: HTMLAttributes<HTMLParagraphElement>): ReactNode {
  return <p className={cx("ui-eyebrow", className)} {...props} />;
}

export function SectionHeading({
  eyebrow,
  title,
  description,
  action,
  className,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}): ReactNode {
  return (
    <div className={cx("ui-section-heading", className)}>
      <div className="ui-section-heading__copy">
        {eyebrow ? <Eyebrow>{eyebrow}</Eyebrow> : null}
        <h2>{title}</h2>
        {description ? <p>{description}</p> : null}
      </div>
      {action ? <div className="ui-section-heading__action">{action}</div> : null}
    </div>
  );
}

export function ProgressBar({
  value,
  max = 100,
  label,
  tone = "accent",
}: {
  value: number;
  max?: number;
  label: string;
  tone?: "accent" | "success" | "warning" | "danger";
}): ReactNode {
  const safeMax = Math.max(1, max);
  const percent = Math.min(100, Math.max(0, (value / safeMax) * 100));

  return (
    <div className="ui-progress">
      <div className="ui-progress__label">
        <span>{label}</span>
        <span className="ui-mono">
          {value}/{max}
        </span>
      </div>
      <div
        className="ui-progress__track"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value}
      >
        <span
          className={cx("ui-progress__fill", `ui-progress__fill--${tone}`)}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

export function VisuallyHidden({
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement>): ReactNode {
  return <span className={cx("ui-visually-hidden", className)} {...props} />;
}
