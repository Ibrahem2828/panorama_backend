# Docker build evidence

Date: 2026-07-30

| Check | Command | Actual result |
| --- | --- | --- |
| Compose syntax/interpolation | `docker compose -f docker-compose.coolify.yml config --quiet` with non-secret values | PASS |
| Release image build | `docker build --no-cache --pull -t panorama-backend:release-candidate .` | BLOCKED: Docker Desktop Linux daemon unavailable |
| Image inspection | `docker image inspect panorama-backend:release-candidate` | BLOCKED: no daemon and no image |
| Image runtime check | `docker run --rm panorama-backend:release-candidate ...` | BLOCKED: image was not built |
| Image scan/SBOM | Trivy/Grype and image SBOM | BLOCKED: no image available |

The exact Docker error was `failed to connect to the docker API ... dockerDesktopLinuxEngine`. The initial sandbox attempt additionally failed on the local buildx lock; the elevated retry reached the daemon check and failed for the missing daemon. No Image ID, digest, size, layer list, runtime user, or scan result exists. This is not a Docker build pass.
