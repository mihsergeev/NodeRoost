FROM golang:1.27.4-alpine@sha256:70bd7ddea8b6cbe5d6943cde279d6b67feb10cc924fb9100d1d8757c1575b280 AS builder

RUN apk add --no-cache ca-certificates git
RUN git clone --depth 1 --branch v0.29.7 https://github.com/juanfont/headscale.git /src/headscale \
    && test "$(git -C /src/headscale rev-parse HEAD)" = "c9cf210b8bc84d13177e18b91f555788a44b47b6"

WORKDIR /src/headscale
COPY reverse-dns.patch /tmp/reverse-dns.patch
RUN git apply --check /tmp/reverse-dns.patch \
    && git apply /tmp/reverse-dns.patch

ENV CGO_ENABLED=0 GOTOOLCHAIN=local
RUN go test ./hscontrol/util -count=1
RUN go build -buildvcs=true -trimpath -o /out/headscale ./cmd/headscale \
    && /out/headscale version

FROM docker.io/headscale/headscale:0.29.7@sha256:11f81665e842809db9fe3006a38c56a66e968baa5abd4bac498e86445572b970
COPY --from=builder --chmod=0755 /out/headscale /ko-app/headscale
