terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.70.0" # S3 Vectors + Knowledge Base on S3 Vectors need >= 6.2x
    }
    archive = {
      source  = "hashicorp/archive"
      version = "2.7.1"
    }
    time = {
      source  = "hashicorp/time"
      version = "0.13.1"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = var.name }
  }
}

data "aws_caller_identity" "me" {}

# ---------------------------------------------------------------- app bundle
# Zip the local app source and ship it via S3, so deploys use your working copy
# (no git push or container registry needed).
data "archive_file" "app" {
  type        = "zip"
  source_dir  = "${path.module}/.."
  output_path = "${path.module}/build/app.zip"
  excludes = [
    ".git", ".venv", ".kiro", "__pycache__", "infra", "kb-docs",
    ".env", ".streamlit", ".gitignore",
  ]
}

resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${var.name}-artifacts-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "app" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "app-${data.archive_file.app.output_md5}.zip"
  source = data.archive_file.app.output_path
  etag   = data.archive_file.app.output_md5
}

# ---------------------------------------------------------------- IAM (least privilege)
data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "app" {
  statement {
    sid     = "InvokeClaude"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [
      "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.me.account_id}:inference-profile/${var.model_id}",
      # The "us." cross-region profile can route to any of these US regions.
      "arn:aws:bedrock:*::foundation-model/${trimprefix(var.model_id, "us.")}",
    ]
  }
  statement {
    sid       = "QueryKnowledgeBase"
    actions   = ["bedrock:Retrieve"]
    resources = [aws_bedrockagent_knowledge_base.kb.arn]
  }
  statement {
    sid       = "ReadAppBundle"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }
}

resource "aws_iam_role" "app" {
  name_prefix        = "${var.name}-"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

resource "aws_iam_role_policy" "app" {
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app.json
}

# Shell access through SSM Session Manager instead of opening SSH.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "app" {
  name_prefix = "${var.name}-"
  role        = aws_iam_role.app.name
}

# ---------------------------------------------------------------- network
data "aws_vpc" "default" {
  default = true
}

resource "aws_security_group" "app" {
  name_prefix = "${var.name}-"
  description = "HTTP to the Streamlit app"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs
  }
  egress {
    description = "Outbound to Bedrock, S3 and package mirrors"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  lifecycle {
    create_before_destroy = true
  }
}

# ---------------------------------------------------------------- instance
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${var.arch}"
}

resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  iam_instance_profile   = aws_iam_instance_profile.app.name
  vpc_security_group_ids = [aws_security_group.app.id]

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region = var.region
    bucket = aws_s3_bucket.artifacts.id
    key    = aws_s3_object.app.key
    kb_id  = aws_bedrockagent_knowledge_base.kb.id
  })
  # New code -> new bundle key -> new instance (the Elastic IP keeps the URL stable).
  user_data_replace_on_change = true

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # lets the Docker container reach instance credentials
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 10
    encrypted   = true
  }

  tags = { Name = var.name }
}

resource "aws_eip" "app" {
  domain   = "vpc"
  instance = aws_instance.app.id
}
