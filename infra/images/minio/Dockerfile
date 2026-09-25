# syntax=docker/dockerfile:1.7

ARG RUNTIME_BASE=docker.io/library/alpine:3.21@sha256:ce64758a109eb420d874a118f87920e625e12d3634e03b4a5573fd9f6e5d3507

FROM --platform=$BUILDPLATFORM golang:1.23-alpine AS build
ARG TARGETARCH
ARG MINIO_TAG=RELEASE.2024-12-18T13-15-44Z
ARG MINIO_COMMIT=16f8cf1c52f0a77eeb8f7565aaf7f7df12454583
ARG MINIO_VERSION_TIME=2024-12-18T13:15:44Z
ARG MC_TAG=RELEASE.2024-11-21T17-21-54Z
ARG MC_COMMIT=1681e4497c09d7438a34e846f76dbde972ab7daf
ARG MC_VERSION_TIME=2024-11-21T17:21:54Z
ENV CGO_ENABLED=0 GOOS=linux GOFLAGS=-mod=mod
RUN apk add -U --no-cache ca-certificates git bash
WORKDIR /src
RUN git clone --quiet --depth 1 --branch "${MINIO_TAG}" https://github.com/minio/minio.git minio \
 && test "$(git -C minio rev-parse HEAD)" = "${MINIO_COMMIT}"
RUN git clone --quiet --depth 1 --branch "${MC_TAG}" https://github.com/minio/mc.git mc \
 && test "$(git -C mc rev-parse HEAD)" = "${MC_COMMIT}"
RUN cd minio \
 && LDFLAGS="$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go "${MINIO_VERSION_TIME}")" \
 && GOARCH="${TARGETARCH}" go build -tags kqueue -trimpath -ldflags "${LDFLAGS}" -o /out/minio . \
 && mkdir -p /out/licenses/minio && cp LICENSE CREDITS /out/licenses/minio/ \
 && cp dockerscripts/docker-entrypoint.sh /out/docker-entrypoint.sh
RUN cd mc \
 && LDFLAGS="$(MC_RELEASE=RELEASE go run buildscripts/gen-ldflags.go "${MC_VERSION_TIME}")" \
 && GOARCH="${TARGETARCH}" go build -tags kqueue -trimpath -ldflags "${LDFLAGS}" -o /out/mc . \
 && mkdir -p /out/licenses/mc && cp LICENSE CREDITS /out/licenses/mc/

FROM ${RUNTIME_BASE} AS minio
ARG MINIO_TAG=RELEASE.2024-12-18T13-15-44Z
ARG MINIO_COMMIT=16f8cf1c52f0a77eeb8f7565aaf7f7df12454583
LABEL org.opencontainers.image.source="https://github.com/emmanuelnavaromero02-commits/CONSOLA-V1" \
      org.opencontainers.image.title="minio" \
      org.opencontainers.image.version="${MINIO_TAG}" \
      org.opencontainers.image.revision="${MINIO_COMMIT}" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.description="MinIO ${MINIO_TAG} rebuilt from the archived upstream source tag; runtime contract identical to quay.io/minio/minio:${MINIO_TAG}"
ENV MINIO_ACCESS_KEY_FILE=access_key \
    MINIO_SECRET_KEY_FILE=secret_key \
    MINIO_ROOT_USER_FILE=access_key \
    MINIO_ROOT_PASSWORD_FILE=secret_key \
    MINIO_KMS_SECRET_KEY_FILE=kms_master_key \
    MINIO_UPDATE_MINISIGN_PUBKEY="RWTx5Zr1tiHQLwG9keckT0c45M3AGeHD6IvimQHpyRywVWGbP1aVSGav" \
    MINIO_CONFIG_ENV_FILE=config.env \
    MC_CONFIG_DIR=/tmp/.mc
RUN apk add -U --no-cache ca-certificates curl bash
COPY --from=build /out/minio /out/mc /usr/bin/
COPY --from=build /out/docker-entrypoint.sh /usr/bin/docker-entrypoint.sh
COPY --from=build /out/licenses/minio/ /licenses/
RUN chmod +x /usr/bin/minio /usr/bin/mc /usr/bin/docker-entrypoint.sh
EXPOSE 9000
VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/docker-entrypoint.sh"]
CMD ["minio"]

FROM ${RUNTIME_BASE} AS mc
ARG MC_TAG=RELEASE.2024-11-21T17-21-54Z
ARG MC_COMMIT=1681e4497c09d7438a34e846f76dbde972ab7daf
LABEL org.opencontainers.image.source="https://github.com/emmanuelnavaromero02-commits/CONSOLA-V1" \
      org.opencontainers.image.title="mc" \
      org.opencontainers.image.version="${MC_TAG}" \
      org.opencontainers.image.revision="${MC_COMMIT}" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.description="MinIO client ${MC_TAG} rebuilt from the archived upstream source tag; runtime contract identical to quay.io/minio/mc:${MC_TAG}"
RUN apk add -U --no-cache ca-certificates
COPY --from=build /out/mc /usr/bin/mc
COPY --from=build /out/licenses/mc/ /licenses/
RUN chmod +x /usr/bin/mc
ENTRYPOINT ["mc"]
