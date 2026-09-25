export interface IntentKeyRegistry {
  keyFor(intent: string): string;
  complete(intent: string): void;
  discard(intent: string): void;
}

export function createIntentKeyRegistry(
  generate: () => string = () => crypto.randomUUID(),
): IntentKeyRegistry {
  const keys = new Map<string, string>();
  return {
    keyFor(intent: string): string {
      const existing = keys.get(intent);
      if (existing) return existing;
      const created = generate();
      keys.set(intent, created);
      return created;
    },
    complete(intent: string): void {
      keys.delete(intent);
    },
    discard(intent: string): void {
      keys.delete(intent);
    },
  };
}
