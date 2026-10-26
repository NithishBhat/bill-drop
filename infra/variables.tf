variable "name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "bill-drop"
}

variable "region" {
  description = "AWS region (must match backend.py)."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "Largest Free Tier plan type: 2 vCPU / 4 GB compute-optimized (was t4g.small). Must match arch."
  type        = string
  default     = "c7i-flex.large"
}

variable "arch" {
  description = "CPU architecture of instance_type: x86_64 or arm64 (picks the Amazon Linux AMI)."
  type        = string
  default     = "x86_64"
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
