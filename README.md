# Anonimizador local de documentos

Herramienta web que corre en el NAS (Docker) para quitar los datos personales de un documento **antes** de enviarlo a un LLM externo (ChatGPT, Claude, Gemini…) y, después, **volver a poner los datos reales** en la respuesta del LLM.

Todo el procesamiento es local: el documento no sale del NAS y el contenedor no hace ninguna conexión a Internet mientras funciona (los modelos van dentro de la imagen; GitHub la prueba en una red sin Internet antes de publicarla).

```
PDF / foto ──► OCR (si es escaneado) ──► detección ──► TÚ REVISAS ──► texto con [PERSONA_1], [DNI_1]…  ──► LLM externo
                                                                   └► PDF tachado en negro                        │
                                   respuesta con datos reales ◄── restaurar ◄── respuesta del LLM ◄───────────────┘
```

## Qué detecta

| Marcador | Qué es | Cómo |
|---|---|---|
| `PERSONA` | Nombres y apellidos | spaCy + GLiNER + tu lista «Siempre anonimizar» |
| `LUGAR` | Ciudades, países, lugares | Modelo de lenguaje |
| `ORGANIZACION` | Empresas, organismos | Modelo de lenguaje (**desmarcado por defecto**) |
| `DNI`, `NIE` | Documentos de identidad | Patrón + **letra de control verificada** |
| `NSS` | Nº de la Seguridad Social | Patrón + dígitos de control verificados |
| `IBAN`, `TARJETA` | Cuentas y tarjetas | Patrón + dígitos de control verificados |
| `TELEFONO`, `EMAIL`, `IP` | Contacto | Patrones (teléfonos ES, AD, FR, GB, US) |
| `DIRECCION`, `CP` | Domicilios | Patrones (ES, CA, EN); el CP solo con contexto («C.P.», «codi postal») |
| `MATRICULA` | Matrículas españolas | Patrón |
| `CUPS` | Código de suministro de luz y gas (identifica la vivienda) | Patrón + letras de control verificadas (python-stdnum) |
| `CATASTRO` | Referencia catastral (IBI, escrituras) | Patrón + caracteres de control verificados (python-stdnum) |
| `SANITARIO` | Tarjeta sanitaria (CIP), nº de historia clínica | Patrón **solo si hay contexto** cerca («CIP», «NHC», «historia clínica»…) |
| `REFERENCIA` | Nº de póliza, cliente, contrato, expediente | Patrón **solo si hay contexto** cerca («póliza», «cliente», «contrato»…) |
| `FECHA` | Fechas | Patrón (**desmarcado por defecto**: márcalas tú si son fechas de nacimiento) |

Idiomas: **español** (modelo grande, máxima calidad), **catalán** e **inglés** (modelo mediano). El idioma se detecta automáticamente en cada página.

**Doble detector:** además de spaCy y las reglas, un segundo modelo especializado en datos personales, **GLiNER** (`urchade/gliner_multi_pii-v1`, Apache-2.0, multilingüe), repasa cada página. Encuentra nombres y direcciones que se le escapan al primero, sobre todo en mayúsculas o en tablas. En la tabla de revisión, lo que solo ha encontrado él aparece como «GLiNER». Se desactiva con `GLINER: "0"`.

**Varios documentos al mismo chat (opcional):** en *Opciones → Marcadores* puedes elegir «Los mismos que …» un documento anterior, para que la misma persona tenga el mismo marcador en ambos. Por defecto cada documento va solo.

**Memoria:** cada modelo (idioma y GLiNER) se carga solo cuando aparece un documento en ese idioma y se libera tras 15 minutos sin uso (`IDLE_SECONDS`). En reposo el contenedor apenas ocupa RAM. El primer documento tras un rato sin usarlo tarda unos segundos más mientras carga el modelo. Las cifras reales de cada versión aparecen en el resumen de la ejecución en GitHub Actions.

## Componentes (todos de código abierto)

| Pieza | Para qué | Proyecto |
|---|---|---|
| Microsoft Presidio | Motor de detección | github.com/microsoft/presidio |
| spaCy + modelos es/ca/en | Reconocer nombres, lugares, organizaciones | spacy.io |
| OCRmyPDF + Tesseract | OCR de escaneados y fotos | ocrmypdf.readthedocs.io |
| PyMuPDF | Tachado real (borra el texto, no solo lo tapa) | pymupdf.readthedocs.io |
| Lingua | Detección de idioma, local | github.com/pemistahl/lingua-py |
| python-stdnum | Validación de CUPS y referencia catastral | github.com/arthurdejong/python-stdnum |
| GLiNER | Segundo detector de datos personales | github.com/urchade/GLiNER |
| FastAPI | Servidor web | fastapi.tiangolo.com |

El código propio está en `app/` (unas 1.000 líneas con la interfaz, comentadas), por si quieres revisarlo o que alguien lo audite.

---

## Cómo se publica (automático)

Cada vez que cambia el código, y además cada lunes para recoger parches de seguridad, GitHub Actions:

