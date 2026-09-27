"use client";

import { useState } from "react";

import { GLOSSARY } from "@/lib/glossary";
import {
  CUSTOM_FREQUENCY_LABEL,
  customScheduleError,
  defaultTimeZone,
  describeSchedule,
  FREQUENCY_PRESETS,
  normalizeCron,
  presetById,
  presetFromCron,
  timeZoneName,
  timeZoneOptions,
  type FrequencyChoice,
} from "@/lib/schedule/frequency";
import { cn } from "@/lib/utils";

export interface FrequencyValue {
  cron: string;
  timeZone: string;
}

const CONTROL_CLASS = cn(
  "min-h-[44px] w-full rounded-md border bg-background px-3 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60",
);

function OptionCard({
  name,
  value,
  checked,
  title,
  hint,
  disabled,
  onSelect,
}: {
  name: string;
  value: FrequencyChoice;
  checked: boolean;
  title: string;
  hint: string;
  disabled?: boolean;
  onSelect: (value: FrequencyChoice) => void;
}) {
  return (
    <label
      data-frequency-option={value}
      className={cn(
        "flex min-h-[64px] cursor-pointer gap-3 rounded-md border bg-background p-3 text-sm",
        checked ? "border-primary bg-primary/5" : "hover:bg-accent/5",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={() => onSelect(value)}
        className="mt-1 h-4 w-4"
      />
      <span>
        <span className="block font-medium">{title}</span>
        <span className="block text-xs text-muted-foreground">{hint}</span>
      </span>
    </label>
  );
}

export function FrequencyPicker({
  value,
  onChange,
  name = "frequency",
  legend = GLOSSARY.schedule.one,
  disabled,
  className,
}: {
  value: FrequencyValue;
  onChange: (value: FrequencyValue) => void;
  name?: string;
  legend?: string;
  disabled?: boolean;
  className?: string;
}) {
  const derived = presetFromCron(value.cron);
  const [customMode, setCustomMode] = useState(derived === "custom");
  const [preservedCustom] = useState(derived === "custom" ? normalizeCron(value.cron) : "");
  const choice: FrequencyChoice = derived === "custom" || customMode ? "custom" : derived;
  const browserZone = defaultTimeZone();
  const zones = timeZoneOptions(browserZone, value.timeZone);
  const customError = choice === "custom" ? customScheduleError(value.cron) : null;

  function select(next: FrequencyChoice) {
    if (next === "custom") {
      setCustomMode(true);
      onChange({ ...value, cron: preservedCustom || normalizeCron(value.cron) });
      return;
    }
    setCustomMode(false);
    onChange({ ...value, cron: presetById(next).cron ?? "" });
  }

  return (
    <fieldset className={cn("space-y-3", className)} data-frequency-picker={name}>
      <legend className="text-sm font-medium">{legend}</legend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {FREQUENCY_PRESETS.map((preset) => (
          <OptionCard
            key={preset.id}
            name={name}
            value={preset.id}
            checked={choice === preset.id}
            title={preset.label}
            hint={preset.hint}
            disabled={disabled}
            onSelect={select}
          />
        ))}
        <OptionCard
          name={name}
          value="custom"
          checked={choice === "custom"}
          title={CUSTOM_FREQUENCY_LABEL}
          hint={preservedCustom ? "Conserva la programación registrada." : "Para calendarios que no aparecen en la lista."}
          disabled={disabled}
          onSelect={select}
        />
      </div>
      {choice === "custom" ? (
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium">Expresión de programación (minuto hora día mes día-de-semana)</span>
          <input
            name={`${name}_custom`}
            value={value.cron}
            disabled={disabled}
            onChange={(event) => onChange({ ...value, cron: event.target.value })}
            placeholder="30 7 * * 1-5"
            className={cn(CONTROL_CLASS, "font-mono")}
            aria-invalid={customError ? true : undefined}
          />
          {customError ? <span className="text-destructive">{customError}</span> : null}
        </label>
      ) : null}
      {choice !== "manual" ? (
        <label className="flex max-w-sm flex-col gap-1 text-xs">
          <span className="font-medium">Zona horaria</span>
          <select
            name={`${name}_timezone`}
            value={value.timeZone}
            disabled={disabled}
            onChange={(event) => onChange({ ...value, timeZone: event.target.value })}
            className={CONTROL_CLASS}
          >
            {zones.map((zone) => (
              <option key={zone} value={zone}>
                {zone === browserZone ? `${timeZoneName(zone)} (este navegador)` : timeZoneName(zone)}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <p className="text-xs text-muted-foreground" aria-live="polite" data-frequency-summary>
        {describeSchedule(value.cron, value.timeZone)}
      </p>
    </fieldset>
  );
}
