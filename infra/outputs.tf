output "url" {
  description = "App URL (allow ~3-5 minutes after apply for the container build)."
  value       = "http://${aws_eip.app.public_ip}"
}

output "knowledge_base_id" {
  description = "For running the app locally: set KNOWLEDGE_BASE_ID to this."
  value       = aws_bedrockagent_knowledge_base.kb.id
}

output "instance_id" {
  description = "Use with: aws ssm start-session --target <id>"
  value       = aws_instance.app.id
}
