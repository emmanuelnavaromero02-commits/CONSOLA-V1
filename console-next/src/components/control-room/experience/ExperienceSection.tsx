import type { ExperienceSection as ExperienceSectionModel } from "@/lib/control-room/experience-contract";

import { ExperienceFact } from "./ExperienceFact";

export function ExperienceSection({ section }: { section: ExperienceSectionModel }) {
  if (section.facts.length === 0) return null;

  return (
    <section aria-label={section.title} className="border-t py-7 first:border-t-0 first:pt-0">
      <h2 className="mb-4 break-words text-lg font-semibold text-foreground">
        {section.title}
      </h2>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {section.facts.map((fact, index) => (
          <ExperienceFact key={index} fact={fact} />
        ))}
      </div>
    </section>
  );
}
