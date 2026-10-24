variable "name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "energy-coach"
}

variable "region" {
  description = "AWS region (must match backend.py)."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "ARM (Graviton) instance; 2 vCPU / 2 GB handles ~10 concurrent users."
  type        = string
  default     = "t4g.small"
}

variable "allowed_cidrs" {
  description = "Who can reach the app. Narrow this (e.g. [\"1.2.3.4/32\"]) to limit Bedrock spend."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "model_id" {
  description = "Bedrock inference profile used by backend.py."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

variable "knowledge_base_id" {
  description = "Bedrock Knowledge Base used by backend.py."
  type        = string
  default     = "ZK6VSBXXCK"
}
