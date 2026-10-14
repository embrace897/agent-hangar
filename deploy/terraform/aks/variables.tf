variable "subscription_id" {
  description = "Assinatura do Azure"
  type        = string
}

variable "location" {
  type    = string
  default = "brazilsouth"
}

variable "name" {
  description = "Prefixo dos recursos"
  type        = string
  default     = "agent-hangar"
}

variable "host" {
  description = "Host público (ex.: hangar.suaempresa.com). Aponte o DNS para o IP do ingress (output ingress_hint)"
  type        = string
}

variable "cert_manager_issuer" {
  description = "ClusterIssuer do cert-manager para o TLS (vazio = sem cert-manager; use Key Vault via extra_values)"
  type        = string
  default     = "letsencrypt"
}

variable "admin_emails" {
  type    = list(string)
  default = []
}

variable "hangar_version" {
  type    = string
  default = "0.33.0"
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
  type    = number
  default = 2
}

variable "node_size" {
  type    = string
  default = "Standard_D4s_v5"
}

variable "node_min" {
  type    = number
  default = 2
}

variable "node_max" {
  type    = number
  default = 6
}

variable "db_sku" {
  description = "SKU do PostgreSQL Flexible Server"
  type        = string
  default     = "GP_Standard_D2ds_v5"
}

variable "db_high_availability" {
  description = "Réplica em outra zona com failover automático"
  type        = bool
  default     = true
}

variable "memory_backend" {
  type    = string
  default = "neo4j"
  validation {
    condition     = contains(["neo4j", "falkordb", "none"], var.memory_backend)
    error_message = "use neo4j, falkordb ou none"
  }
}

variable "extra_values" {
  description = "YAML extra para o chart (SSO Entra ID, OAuth do MCP, observabilidade…)"
  type        = string
  default     = "{}"
}
