{{/*
Standard Helm helpers. Names are truncated to 63 characters because that is the Kubernetes label
value limit, and a name that overflows it fails at apply time with an error that does not mention
length.
*/}}

{{- define "kubethrifty.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "kubethrifty.fullname" -}}
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

{{- define "kubethrifty.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "kubethrifty.labels" -}}
helm.sh/chart: {{ include "kubethrifty.chart" . }}
{{ include "kubethrifty.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Selector labels must be STABLE across upgrades: a Deployment's selector is immutable, so adding a
chart version here would make every `helm upgrade` fail on an existing release.
*/}}
{{- define "kubethrifty.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kubethrifty.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
