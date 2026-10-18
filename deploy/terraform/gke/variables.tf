variable "project_id" {
  description = "Projeto do Google Cloud"
  type        = string
}

variable "region" {
  description = "Região (cluster e banco)"
  type        = string
  default     = "southamerica-east1"
}

variable "name" {
  description = "Prefixo dos recursos"
  type        = string
  default     = "agent-hangar"
}

variable "host" {
  description = "Host público (ex.: hangar.suaempresa.com). Depois do apply, aponte o DNS para o output ingress_ip"
  type        = string
}

variable "admin_emails" {
  description = "E-mails que viram admin no primeiro login (SSO)"
  type        = list(string)
  default     = []
}

variable "hangar_version" {
  description = "Versão do Agent Hangar (imagens e chart)"
  type        = string
  default     = "0.43.0"
}

variable "chart_repository" {
  description = "Repositório OCI do chart. Vazio = usa o chart desta pasta do repositório"
  type        = string
  default     = "oci://ghcr.io/valteresj2/charts"
}

variable "namespace" {
  type    = string
  default = "agent-hangar"
}

variable "release" {
  type    = string
  default = "agent-hangar"
}

variable "central_replicas" {
  description = "Réplicas da central (2+ = alta disponibilidade)"
  type        = number
  default     = 2
}

variable "db_tier" {
  description = "Máquina do Cloud SQL"
  type        = string
  default     = "db-custom-2-7680"
}

variable "db_high_availability" {
  description = "Cloud SQL regional (failover automático entre zonas)"
  type        = bool
  default     = true
}

variable "memory_backend" {
  description = "Memória dos agentes: neo4j | falkordb | none"
  type        = string
  default     = "neo4j"
  validation {
    condition     = contains(["neo4j", "falkordb", "none"], var.memory_backend)
    error_message = "use neo4j, falkordb ou none"
  }
}

variable "deletion_protection" {
  description = "Protege cluster e banco contra terraform destroy"
  type        = bool
  default     = true
}

variable "extra_values" {
  description = "YAML extra para o chart (SSO, OAuth do MCP, observabilidade…)"
  type        = string
  default     = "{}"
}
