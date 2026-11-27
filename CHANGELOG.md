# Versiones

## v1.0.1 — 27/11/2026

- La app muestra su número de versión en la cabecera (y en `/salud`), para comprobar qué versión hay instalada.
- Documentación al día: nombres de las listas («Ocultar siempre» / «No ocultar nunca»), versiones fijas en el compose, proceso de publicación y licencias.

## v1.0.0 — 27/11/2026

Primera versión numerada.

- **Detección:** spaCy (español grande, catalán e inglés) + GLiNER multilingüe + reglas españolas con verificación (DNI, NIE, NSS, IBAN, CUPS, referencia catastral) y reglas con contexto (tarjeta sanitaria, póliza, cliente, contrato).
- **Revisión sobre el documento:** pulsar un dato para dejarlo visible y seleccionar texto para ocultarlo; vista de lista sincronizada.
- **Resultado:** texto con marcadores para el LLM, PDF tachado (borrado real, sin metadatos) y verificación automática de restos.
- **Restaurar (opcional):** volver a poner los datos reales en la respuesta del LLM.
- **Privacidad:** todo local y sin Internet en ejecución; datos borrados a las 24 h o con «Terminar y borrar del NAS».
- **Interfaz:** rediseño con modo oscuro y adaptada al móvil.
- **Memoria:** modelos bajo demanda y liberados tras 15 min sin uso.
