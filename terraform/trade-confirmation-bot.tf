terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 3.0"
    }
  }
  backend "s3" {
    bucket = "trade-confirmation-bots"
    region = "us-east-2"
    key    = "terraform.tfstate"
  }
}

# Configure the AWS Provider
provider "aws" {}

resource "aws_iam_policy" "trade-confirmation-bot-policy" {
  name        = "trade-confirmation-bot-policy-${random_string.random.result}"
  description = "Provides permissions to access bot secrets"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "secretsmanager:ListSecrets"
        ],
        Resource = "*"
      },
      {
        Effect = "Allow",
        Action = [
          "secretsmanager:GetSecretValue"
        ],
        Resource = "arn:aws:secretsmanager:us-east-2:187533391436:secret:trade-confirmation-bot*"
      }
    ]
  })
}

resource "random_string" "random" {
  length  = 5
  special = false
}

resource "aws_iam_role" "trade-confirmation-bot-role" {
  name = "trade-confirmation-bot-role-${random_string.random.result}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "attach-role-and-policy" {
  depends_on = [aws_iam_role.trade-confirmation-bot-role, aws_iam_policy.trade-confirmation-bot-policy]
  role       = aws_iam_role.trade-confirmation-bot-role.name
  policy_arn = aws_iam_policy.trade-confirmation-bot-policy.arn
}

resource "aws_iam_instance_profile" "trade-confirmation-bot-instance-profile" {
  depends_on = [aws_iam_role.trade-confirmation-bot-role]
  name       = "trade-confirmation-bot-instance-profile-${random_string.random.result}"
  role       = aws_iam_role.trade-confirmation-bot-role.name
}

data "aws_ami" "amazon-linux-2" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "owner-alias"
    values = ["amazon"]
  }
  filter {
    name   = "name"
    values = ["al2023-ami-*-arm64"]
  }
}

resource "aws_instance" "trade-confirmation-bot" {
  ami                  = data.aws_ami.amazon-linux-2.id
  instance_type        = "t4g.nano"
  key_name             = "michaels-personal-aws-kp"
  iam_instance_profile = aws_iam_instance_profile.trade-confirmation-bot-instance-profile.name
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }
  tags = {
    Name = "trade-confirmation-bot"
  }
  user_data = templatefile("${path.module}/bootstrap.sh", {})
}
