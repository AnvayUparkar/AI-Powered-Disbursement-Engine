# Standalone Intelligent Document Processing (IDP) Microservice Architecture

## Table of Contents
1. [Executive Overview](#1-executive-overview)
2. [Microservice Decoupling & Boundaries](#2-microservice-decoupling--boundaries)
3. [System Architecture & Data Flow](#3-system-architecture--data-flow)
4. [API Specification & Endpoints](#4-api-specification--endpoints)
5. [Authentication, Multi-Tenancy & Security Norms](#5-authentication-multi-tenancy--security-norms)
6. [Core OCR & Parsing Engine Pipeline](#6-core-ocr--parsing-engine-pipeline)
7. [Storage Layer (S3 vs. Local Mock S3)](#7-storage-layer-s3-vs-local-mock-s3)
8. [Standalone Deployment Options](#8-standalone-deployment-options)
   - [A. Standalone Local Execution](#a-standalone-local-execution)
   - [B. Standalone Docker Container](#b-standalone-docker-container)
   - [C. Enterprise Kubernetes (Airgapped/Production)](#c-enterprise-kubernetes-airgappedproduction)
   - [D. Secure Reverse Proxy (Nginx / HTTPS 443)](#d-secure-reverse-proxy-nginx--https-443)
9. [Configuration Reference (`.env`)](#9-configuration-reference-env)
10. [Client Integration Examples (cURL, Python, Node.js)](#10-client-integration-examples-curl-python-nodejs)

---

## 1. Executive Overview

The **IDP Engine** (`idp/` package) is an autonomous, stateless, high-throughput microservice responsible for ingesting complex multi-page financial documents (PDFs, TIFFs, PNGs, JPEGs) and returning structured, machine-readable representations containing:
- Hierarchical document layouts (headings, paragraphs, footnotes)
- Structured tabular data (via IBM Docling & TableFormer)
- High-accuracy OCR text tokens with bounding boxes and per-token confidence scores
- Script-aware and scanned-page text extraction (via Docling/RapidOCR or LightOnOCR)
- Deterministic key-value field extraction (Loan Sanction Letters, KFS, KYC Aadhaar/PAN, Application Forms)

While the parent repository includes an orchestration layer (Celery, LangGraph pipeline, review database, React UI), the IDP engine is designed to be **completely decoupled and operated as a standalone microservice API**.

---

## 2. Microservice Decoupling & Boundaries

To run IDP as a standalone service independent of the main Loan Disbursement backend:

| Capability | Core Monolith / Orchestration | Standalone IDP Microservice |
| :--- | :--- | :--- |
| **Package Root** | `app/`, `pipeline/`, `frontend/` | `idp/`, `config/` |
| **Service Entrypoint**| `app.main:app` (Port 8000) | `idp.main:app` (Port 8001 / Internal) |
| **Primary Duty** | Business logic, rule engine, UI, SQLite auth | Deep document parsing, OCR, table detection |
| **Execution Model** | Async API + Celery background workers | Asynchronous HTTP REST API (`/process`, `/upload`) |
| **Statefulness** | Stateful (databases, cases, reviews) | Stateless (processes bytes/S3 keys, writes results) |
| **Scaling Metric** | Web traffic / User requests | Document volume, page count, GPU/CPU capacity |

---

## 3. System Architecture & Data Flow

```
                   CLIENT / EXTERNAL SYSTEM / UPSTREAM
                                   │
                                   ▼ HTTPS (Port 443)
              ┌──────────────────────────────────────────────┐
              │     Enterprise Reverse Proxy / API Gateway   │
              │         (Nginx / Kong / AWS ALB)             │
              │  - TLS Termination (Strict HTTPS)           │
              │  - Header & Tenant Validation               │
              │  - Rate Limiting & File Size Gating (60MB)   │
              └──────────────────────┬───────────────────────┘
                                     │
                        Private Subnet / Virtual Network
                                     │
                                     ▼ HTTP (Port 8001)
         ┌─────────────────────────────────────────────────────────┐
         │             IDP Engine Microservice (`idp.main`)        │
         ├─────────────────────────────────────────────────────────┤
         │                                                         │
         │  1. Ingestion Layer                                     │
         │     ├── POST /api/v1/documents/process (S3 reference)   │
         │     ├── POST /api/v1/documents/upload  (Multipart)      │
         │     └── GET  /health                                    │
         │                                                         │
         │  2. In-Flight Singleflight Deduplication & Locking      │
         │     └── Deduplicates concurrent requests for same doc   │
         │                                                         │
         │  3. Preprocessing & Document Classification             │
         │     ├── PDF type detection (Scanned vs. Digital/Vector) │
         │     ├── Contrast enhancement & deskewing               │
         │     └── Multi-page image rasterisation                  │
         │                                                         │
         │  4. Hybrid OCR & Layout Engine Routing                  │
         │     ├── Digital PDFs ──► Docling + TableFormer         │
         │     └── Scanned PDFs ──► LightOnOCR (or RapidOCR)      │
         │                                                         │
         │  5. Structural Normalization & Location Resolver        │
         │     ├── Bounding box alignment [x1, y1, x2, y2]         │
         │     ├── Token confidence calculation                    │
         │     └── Key-value field bounding box resolver           │
         │                                                         │
         │  6. Output Generation & Persistence                     │
         │     ├── Write parsed JSON to S3 / Local Mock Storage    │
         │     └── Return structured DocumentStatusResponse        │
         └─────────────────────────────────────────────────────────┘
```

---

## 4. API Specification & Endpoints

Base URL: `http://<host>:8001` (or through reverse proxy `https://<domain>/idp`)

### 4.1 Health Check (Unauthenticated)
- **Path**: `GET /health`
- **Purpose**: Liveness & readiness probes for Kubernetes, Docker, and load balancers.
- **Response**:
```json
{
  "status": "healthy",
  "app_name": "Node 2 - IDP Engine",
  "version": "1.0.0",
  "docling_available": true,
  "rapidocr_available": true,
  "vlm_enabled": false,
  "lightonocr_enabled": true
}
```

### 4.2 Process Existing Document (S3 Reference)
- **Path**: `POST /api/v1/documents/process`
- **Headers**:
  - `Content-Type: application/json`
  - `x-internal-token: <INTERNAL_API_TOKEN>`
  - `x-tenant-id: <TENANT_ID>`
- **Request Body**:
```json
{
  "document_id": "DOC-98234-XYZ",
  "s3_key": "raw-documents/sanction_letter_102.pdf",
  "s3_bucket": "disbursement-documents"
}
```

### 4.3 Upload & Process Document (Direct Multipart)
- **Path**: `POST /api/v1/documents/upload`
- **Headers**:
  - `Content-Type: multipart/form-data`
  - `x-internal-token: <INTERNAL_API_TOKEN>`
  - `x-tenant-id: <TENANT_ID>`
- **Form Parameters**:
  - `file` (File, Required): Binary PDF or image file
  - `document_id` (String, Optional): Custom document identifier (defaults to `DOC-<UUID>`)
  - `doc_type` (String, Optional): `SANCTION_LETTER`, `KFS`, `AADHAAR`, `PAN`, `APPLICATION_FORM`
  - `run_idp` (Boolean, Optional): `true` to parse immediately, `false` to stage raw file only

### 4.4 Unified Response Schema (`DocumentStatusResponse`)
```json
{
  "document_id": "DOC-98234-XYZ",
  "processing_id": "proc-DOC-98234-XYZ",
  "status": "completed",
  "output_location": "s3://disbursement-documents/parsed-documents/DOC-98234-XYZ.json",
  "processing_time_seconds": 3.42,
  "result": {
    "raw_text": "SANCTION LETTER\nLoan Account: HDB-908123\nSanctioned Amount: INR 5,00,000...",
    "formatted_text": "# SANCTION LETTER\n\n**Loan Account**: HDB-908123...",
    "extracted_fields": {
      "loan_amount": "500000",
      "borrower_name": "John Doe",
      "interest_rate": "12.5%"
    },
    "field_locations": {
      "loan_amount": {
        "page_number": 1,
        "bbox": [120.4, 345.1, 210.0, 360.5],
        "confidence": 0.98
      }
    },
    "ocr_tokens": [
      {
        "id": "tok-1",
        "text": "SANCTION",
        "bbox": [50.0, 72.0, 150.0, 90.0],
        "confidence": 0.99,
        "page_number": 1
      }
    ]
  }
}
```

---

## 5. Authentication, Multi-Tenancy & Security Norms

### 5.1 Why Not Expose Raw Ports 8000/8001 Directly in Production?
1. **Automated Attack Vectors**: Ports `8000` and `8001` are common targets for botnets and vulnerability scanners scanning for exposed debug endpoints.
2. **Cleartext Transmission**: Unencrypted HTTP over raw ports exposes confidential borrower KYC data (PAN, Aadhaar numbers, loan balances).
3. **Missing WAF & Rate Limiting**: Uvicorn is an application server, not a reverse proxy; exposing it directly risks Denial-of-Service via large PDF exhaustion.
4. **Internal Microservice Isolation**: IDP is a computational backend engine and should be accessible only via authenticated private networks or through a secure API gateway.

### 5.2 Enterprise Authentication Model
The IDP service enforces tenancy through [`app.auth.require_tenant`](file:///d:/AI-Powered-Disbursement-Engine/app/auth.py#L260):

1. **Service-to-Service Headers (Machine-to-Machine Integration)**:
   ```http
   x-internal-token: <INTERNAL_API_TOKEN>
   x-tenant-id: <TENANT_ID>
   ```
   - Validated in constant-time using `hmac.compare_digest`.
   - Binds the data directory and S3 key prefix to `<TENANT_ID>` to guarantee zero tenant cross-contamination.

2. **Session Cookie (Web Client Integration)**:
   ```http
   Cookie: dgcl_session=<SESSION_TOKEN>
   ```

---

## 6. Core OCR & Parsing Engine Pipeline

The IDP microservice executes a five-stage processing pipeline:

```
Raw File ──► [Stage 1: Preprocess] ──► [Stage 2: Hybrid Route] ──► [Stage 3: Token Resolution] ──► [Stage 4: Storage]
```

1. **Pre-processing (`idp/services/preprocessing/`)**:
   - Analyzes font metadata in the PDF stream.
   - If digital text elements exist: classified as **Digital PDF**.
   - If no native text streams: classified as **Scanned PDF** and sent to scan preprocessing (contrast enhancement, deskewing).

2. **Hybrid OCR Routing (`idp/services/document_processor.py`)**:
   - **Digital PDFs**: Processed by **Docling** with IBM **TableFormer** for multi-column layout recognition and complex borderless table extraction.
   - **Scanned PDFs**: Routed to **LightOnOCR-2-1B** (when `LIGHTONOCR_ENABLED=true`) or local **RapidOCR (PP-OCRv6)**.

3. **Field Location Resolver (`idp/services/extraction/field_location_resolver.py`)**:
   - Matches extracted text tokens back to PDF coordinates `[x1, y1, x2, y2]`.
   - Produces pixel-accurate bounding box coordinates for frontend visual highlighting and auditing.

---

## 7. Storage Layer (S3 vs. Local Mock S3)

The storage driver (`idp/services/storage/s3.py`) features automatic dual-mode operation:

1. **Production AWS / S3-Compatible Storage**:
   - Enabled when `AWS_ACCESS_KEY_ID` and `S3_BUCKET` are defined.
   - Uses `aiobotocore` for non-blocking asynchronous uploads/downloads.

2. **Local / Airgapped Mock Storage**:
   - If AWS credentials are not set, it transparently falls back to disk storage under `${TEMP_DIR}/s3_mock/`.
   - Allows the exact same code and S3 URIs (`s3://<bucket>/<key>`) to work seamlessly on local machines and airgapped bare-metal environments.

---

## 8. Standalone Deployment Options

### A. Standalone Local Execution

To run IDP isolated from any other services:

```powershell
# 1. Activate project virtual environment
.\venv\Scripts\activate

# 2. Start IDP standalone on port 8001
python -m uvicorn idp.main:app --host 0.0.0.0 --port 8001 --workers 2 --reload
```

Access Swagger UI at: `http://localhost:8001/docs`

---

### B. Standalone Docker Container

You can package and run IDP in a dedicated container:

```dockerfile
# Dockerfile.idp-standalone
FROM python:3.11-slim

WORKDIR /srv/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY idp/ ./idp/
COPY config/ ./config/
COPY app/auth.py ./app/auth.py

ENV IDP_PORT=8001
ENV TEMP_DIR=/tmp/idp_storage

EXPOSE 8001

CMD ["uvicorn", "idp.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "4"]
```

Build and run:
```bash
docker build -f Dockerfile.idp-standalone -t dgcl-idp-standalone:latest .
docker run -d -p 8001:8001 --env-file .env dgcl-idp-standalone:latest
```

---

### C. Enterprise Kubernetes (Airgapped/Production)

In Kubernetes, deploy IDP as a dedicated deployment with an internal `ClusterIP` Service:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: idp-service
  namespace: dgcl
spec:
  replicas: 2
  selector:
    matchLabels:
      app: idp-service
  template:
    metadata:
      labels:
        app: idp-service
    spec:
      containers:
      - name: idp
        image: dgcl-engine-backend:v1.1.3
        command: ["uvicorn"]
        args: ["idp.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "4"]
        ports:
        - containerPort: 8001
        envFrom:
        - configMapRef:
            name: dgcl-config
        - secretRef:
            name: dgcl-secrets
        resources:
          requests:
            cpu: "2"
            memory: "4Gi"
          limits:
            cpu: "4"
            memory: "8Gi"
        readinessProbe:
          httpGet:
            path: /health
            port: 8001
          initialDelaySeconds: 15
          periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: idp-service
  namespace: dgcl
spec:
  type: ClusterIP
  selector:
    app: idp-service
  ports:
  - port: 8001
    targetPort: 8001
```

---

### D. Secure Reverse Proxy (Nginx / HTTPS 443)

In compliance with enterprise banking security norms, place Nginx in front of IDP to terminate SSL on port `443`:

```nginx
# /etc/nginx/conf.d/idp_ssl.conf
upstream idp_backend {
    server 127.0.0.1:8001;
    keepalive 32;
}

server {
    listen 443 ssl http2;
    server_name idp.internal.hdbfs.com;

    ssl_certificate     /etc/ssl/certs/idp.crt;
    ssl_certificate_key /etc/ssl/private/idp.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Gated to allow large scanned documents
    client_max_body_size 60M;

    # IDP operations can take up to 60-120 seconds on 50-page PDFs
    proxy_read_timeout 300s;
    proxy_connect_timeout 30s;
    proxy_send_timeout 300s;

    location / {
        proxy_pass http://idp_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

---

## 9. Configuration Reference (`.env`)

Essential environment variables governing standalone IDP behavior:

```ini
# IDP Networking & Runtime
IDP_PORT=8001
INTERNAL_API_TOKEN=generate-a-secure-64-char-token
FRONTEND_ORIGIN=https://app.internal.domain.com

# File & Storage
MAX_DOCUMENT_SIZE_MB=50
TEMP_DIR=/tmp/idp_storage
S3_BUCKET=disbursement-documents
# Optional AWS Credentials (leave blank to activate local S3 mock)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=ap-south-1

# Primary OCR & Layout Models
OCR_ENGINE=docling_ocr
OCR_MODEL=PP-OCRv6_medium
OCR_CONFIDENCE_THRESHOLD=0.70
ENABLE_SCAN_PREPROCESSING=true

# LightOnOCR (for scanned pages)
LIGHTONOCR_ENABLED=true
LIGHTONOCR_MODEL=lightonai/LightOnOCR-2-1B
LIGHTONOCR_BASE_URL=http://127.0.0.1:8002/v1
LIGHTONOCR_API_KEY=dummy
LIGHTONOCR_TIMEOUT_SECONDS=60
LIGHTONOCR_MAX_TOKENS=4096
LIGHTONOCR_QUALITY_THRESHOLD=0.40

# VLM Fallback (Optional)
VLM_ENABLED=false
```

---

## 10. Client Integration Examples

### 10.1 cURL (Direct Document Upload)
```bash
curl -X POST https://idp.internal.hdbfs.com/api/v1/documents/upload \
  -H "x-internal-token: YOUR_SECRET_INTERNAL_TOKEN" \
  -H "x-tenant-id: default" \
  -F "file=@/path/to/sanction_letter.pdf" \
  -F "doc_type=SANCTION_LETTER" \
  -F "run_idp=true"
```

### 10.2 Python (`httpx`)
```python
import httpx

IDP_BASE_URL = "https://idp.internal.hdbfs.com"
INTERNAL_TOKEN = "YOUR_SECRET_INTERNAL_TOKEN"
TENANT_ID = "default"

headers = {
    "x-internal-token": INTERNAL_TOKEN,
    "x-tenant-id": TENANT_ID
}

# Process a file via multipart upload
with open("loan_kfs.pdf", "rb") as f:
    files = {"file": ("loan_kfs.pdf", f, "application/pdf")}
    data = {"doc_type": "KFS", "run_idp": True}
    
    response = httpx.post(
        f"{IDP_BASE_URL}/api/v1/documents/upload",
        headers=headers,
        files=files,
        data=data,
        timeout=180.0
    )

result = response.json()
print("Document Status:", result["status"])
print("Extracted Text:", result["result"]["raw_text"][:200])
print("Extracted Key Fields:", result["result"]["extracted_fields"])
```

### 10.3 Node.js / TypeScript (`axios` & `form-data`)
```typescript
import axios from 'axios';
import * as fs from 'fs';
import FormData from 'form-data';

async function processDocument(filePath: string) {
  const form = new FormData();
  form.append('file', fs.createReadStream(filePath));
  form.append('doc_type', 'SANCTION_LETTER');
  form.append('run_idp', 'true');

  const response = await axios.post(
    'https://idp.internal.hdbfs.com/api/v1/documents/upload',
    form,
    {
      headers: {
        ...form.getHeaders(),
        'x-internal-token': 'YOUR_SECRET_INTERNAL_TOKEN',
        'x-tenant-id': 'default',
      },
      timeout: 180000,
    }
  );

  console.log('IDP Processing Completed:', response.data.document_id);
  console.log('Extracted Fields:', response.data.result.extracted_fields);
}
```
