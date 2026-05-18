terraform {
  backend "s3" {
    bucket         = "modecissions-tfstate-us-east-1"
    key            = "infra/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "modecissions-tfstate-lock"
  }
}
