# These three Secret Manager containers predate Terraform state.  Config-driven
# imports adopt the containers without reading or replacing their version
# payloads.  The operator must first run the separately sealed `secret-adoption`
# transaction; the full foundation profile rejects an unconsumed import.
import {
  to = google_secret_manager_secret.runtime["control_room_evidence_signing_key_id"]
  id = "projects/project-dd5ba7fa-374c-4554-ae6/secrets/omega-staging-control_room_evidence_signing_key_id"
}

import {
  to = google_secret_manager_secret.runtime["control_room_evidence_signing_key"]
  id = "projects/project-dd5ba7fa-374c-4554-ae6/secrets/omega-staging-control_room_evidence_signing_key"
}

import {
  to = google_secret_manager_secret.runtime["control_room_evidence_signing_previous_keys"]
  id = "projects/project-dd5ba7fa-374c-4554-ae6/secrets/omega-staging-control_room_evidence_signing_previous_keys"
}
