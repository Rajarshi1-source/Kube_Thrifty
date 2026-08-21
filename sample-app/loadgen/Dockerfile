# syntax=docker/dockerfile:1.19
#
# The synthetic workload generator behind all five demo services. One image, five profiles chosen
# by WORKLOAD_PROFILE. Pinned tag, non-root, no dependencies beyond the standard library -- a demo
# fixture should never be the reason a build breaks.
#
#   docker build -t kubethrifty-loadgen:dev sample-app/loadgen
#   kind load docker-image kubethrifty-loadgen:dev --name kubethrifty

FROM python:3.14.6-slim-trixie

LABEL org.opencontainers.image.title="kubethrifty-loadgen" \
      org.opencontainers.image.description="Synthetic demand-shape generator for the KubeThrifty demo cluster"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --gid 65532 nonroot \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin nonroot

WORKDIR /app
COPY --chown=65532:65532 app.py ./app.py

USER 65532:65532
EXPOSE 8080

ENTRYPOINT ["python", "app.py"]
