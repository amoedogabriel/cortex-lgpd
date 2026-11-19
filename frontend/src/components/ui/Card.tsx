"use client";

import React from "react";
import { cn } from "@/lib/utils";

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  padded?: boolean;
}

export function Card({
  className,
  padded = false,
  children,
  ...props
}: CardProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-line-subtle bg-surface-card shadow-xs",
        padded && "p-5",
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}
