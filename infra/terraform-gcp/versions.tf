terraform {
  required_version = "= 1.11.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "= 6.50.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "= 6.50.0"
    }
  }

  backend "gcs" {}
}
