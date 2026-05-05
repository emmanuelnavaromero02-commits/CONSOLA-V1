import sys

file_path = "infra/terraform/deploy/docker-compose.aws.yml"
with open(file_path, "r") as f:
    content = f.read()

# Make sure it's valid too
