FROM golang:1.27.4-alpine3.24@sha256:70bd7ddea8b6cbe5d6943cde279d6b67feb10cc924fb9100d1d8757c1575b280 AS build

ARG BRIDGE_REVISION=3e9f85aa3c676a573b64b1d90829970ded26dbfa
ARG BRIDGE_VERSION=3.27.2
ENV GOTOOLCHAIN=local CGO_ENABLED=1 GOFLAGS=-mod=readonly
WORKDIR /src
RUN apk add --no-cache \
    bash=5.3.9-r1 \
    build-base=0.5-r4 \
    git=2.54.1-r0 \
    libsecret-dev=0.21.7-r0 \
    libfido2-dev=1.16.0-r4 \
    libcbor-dev=0.13.0-r0 \
    openssl-dev=3.5.9-r1 \
    zlib=1.4-r0
RUN git init . \
    && git remote add origin https://github.com/ProtonMail/proton-bridge.git \
    && git fetch --depth 1 origin "${BRIDGE_REVISION}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${BRIDGE_REVISION}" \
    && git tag "v${BRIDGE_VERSION}"
COPY build/bridge/go.mod build/bridge/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    go mod download && go mod verify \
    && make build-nogui BRIDGE_APP_VERSION="${BRIDGE_VERSION}" BUILD_ENV=release \
    && install -m 755 bridge /bridge \
    && tar --exclude=./.git --exclude=./bridge --exclude=./proton-bridge -czf /source.tar.gz .

FROM build AS upstream-tests
COPY build/bridge/go.mod build/bridge/go.sum build/bridge/dependencies.go /manifest/
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    cd /manifest && test -z "$(gofmt -l dependencies.go)" && go mod tidy -diff && go mod verify
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    go test ./internal/certs ./pkg/message ./pkg/mime

FROM alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS python-deps
RUN apk add --no-cache python3=3.14.10-r2 py3-pip=26.1.2-r1
COPY requirements.txt /requirements.txt
RUN python3 -m venv --without-pip /opt/venv \
    && pip --python /opt/venv install --no-cache-dir --only-binary=:all: --require-hashes -r /requirements.txt

FROM alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS runtime

ARG SOURCE_URL
LABEL org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.description="Proton Mail Bridge with encrypted IMAP access for Paperless-ngx" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"
RUN apk add --no-cache \
    ca-certificates=20261020-r0 \
    gnupg=2.4.9-r1 \
    libfido2=1.16.0-r4 \
    libsecret=0.21.7-r0 \
    openssl=3.5.9-r1 \
    pass=1.7.4-r3 \
    python3=3.14.10-r2 \
    socat=1.8.1.3-r0 \
    zlib=1.4-r0 \
    && addgroup -g 1000 bridge \
    && adduser -D -u 1000 -G bridge -h /data -H bridge \
    && mkdir -p /data && chown bridge:bridge /data && chmod 700 /data
COPY --from=build /bridge /usr/local/bin/proton-bridge
COPY --from=build /src/LICENSE /usr/share/doc/proton-bridge/LICENSE
COPY --from=build /source.tar.gz /usr/share/doc/proton-bridge/source.tar.gz
COPY --from=python-deps /opt/venv /opt/venv
COPY bridge_container /opt/paperless-proton/bridge_container
ENV PATH="/opt/venv/bin:$PATH" \
    GNUPGHOME=/data/gnupg \
    PASSWORD_STORE_DIR=/data/password-store \
    XDG_CONFIG_HOME=/data/config \
    XDG_CACHE_HOME=/data/cache \
    XDG_DATA_HOME=/data/share \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /opt/paperless-proton
USER 1000:1000
VOLUME ["/data"]
EXPOSE 1993 8443
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python3", "-m", "bridge_container", "healthcheck"]
ENTRYPOINT ["python3", "-m", "bridge_container"]
CMD ["serve"]
