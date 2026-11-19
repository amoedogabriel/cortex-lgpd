"use client";

import React from "react";
import { cn } from "@/lib/utils";

export interface SegmentedOption<T extends string | number> {
  value: T;
  label: React.ReactNode;
  title?: string;
  testId?: string;
  activeClassName?: string;
}

export interface SegmentedControlProps<T extends string | number> {
  ariaLabel: string;
  value: T;
  options: SegmentedOption<T>[];
  onChange: (nextValue: T) => void;
  className?: string;
  itemClassName?: string;
}

export function SegmentedControl<T extends string | number>({
  ariaLabel,
  value,
  options,
  onChange,
  className,
  itemClassName,
}: SegmentedControlProps<T>) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn(
        "inline-grid gap-0.5 rounded-lg border border-line-subtle bg-surface-subtle p-0.5",
        className
      )}
      style={{
        gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))`,
      }}
    >
      {options.map((opt) => {
        const isActive = opt.value === value;
        return (
          <button
            key={String(opt.value)}
            type="button"
            aria-pressed={isActive}
            title={opt.title}
            onClick={() => onChange(opt.value)}
            data-testid={opt.testId}
            className={cn(
              "inline-flex h-7 items-center justify-center rounded-md px-2 text-xs font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-focus focus-visible:ring-offset-1",
              isActive
                ? opt.activeClassName || "bg-ink-primary text-white shadow-xs"
                : "bg-transparent text-ink-secondary hover:bg-surface-card hover:text-ink-primary",
              itemClassName
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
