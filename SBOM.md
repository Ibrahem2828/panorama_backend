# SBOM delivery

The release workflow emits an image SBOM (`sbom: true`) and build provenance for the immutable GHCR image digest. CI also generates and uploads an SPDX JSON SBOM artifact named `panorama-sbom-<git-sha>`.

No local container-image SBOM is attached to this workspace because the Docker daemon is unavailable. A release cannot cite this placeholder as evidence; attach the CI artifact URL and its image digest to `PRODUCTION_RELEASE_REPORT.md` after the remote workflow passes.