1. ejecuta las pruebas de la lógica propia;
2. construye la imagen con los modelos de idioma dentro;
3. la arranca **sin acceso a Internet** y con el mismo endurecimiento que en el NAS;
4. le pasa un contrato ficticio (español, catalán e inglés) y un escaneado con OCR, y comprueba que detecta, tacha, verifica y restaura;
5. **solo si todo pasa**, la publica en `ghcr.io/z2gpmc0q/anonimizador:latest`.

Puedes ver cada ejecución en la pestaña **Actions** del repositorio.

## Instalación en el NAS (sin SSH)

Tiempo: unos 10 minutos, más la descarga de la imagen (aproximadamente 2–3 GB, una sola vez).

### 1. Comprobar que la imagen está publicada

En GitHub, en tu repositorio `anonimizador`:
1. Pestaña **Actions**: la última ejecución debe tener una marca verde ✔.
2. A la derecha, en **Packages**, aparece `anonimizador`. Entra → **Package settings** → *Change visibility* → **Public**. Así el NAS puede descargarla sin contraseña. La imagen no contiene ningún dato tuyo.

### 2. Crear el proyecto en la app Docker de UGOS

1. Abre **Docker** en UGOS → **Proyecto** → **Crear**, igual que con Immich o Paperless.
2. Nombre: `anonimizador`.
3. Pega el contenido de `docker-compose.yml` y cambia `ANON_PASSWORD` por una contraseña larga (12 caracteres o más, distinta a la del NAS). Guárdala en tu gestor de contraseñas.
4. **Desplegar**. UGOS descarga la imagen y arranca el contenedor. Tras 1–2 minutos, mientras carga los modelos, su estado pasa a *healthy* (en ejecución).

No hay que crear carpetas ni cambiar permisos: los datos van en un volumen de Docker que se crea solo, con los permisos correctos.

### 3. Firewall y acceso

- Tus reglas del firewall de UGOS para `192.168.1.0/24` y `100.64.0.0/10` deben permitir el puerto **8010**. Si permiten «todos los puertos», no hay que hacer nada; si van puerto a puerto, añade el 8010 en ambas.
- **No abras el 8010 en el router.**
- En casa: `http://192.168.1.150:8010`
- Fuera de casa, con Tailscale activo en tu dispositivo: `http://nas-casa:8010` (o la IP `100.x.x.x` del NAS). Funciona si tu contenedor de Tailscale usa `network_mode: host`, que es lo habitual.

El navegador pedirá usuario (`anon`) y la contraseña que pusiste.

### 4. Tus listas (recomendado, desde la web)

Pestaña **Listas**:
- **Siempre anonimizar**: nombres y apellidos de la familia, vuestra dirección, etc. Se anonimizan aunque el detector no los vea (por ejemplo, en mayúsculas o en una tabla):
  ```
  María García López;PERSONA
  García López;PERSONA
  Carrer de Balmes 45;DIRECCION
  ```
- **Nunca anonimizar**: palabras que el detector marca por error (Agencia Tributaria, tu banco…).

Pulsa **Guardar listas**.

---

## Cómo se usa

1. **Subir**: arrastra el PDF o la foto a la zona de subida, o púlsala para elegirlo. Luego pulsa **Buscar datos personales**. Un PDF digital tarda segundos; uno escaneado, aproximadamente 5–20 s por página.
2. **Revisar**: ves el documento entero con los datos encontrados resaltados en colores según su tipo. Este es el paso importante:
   - **pulsa un dato resaltado** para dejarlo visible (queda con borde discontinuo); vuelve a pulsarlo para ocultarlo;
   - **selecciona con el ratón o el dedo cualquier otro texto** y en la barra que aparece abajo elige el tipo y pulsa **Ocultar**. Se oculta en todo el documento;
   - si lo prefieres, en **Como lista** tienes los mismos datos agrupados por tipo, con la frase donde aparece cada uno.
3. **Copiar al LLM**: verás el documento tal como lo recibirá el LLM, con los datos tapados por barras de tinta. Pulsa **Copiar texto** y pégalo en Claude o Gemini. También puedes descargar el PDF tachado.
4. **(Opcional) Volver a poner los nombres**: debajo del resultado, en «Ya tengo la respuesta: volver a poner los nombres», pega la respuesta del LLM y los marcadores se cambian por los datos reales solo en tu pantalla. Esa respuesta restaurada **no** se la reenvíes al LLM.
5. **Terminar y borrar del NAS**: elimina al momento el documento, el resultado y la tabla de marcadores. Si no lo pulsas, se borran solos a las **24 horas** (`RETENTION_DAYS`).

Consejos:
- Envía al LLM **el texto**, no el PDF: es más fiable y el LLM lo procesa mejor.
- El mismo valor siempre recibe el mismo marcador dentro de un documento, así que el LLM puede razonar («[PERSONA_1] le debe dinero a [PERSONA_2]»).
- La interfaz se adapta al móvil y al modo oscuro del sistema.

