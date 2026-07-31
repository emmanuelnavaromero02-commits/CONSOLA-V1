/**
 * Registro en memoria de claves de idempotencia por intención lógica.
 *
 * Una intención lógica = (operación, id de acción). La clave se crea
 * perezosamente en el primer intento, se REUTILIZA en reintentos tras
 * timeout o error ambiguo, y se rota únicamente tras una respuesta
 * definitiva exitosa (`complete`) o al descartar la intención de forma
 * explícita (`discard`).
 *
 * Las claves son UUID aleatorios: no derivan de datos del usuario ni de
 * la acción. Nunca se persisten en localStorage/sessionStorage/URL/HTML.
 *
 * Limitación documentada: el registro vive en la memoria del componente
 * montado. El contrato actual no expone un identificador de intención
 * server-authoritative, así que NO se garantiza idempotencia entre
 * pestañas ni tras recargar la página; cada montaje crea claves nuevas,
 * lo que además impide reutilizar por accidente una intención ya
 * completada en un montaje anterior.
 */
export interface IntentKeyRegistry {
  /** Devuelve la clave vigente de la intención, creándola si no existe. */
  keyFor(intent: string): string;
  /** Éxito definitivo: la próxima intención igual usará una clave nueva. */
  complete(intent: string): void;
  /** Cancelación explícita de la intención en curso. */
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
