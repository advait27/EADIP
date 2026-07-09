{{- define "eadip.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "eadip.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "eadip.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "eadip.labels" -}}
app.kubernetes.io/name: {{ include "eadip.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "eadip.selectorLabels" -}}
app.kubernetes.io/name: {{ include "eadip.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
