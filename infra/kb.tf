# ---------------------------------------------------------------- Knowledge Base
# Docs bucket -> S3 Vectors index -> Bedrock Knowledge Base -> S3 data source -> sync.
# Everything the app's Knowledge Base needs, so one `terraform apply` rebuilds it from kb-docs/upload.

locals {
  account_id      = data.aws_caller_identity.me.account_id
  kb_docs_dir     = "${path.module}/../kb-docs/upload"
  kb_docs         = fileset(local.kb_docs_dir, "*")
  embedding_model = "arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"
}

resource "aws_s3_bucket" "docs" {
  bucket        = "${var.name}-docs-${local.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "doc" {
  for_each = local.kb_docs
  bucket   = aws_s3_bucket.docs.id
  key      = each.value
  source   = "${local.kb_docs_dir}/${each.value}"
  etag     = filemd5("${local.kb_docs_dir}/${each.value}")
}

resource "aws_s3vectors_vector_bucket" "kb" {
  vector_bucket_name = "${var.name}-vectors-${local.account_id}"
  force_destroy      = true
}

resource "aws_s3vectors_index" "kb" {
  vector_bucket_name = aws_s3vectors_vector_bucket.kb.vector_bucket_name
  index_name         = "${var.name}-index"
  data_type          = "float32"
  dimension          = 1024 # Titan Text Embeddings V2
  distance_metric    = "cosine"

  # Bedrock stores chunk text in these keys; they're too big for filterable metadata.
  metadata_configuration {
    non_filterable_metadata_keys = ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]
  }
}

data "aws_iam_policy_document" "assume_bedrock" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock:${var.region}:${local.account_id}:knowledge-base/*"]
    }
  }
}

data "aws_iam_policy_document" "kb" {
  statement {
    sid       = "Embed"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.embedding_model]
  }
  statement {
    sid       = "ListDocs"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.docs.arn]
  }
  statement {
    sid       = "ReadDocs"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.docs.arn}/*"]
  }
  statement {
    sid = "Vectors"
    actions = ["s3vectors:PutVectors", "s3vectors:GetVectors", "s3vectors:DeleteVectors",
    "s3vectors:QueryVectors", "s3vectors:GetIndex"]
    resources = [aws_s3vectors_index.kb.index_arn]
  }
}

resource "aws_iam_role" "kb" {
  name_prefix        = "${var.name}-kb-"
  assume_role_policy = data.aws_iam_policy_document.assume_bedrock.json
}

resource "aws_iam_role_policy" "kb" {
  role   = aws_iam_role.kb.id
  policy = data.aws_iam_policy_document.kb.json
}

# New IAM roles take a few seconds before Bedrock can assume them.
resource "time_sleep" "kb_role" {
  depends_on      = [aws_iam_role_policy.kb]
  create_duration = "15s"
}

resource "aws_bedrockagent_knowledge_base" "kb" {
  name        = "${var.name}-kb"
  description = "Bill Drop: Mass Save rebates, MA electricity supply rates and supplier offers"
  role_arn    = aws_iam_role.kb.arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = local.embedding_model
    }
  }

  storage_configuration {
    type = "S3_VECTORS"
    s3_vectors_configuration {
      index_arn = aws_s3vectors_index.kb.index_arn
    }
  }

  depends_on = [time_sleep.kb_role]
}

resource "aws_bedrockagent_data_source" "docs" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.kb.id
  name              = "${var.name}-docs"
  # The index is destroyed with the stack anyway; RETAIN keeps teardown from waiting on vector deletes.
  data_deletion_policy = "RETAIN"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn = aws_s3_bucket.docs.arn
    }
  }
}

# Terraform has no "sync" resource, so start an ingestion job whenever the docs change.
# Needs the AWS CLI with the same credentials Terraform uses. Sync takes about a minute,
# which finishes before the EC2 instance has built the app.
resource "terraform_data" "kb_sync" {
  triggers_replace = [
    aws_bedrockagent_data_source.docs.data_source_id,
    sha1(join(",", [for k in sort(tolist(local.kb_docs)) : aws_s3_object.doc[k].etag])),
  ]

  provisioner "local-exec" {
    command = "aws bedrock-agent start-ingestion-job --region ${var.region} --knowledge-base-id ${aws_bedrockagent_knowledge_base.kb.id} --data-source-id ${aws_bedrockagent_data_source.docs.data_source_id} --query ingestionJob.status --output text"
  }
}
