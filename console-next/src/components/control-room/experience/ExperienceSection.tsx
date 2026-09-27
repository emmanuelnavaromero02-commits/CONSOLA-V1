import type { ExperienceSectionV2 } from "@/lib/control-room/experience-contract";
import { buildSectionHighlights } from "@/lib/control-room/experience-headlines";
import type { OpenExperiencePreview } from "@/lib/control-room/use-control-room-experience-preview";

import { ExperienceFact } from "./ExperienceFact";

export function ExperienceSection({
  section,
  onPreviewAction,
}: {
  section: ExperienceSectionV2;
  onPreviewAction: OpenExperiencePreview;
}) {
  if (section.facts.length === 0) return null;
  const highlights = section.facts.length > 1 ? buildSectionHighlights(section) : [];

  return (
    <section aria-label={section.title} className="border-t py-7 first:border-t-0 first:pt-0">
      <h2 className="mb-4 break-words text-lg font-semibold text-foreground">
        {section.title}
      </h2>
      {highlights.length > 0 ? (
        <ul
          aria-label={`Resumen de ${section.title}`}
          className="mb-5 space-y-2 border-l-2 border-primary/30 pl-4"
        >
          {highlights.map((highlight) => (
            <li key={highlight.key} className="min-w-0">
              <p className="break-words text-sm font-medium text-foreground">
                {highlight.headline}
              </p>
              {highlight.subtitle ? (
                <p className="break-words text-xs text-muted-foreground">
                  {highlight.subtitle}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {section.facts.map((fact, index) => (
          <ExperienceFact
            key={index}
            fact={fact}
            sectionTitle={section.title}
            onPreviewAction={onPreviewAction}
          />
        ))}
      </div>
    </section>
  );
}
