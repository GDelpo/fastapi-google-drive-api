# Drive Service

Microservicio FastAPI para gestión de archivos en Google Drive.


## Características

- Upload de archivos individuales y en batch
- Creación de carpetas con links compartidos
- Búsqueda de archivos con filtros y paginación
- Descarga de contenido de archivos
- Renombrar archivos y carpetas
- Generación de links de descarga/visualización
- Información de quota y estadísticas
- Dashboard web integrado (`/dashboard`)
- Health check con caché (para Traefik)

## Requisitos

- Python 3.14+
- Credenciales de Google Cloud con acceso a Drive API (ver sección Autenticación)

---

## Autenticación con Google Drive

El servicio soporta **dos modos de autenticación** con Google Drive.
Cuando las tres variables OAuth2 están configuradas, se usa OAuth2 automáticamente.

### Modo A — OAuth2 con cuenta personal (recomendado)

Usa las credenciales de una cuenta personal de Google. Los archivos se suben a
la cuenta del usuario y **usan su cuota de almacenamiento** (ej. 2 TB si tiene
plan Google One).

| Variable | Descripción |
|----------|-------------|
| `GOOGLE_OAUTH_CLIENT_ID` | Client ID del proyecto OAuth2 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Client Secret |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | Token de refresco (se obtiene una sola vez) |

**Obtener el refresh token (proceso one-time):**

```bash
# 1. Crear credenciales OAuth2 en Google Cloud Console
#    → APIs & Services → Credentials → Create OAuth Client ID (Desktop app)
#    → Descargar JSON a credentials/oauth_google_credentials.json

# 2. Obtener el refresh token usando google-auth-oauthlib:
#    python -c "
#      from google_auth_oauthlib.flow import InstalledAppFlow
#      flow = InstalledAppFlow.from_client_secrets_file(
#          'credentials/oauth_google_credentials.json',
#          scopes=['https://www.googleapis.com/auth/drive']
#      )
#      creds = flow.run_local_server(port=0)
#      print('Refresh token:', creds.refresh_token)
#    "

# 3. Copiar el refresh token a .env → GOOGLE_OAUTH_REFRESH_TOKEN
```

### Modo B — Service Account (alternativa)

Usa una cuenta de servicio de Google Cloud. Los archivos pertenecen a la cuenta
de servicio, que tiene su **propia cuota de 15 GB** independiente de cualquier
cuenta personal.

| Variable | Descripción |
|----------|-------------|
| `GOOGLE_CREDENTIALS_PATH` | Ruta al JSON de la service account |

```bash
# Colocar el archivo JSON en credentials/google_credentials.json
# En Docker se monta como volumen read-only
```

> **Nota:** La service account tiene 15 GB propios. Si se necesita más espacio,
> usar OAuth2 (Modo A) con una cuenta que tenga almacenamiento extendido.

---

## Configuración

1. Copiar `.env.example` a `.env`
2. Configurar las variables de autenticación (ver sección anterior)
3. Configurar `GOOGLE_ROOT_FOLDER_ID` con el ID de la carpeta raíz en Drive
4. Configurar las URLs del servicio de Identidad

## Desarrollo Local

```bash
# Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Instalar dependencias
pip install -r requirements.txt -r requirements-dev.txt

# Ejecutar
uvicorn app.main:app --reload --port 8000

# Tests
pytest
```

## Docker (Producción con Traefik)

```bash
cd drive

# Build y levantar
docker compose --env-file .env -f docker/docker-compose.prod.traefik.yml up -d --build

# Ver logs
docker compose --env-file .env -f docker/docker-compose.prod.traefik.yml logs -f

# Acceso vía Traefik:
#   API:    http://<server>/drive/api/v1/...
#   Docs:   http://<server>/drive/docs
#   Health: http://<server>/drive/health
#   Dashboard: http://<server>/drive/dashboard
```

## Endpoints

### Archivos

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/v1/files` | Buscar archivos (con filtros y paginación) |
| GET | `/api/v1/files/{id}` | Obtener metadata de archivo |
| GET | `/api/v1/files/{id}/link` | Obtener links de visualización/descarga |
| GET | `/api/v1/files/{id}/download` | Descargar contenido del archivo |
| POST | `/api/v1/files` | Subir archivo (multipart) |
| POST | `/api/v1/files/batch` | Subir múltiples archivos |
| PATCH | `/api/v1/files/{id}` | Renombrar archivo |
| DELETE | `/api/v1/files/{id}` | Eliminar archivo |

### Carpetas

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/api/v1/folders` | Crear carpeta |
| GET | `/api/v1/folders/{id}` | Info de carpeta (con conteo de archivos) |
| GET | `/api/v1/folders/{id}/link` | Link compartido de carpeta |
| POST | `/api/v1/folders/upload` | Crear carpeta y subir archivos |
| PATCH | `/api/v1/folders/{id}` | Renombrar carpeta |
| DELETE | `/api/v1/folders/{id}` | Eliminar carpeta |

### Utilidades

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/v1/quota` | Cuota de almacenamiento |
| GET | `/api/v1/stats` | Estadísticas (archivos/carpetas, caché 60s) |
| GET | `/api/v1/recent` | Archivos recientes (caché 30s) |
| GET | `/health` | Health check (caché 30s) |

## Autenticación de API

Todos los endpoints requieren JWT válido del servicio de Identidad (rol `service` o `admin`).

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/files
```

En modo desarrollo, se puede configurar `SKIP_AUTH=true` para omitir la validación.

## Troubleshooting

### Error de autenticación con Google Drive

**Síntoma**: `google.auth.exceptions.RefreshError` o `invalid_grant`  
**Causa (OAuth2)**: El refresh token venció o fue revocado  
**Fix**: Regenerar el refresh token con el script del README (proceso one-time) y actualizar `GOOGLE_OAUTH_REFRESH_TOKEN` en `.env`

**Causa (Service Account)**: El JSON de credenciales no está en el path o tiene permisos incorrectos  
**Fix**:
```bash
docker exec -it drive_api ls -la /app/credentials/
chmod o+r /opt/services/credentials/google_credentials.json
```

### Cuota de almacenamiento agotada

**Síntoma**: Upload falla con `storageQuotaExceeded`  
**Causa**: La cuota de 15 GB de la service account está llena (o la del usuario en OAuth2)  
**Fix**: Usar Modo A (OAuth2) con una cuenta Google One de 2 TB, o limpiar archivos viejos en Drive.

### Los archivos no aparecen en búsqueda

**Síntoma**: `GET /api/v1/files` no retorna archivos recién subidos  
**Causa**: Google Drive indexa los archivos con un pequeño delay  
**Fix**: Esperar unos segundos y reintentar. No es un bug del servicio.

---

## Estructura del Proyecto

```
drive/
├── app/
│   ├── config.py          # Settings (pydantic-settings)
│   ├── service.py         # Core Drive logic (769 líneas)
│   ├── api.py             # Endpoints FastAPI
│   ├── main.py            # App factory, lifespan, health
│   ├── schemas.py         # Request/Response models (CamelCase)
│   ├── auth.py            # Auth via Identity Service
│   ├── dependencies.py    # FastAPI DI
│   ├── exceptions.py      # Custom exceptions + handlers
│   ├── logger.py          # JSON/Text logging
│   ├── middleware.py       # Request logging + proxy headers
│   └── dashboard/         # Dashboard web integrado
├── credentials/           # Credenciales Google (gitignored)
├── docker/                # Dockerfile + Compose files
├── tests/                 # Tests pytest
├── .env.example           # Template de configuración
└── requirements.txt       # Dependencias
```