## Limitaciones (léelas)

- **Ninguna detección automática es perfecta.** Revisa siempre el texto antes de enviarlo. Los fallos típicos son nombres poco comunes, nombres en MAYÚSCULAS dentro de tablas y apodos. Usa la lista «Siempre anonimizar» para los nombres que se repiten.
- **Reidentificación por contexto**: «el alcalde de Vic en 2024» identifica a alguien aunque no aparezca su nombre. La herramienta no detecta eso.
- **Imágenes dentro de PDF digitales** (firmas escaneadas, logotipos con nombre, sellos) no pasan por OCR, así que pueden quedar visibles en el PDF tachado. El texto de salida no las incluye.
- **Escaneados de mala calidad o texto manuscrito**: si el OCR lee mal un nombre, no se detecta. Revisa con más cuidado.
- La conexión es `http` sin cifrar dentro de tu LAN. Por Tailscale va cifrada. No lo expongas nunca a Internet.

## Privacidad: qué se guarda y dónde

| Qué | Dónde | Cuánto tiempo |
|---|---|---|
| Documento original subido | No se guarda; se borra tras el análisis | — |
| PDF de trabajo (con datos) y tabla de correspondencias | Volumen Docker `anonimizador_anon_datos` (SSD) | 24 h, o al pulsar «Terminar y borrar» |
| Texto y PDF anonimizados | Mismo volumen | 24 h |
| Tus listas | Mismo volumen | Hasta que las cambies |
| Registros (logs) | Docker; solo peticiones, sin contenido | Rotación de 10 MB |

El contenedor funciona sin root, con el sistema de archivos en solo lectura, sin capacidades de Linux, con límites de memoria y CPU y sin llamadas a Internet: no hay telemetría ni APIs externas, y GitHub lo prueba en una red sin salida.

Lo único que hay en GitHub es el código y la imagen, públicos y sin datos tuyos. Tus documentos, tus listas y tu contraseña solo existen en el NAS.

## Mantenimiento

- **Versiones**: el compose usa una versión fija (`…/anonimizador:v1.0.0`). Cada versión nueva aparece en la sección **Releases** del repositorio, con sus cambios (también en `CHANGELOG.md`).
- **Actualizar**: en la app Docker de UGOS, edita el proyecto `anonimizador`, cambia el número de versión en la línea `image:` y vuelve a desplegarlo. Al cambiar el número, UGOS descarga la imagen nueva sí o sí. Los datos y las listas se conservan.
- **Volver atrás**: si una versión da problemas, pon el número anterior y despliega de nuevo.
- **Sin Internet**: la app no hace conexiones salientes (GitHub lo prueba en una red sin salida). No uses `networks: internal: true` en el compose: esa red impide publicar el puerto 8010 y dejarías de poder abrir la web.
- **Estado**: en la app Docker, el contenedor debe aparecer en ejecución (*healthy*).
- **Copia de seguridad**: los trabajos son temporales y no necesitan copia. Si tus listas son largas, copia su contenido de vez en cuando a un archivo de tu carpeta de usuario.

## Solución de problemas

| Síntoma | Causa / solución |
|---|---|
| No descarga la imagen («denied» o «not found») | El paquete en GitHub no es público (paso 1). |
| El contenedor se reinicia sin parar | Mira su registro en la app Docker. Si dice «Define ANON_USER y ANON_PASSWORD», la contraseña tiene menos de 12 caracteres. |
| No carga la web | Espera 1–2 minutos tras arrancar (carga de modelos). Revisa el firewall de UGOS para el puerto 8010. |
| «Fallo de OCR» | PDF dañado o con restricciones. Prueba a imprimirlo a PDF de nuevo. |
| «El PDF está protegido con contraseña» | Quita la contraseña antes de subirlo. |
| Se queda sin memoria | Sube `mem_limit` en el proyecto (tienes 24 GB de RAM). |
| El análisis tarda más que antes | GLiNER añade unos segundos por página. Si te sobra precisión y prefieres velocidad, pon `GLINER: "0"`. |
| El primer análisis tarda más | Normal: está cargando los modelos. Si te molesta, sube `IDLE_SECONDS` (por ejemplo `3600`) o ponlo a `0` para no liberarlo nunca. |

## Pruebas

- `tests/test_basico.py`: lógica propia (DNI/NIE/NSS, direcciones, seudonimización y restauración). No necesita Docker.
- `tests/e2e.py`: prueba completa contra el contenedor; la ejecuta GitHub Actions en cada publicación.
- `tests/generar_fixtures.py`: genera los documentos ficticios de prueba.

## Licencias

Uso personal. Componentes: Presidio (MIT), spaCy (MIT; algunos modelos de idioma son GPL), PyMuPDF (AGPL), OCRmyPDF (MPL-2.0), Tesseract (Apache-2.0), Lingua (Apache-2.0) y FastAPI (MIT). Las licencias copyleft (GPL/AGPL) solo importan si redistribuyes el software, no para uso doméstico.
