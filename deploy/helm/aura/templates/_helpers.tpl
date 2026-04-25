{{/*
Common labels and helpers for the AURA chart.
*/}}

{{- define "aura.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "aura.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "aura.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "aura.labels" -}}
helm.sh/chart: {{ include "aura.chart" . }}
app.kubernetes.io/name: {{ include "aura.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "aura.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "aura.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Resolve a backend image reference: tag falls back to .Chart.AppVersion.
*/}}
{{- define "aura.backendImage" -}}
{{- $tag := .Values.image.backend.tag | default .Chart.AppVersion -}}
{{- printf "%s:%s" .Values.image.backend.repository $tag -}}
{{- end -}}

{{- define "aura.frontendImage" -}}
{{- $tag := .Values.image.frontend.tag | default .Chart.AppVersion -}}
{{- printf "%s:%s" .Values.image.frontend.repository $tag -}}
{{- end -}}
