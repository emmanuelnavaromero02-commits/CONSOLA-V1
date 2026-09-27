import type {
  ExperienceFactV2,
  ExperienceMetric,
  ExperienceSectionV2,
} from "@/lib/control-room/experience-contract";
import { formatMetric, formatObservedAt } from "@/lib/control-room/experience-presenter";

export interface SectionHighlight {
  key: string;
  headline: string;
  subtitle: string | null;
  count: number;
}

const MAX_NAMED_ENTITIES = 2;

function groupKey(fact: ExperienceFactV2): string {
  return `${fact.kind}\u0000${fact.title.trim().toLocaleLowerCase("es-MX")}`;
}

export function joinEntityLabels(labels: readonly string[]): string | null {
  const distinct = [...new Set(labels.map((label) => label.trim()).filter(Boolean))];
  if (distinct.length === 0) return null;
  if (distinct.length === 1) return distinct[0];
  if (distinct.length === 2) return `${distinct[0]} y ${distinct[1]}`;
  if (distinct.length === 3) return `${distinct[0]}, ${distinct[1]} y ${distinct[2]}`;
  const named = distinct.slice(0, MAX_NAMED_ENTITIES).join(", ");
  return `${named} y ${distinct.length - MAX_NAMED_ENTITIES} más`;
}

function metricSummary(facts: readonly ExperienceFactV2[]): string | null {
  const metrics = facts.map((fact) => fact.metric);
  if (metrics.some((metric) => metric === undefined)) return null;
  const present = metrics as ExperienceMetric[];
  const [first] = present;
  if (
    !first ||
    present.some(
      (metric) =>
        metric.name !== first.name ||
        metric.kind !== first.kind ||
        metric.unit !== first.unit,
    )
  ) {
    return null;
  }
  const ordered = [...present].sort((left, right) => left.value - right.value);
  const low = ordered[0];
  const high = ordered[ordered.length - 1];
  if (low.value === high.value) return `${first.name}: ${formatMetric(low)}`;
  return `${first.name}: de ${formatMetric(low)} a ${formatMetric(high)}`;
}

function latestObservation(facts: readonly ExperienceFactV2[]): string | null {
  let latest: string | null = null;
  let latestMs = Number.NEGATIVE_INFINITY;
  for (const fact of facts) {
    const ms = Date.parse(fact.observed_at);
    if (!Number.isNaN(ms) && ms > latestMs) {
      latestMs = ms;
      latest = fact.observed_at;
    }
  }
  return latest;
}

export function buildSectionHighlights(section: ExperienceSectionV2): SectionHighlight[] {
  const groups = new Map<string, ExperienceFactV2[]>();
  for (const fact of section.facts) {
    const key = groupKey(fact);
    const group = groups.get(key);
    if (group) group.push(fact);
    else groups.set(key, [fact]);
  }
  return [...groups.entries()].map(([key, facts]) => {
    const title = facts[0].title;
    const parts = [
      joinEntityLabels(
        facts.flatMap((fact) => (fact.entity_label ? [fact.entity_label] : [])),
      ),
      metricSummary(facts),
    ];
    const observed = latestObservation(facts);
    if (observed) parts.push(`Dato del ${formatObservedAt(observed)}`);
    const subtitle = parts.filter((part): part is string => Boolean(part)).join(" · ");
    return {
      key,
      headline: facts.length === 1 ? title : `${facts.length} casos detectados: ${title}`,
      subtitle: subtitle || null,
      count: facts.length,
    };
  });
}
