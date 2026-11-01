import type { ReactNode } from "react";
import { cn, PANEL, EYEBROW } from "./ui";

interface PanelProps {
  /** Eyebrow label rendered at the top of the card. */
  label?: string;
  /** Right-aligned slot in the header row (e.g. a "clear" ghost button). */
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}

/** Sidebar card with p-3 padding; compose PANEL directly for other padding. */
export function Panel({ label, action, className, children }: PanelProps) {
  return (
    <div className={cn(PANEL, "p-3", className)}>
      {(label || action) && (
        <div className="mb-2 flex items-center justify-between gap-2">
          {label ? <span className={EYEBROW}>{label}</span> : <span />}
          {action}
        </div>
      )}
      {children}
    </div>
  );
}
