variable "region" {
  type    = string
  default = "sa-east-1"
}

variable "name" {
  description = "Prefixo dos recursos"
  type        = string
  default     = "agent-hangar"
}

variable "host" {
  description = "Host público (ex.: hangar.suaempresa.com). Aponte o DNS (Route 53, CNAME/alias) para o ALB"
  type        = string
}

variable "acm_certificate_arn" {
  description = "Certificado do ACM para o host (na mesma região)"
  type        = string
}

variable "admin_emails" {
  type    = list(string)
  default = []
}

variable "hangar_version" {
  type    = string
  default = "0.37.1"
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

variable "kubernetes_version" {
  type    = string
  default = "1.33"
}

variable "node_type" {
  type    = string
  default = "m6i.xlarge"
}

variable "node_min" {
  type    = number
  default = 2
}

variable "node_max" {
  type    = number
  default = 6
}

variable "single_nat_gateway" {
  description = "Um NAT só (mais barato) em vez de um por zona"
  type        = bool
  default     = false
}

variable "db_instance_class" {
  type    = string
  default = "db.m6g.large"
}

variable "db_multi_az" {
  description = "RDS com réplica em outra zona e failover automático"
  type        = bool
  default     = true
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "lbc_chart_version" {
  description = "Versão do chart do AWS Load Balancer Controller"
  type        = string
  default     = "1.11.0"
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
  description = "YAML extra para o chart (SSO, OAuth do MCP, observabilidade…)"
  type        = string
  default     = "{}"
}
