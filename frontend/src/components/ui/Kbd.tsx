"use client";

import React from "react";
import { cn } from "@/lib/utils";

export interface KbdProps extends React.HTMLAttributes<HTMLElement> {
  children: React.ReactNode;
}

export function Kbd({ className, children, ...props }: KbdProps) {
  return (
    <kbd
      className={cn(
        "inline-flex items-center justify-center rounded border border-line border-b-2 bg-surface-card px-1.5 py-0.5 font-mono text-[11px] font-semibold leading-none text-ink-secondary",
        className
      )}
      {...props}
    >
      {children}
    </kbd>
  );
}
