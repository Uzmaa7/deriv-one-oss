{{- define "platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "platform.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "platform.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" -}}
{{- end -}}

{{- define "platform.labels" -}}
helm.sh/chart: {{ include "platform.chart" . }}
{{ include "platform.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "platform.name" . }}
{{- end -}}

{{- define "platform.componentLabels" -}}
{{ include "platform.selectorLabels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "platform.namespace" -}}
{{- .Values.namespace.name -}}
{{- end -}}

{{- define "platform.configMapName" -}}
{{- printf "%s-config" (include "platform.fullname" .) -}}
{{- end -}}

{{- define "platform.secretName" -}}
{{- if .Values.secret.existingSecret -}}
{{- .Values.secret.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "platform.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "platform.backendImage" -}}
{{- printf "%s:%s" .Values.images.backend.repository .Values.images.backend.tag -}}
{{- end -}}

{{- define "platform.frontendImage" -}}
{{- printf "%s:%s" .Values.images.frontend.repository .Values.images.frontend.tag -}}
{{- end -}}
