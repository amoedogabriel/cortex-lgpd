"use client";

import React from "react";
import { cn } from "@/lib/utils";

export type ButtonVariant =
  | "primary"
  | "secondary"
  | "ghost"
  | "subtle"
  | "danger"
  | "danger-outline"
  | "dark"
  | "header-ghost"
  | "header-solid";

export type ButtonSize = "xs" | "sm" | "md" | "lg" | "icon";

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "border border-brand-primary bg-brand-primary text-white hover:bg-brand-hover hover:border-brand-hover",
  secondary:
    "border border-line bg-surface-card text-ink-primary hover:bg-surface-subtle hover:border-line-strong",
  ghost:
    "border border-transparent bg-transparent text-ink-secondary hover:bg-surface-subtle hover:text-ink-primary",
  subtle:
    "border border-brand-border bg-brand-subtle text-brand-ink hover:bg-emerald-100",
  danger:
    "border border-red-900 bg-red-900 text-white hover:bg-red-950 hover:border-red-950",
  "danger-outline":
    "border border-red-200 bg-surface-card text-red-800 hover:bg-red-50 hover:border-red-300",
  dark:
    "border border-slate-900 bg-slate-900 text-white hover:bg-slate-800",
  "header-ghost":
    "border border-white/25 bg-white/10 text-white hover:bg-white/20 focus-visible:ring-white focus-visible:ring-offset-brand-header",
  "header-solid":
    "border border-white/30 bg-white text-brand-header hover:bg-brand-subtle focus-visible:ring-white focus-visible:ring-offset-brand-header",
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  xs: "h-7 rounded-md px-2 text-xs font-semibold gap-1",
  sm: "h-8 rounded-lg px-2.5 text-xs font-semibold gap-1.5",
  md: "h-9 rounded-lg px-3.5 text-[13px] font-semibold gap-1.5",
  lg: "h-11 rounded-lg px-4 text-sm font-semibold gap-2",
  icon: "h-7 w-7 rounded-lg p-0",
};

export function buttonVariants(options?: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
}): string {
  const variant = options?.variant ?? "secondary";
  const size = options?.size ?? "md";
  return cn(
    "inline-flex items-center justify-center whitespace-nowrap transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-focus focus-visible:ring-offset-2",
    VARIANT_CLASSES[variant],
    SIZE_CLASSES[size],
    options?.className
  );
}

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "secondary", size = "md", type = "button", ...props }, ref) => {
    return (
      <button
        ref={ref}
        type={type}
        className={buttonVariants({ variant, size, className })}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";
