/**
 * Studio Legacy Bootstrap
 * --------------------------
 * Carga los módulos legacy.js y sql-runner.js y publica en `window` cada
 * función exportada. studio.html (y el HTML que legacy.js inyecta en tiempo
 * de ejecución) llama a esas funciones desde atributos `onclick=`, `oninput=`,
 * etc. — necesitan vivir en el scope global, como antes del refactor a ES
 * Module.
 *
 * Originalmente el script inline de studio.html declaraba 141 funciones top
 * level dentro de un `<script>` no estricto → todas quedaban como propiedades
 * de `window`. Tras la modularización las funciones viven en el scope del
 * módulo. Este bootstrap re-expone exactamente la misma superficie para
 * preservar comportamiento. La eliminación de los handlers inline (y por
 * ende de este shim) se planea para Fase 4C/4D.
 */

import * as legacy from './legacy.js';
import * as sqlRunner from './sql-runner.js';
import { wireStudioHandlers } from './wire-handlers.js';

for (const [name, value] of Object.entries(legacy)) {
  if (typeof value === 'function') window[name] = value;
}
for (const [name, value] of Object.entries(sqlRunner)) {
  if (typeof value === 'function') window[name] = value;
}

// Wire DOM listeners that replaced studio.html's former inline on*="" attrs.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', wireStudioHandlers);
} else {
  wireStudioHandlers();
}
