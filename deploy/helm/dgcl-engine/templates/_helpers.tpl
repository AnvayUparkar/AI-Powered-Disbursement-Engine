{{- define "dgcl.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "dgcl.fullname" -}}
{{- .Release.Name -}}
{{- end -}}

{{- define "dgcl.labels" -}}
app.kubernetes.io/name: {{ include "dgcl.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "dgcl.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dgcl.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "dgcl.backendImage" -}}
{{- if .Values.global.imageRegistry -}}
{{ .Values.global.imageRegistry }}/{{ .Values.image.backend.repository }}:{{ .Values.image.backend.tag }}
{{- else -}}
{{ .Values.image.backend.repository }}:{{ .Values.image.backend.tag }}
{{- end -}}
{{- end -}}

{{- define "dgcl.idpImage" -}}
{{- if .Values.idp.gpu.enabled -}}
{{- if .Values.global.imageRegistry -}}
{{ .Values.global.imageRegistry }}/{{ .Values.image.backendGpu.repository }}:{{ .Values.image.backendGpu.tag }}
{{- else -}}
{{ .Values.image.backendGpu.repository }}:{{ .Values.image.backendGpu.tag }}
{{- end -}}
{{- else -}}
{{- include "dgcl.backendImage" . -}}
{{- end -}}
{{- end -}}

{{- define "dgcl.frontendImage" -}}
{{- if .Values.global.imageRegistry -}}
{{ .Values.global.imageRegistry }}/{{ .Values.image.frontend.repository }}:{{ .Values.image.frontend.tag }}
{{- else -}}
{{ .Values.image.frontend.repository }}:{{ .Values.image.frontend.tag }}
{{- end -}}
{{- end -}}

{{/*
Redis host/port for the Bitnami standalone subchart. Password is injected as
REDIS_PASSWORD from the pre-created auth secret and folded into REDIS_URL at
container start (see dgcl.entrypoint) because Kubernetes env vars cannot
interpolate other env vars declaratively.
*/}}
{{- define "dgcl.redisHost" -}}
{{ .Release.Name }}-redis-master
{{- end -}}

{{- define "dgcl.redisEnv" -}}
- name: REDIS_HOST
  value: {{ include "dgcl.redisHost" . }}
- name: REDIS_PORT
  value: "6379"
- name: REDIS_DB
  value: "0"
{{- if .Values.redis.auth.enabled }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.redis.auth.existingSecret }}
      key: {{ .Values.redis.auth.existingSecretPasswordKey }}
{{- end }}
{{- end -}}

{{/*
Wraps the real command so REDIS_URL is assembled from REDIS_HOST/PORT/DB/PASSWORD
at container start, then execs the given command+args (passed as a list of strings).
*/}}
{{- define "dgcl.entrypoint" -}}
- /bin/sh
- -c
- |
  set -e
  export REDIS_URL="redis://:${REDIS_PASSWORD}@${REDIS_HOST}:${REDIS_PORT}/${REDIS_DB}"
  exec {{ range . }}{{ . | quote }} {{ end }}
{{- end -}}

{{/*
Shared RWX PVC across api/worker/idp — the fix for the file-visibility gap
documented in deploy/helm/README.md: those three are separate pods with no
shared filesystem otherwise, so a document written by api/worker (e.g. via
fetch_documents) is invisible to idp when it reads the same path back
(idp/services/storage/s3.py's local-mode fallback only ever looks on its own
pod's disk). Mounted at /srv/app/poc_data — the app's whole local "S3
simulation" tree (config/paths.py's POC_DATA_DIR and everything under it).

An initContainer seeds the PVC from the image's baked-in poc_data fixtures
(LOAN_001/002/003, etc.) the FIRST time only — mounting an empty PVC directly
over that path would otherwise silently hide those fixtures the instant the
volume is attached, since the mount replaces the container's view of that
directory entirely.
*/}}
{{- define "dgcl.sharedStorageVolume" -}}
{{- if .Values.sharedStorage.enabled }}
- name: shared-poc-data
  persistentVolumeClaim:
    claimName: {{ include "dgcl.fullname" . }}-shared-poc-data
{{- end }}
{{- end -}}

{{- define "dgcl.sharedStorageInitContainer" -}}
{{- if .Values.sharedStorage.enabled }}
- name: seed-shared-storage
  image: {{ include "dgcl.backendImage" . }}
  imagePullPolicy: {{ .Values.image.backend.pullPolicy }}
  command:
    - /bin/sh
    - -c
    - |
      set -e
      if [ -z "$(ls -A /shared 2>/dev/null)" ]; then
        echo "Shared PVC is empty — seeding from image's built-in poc_data fixtures"
        cp -a /srv/app/poc_data/. /shared/
      else
        echo "Shared PVC already populated — skipping seed"
      fi
  volumeMounts:
    - name: shared-poc-data
      mountPath: /shared
{{- end }}
{{- end -}}

{{- define "dgcl.sharedStorageVolumeMount" -}}
{{- if .Values.sharedStorage.enabled }}
- name: shared-poc-data
  mountPath: /srv/app/poc_data
{{- end }}
{{- end -}}

{{- define "dgcl.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{ .Values.serviceAccount.name | default (printf "%s-sa" (include "dgcl.fullname" .)) }}
{{- else -}}
{{ .Values.serviceAccount.name | default "default" }}
{{- end -}}
{{- end -}}
