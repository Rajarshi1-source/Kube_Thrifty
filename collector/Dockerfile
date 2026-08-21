# ================================================================================================
# The cgroup-truth collector image.
#
# Separate from the analyser image on purpose. This is the one pod in the system that mounts a host
# path, so its attack surface is worth minimising independently: it needs no pandas, no numpy, no
# database driver and no Kubernetes client -- only prometheus_client and the standard library.
#
# The analyser image is ~400 MB because of the scientific stack. This one is a fraction of that, and
# every megabyte absent is a megabyte that cannot contain a CVE on a host-path-mounting pod.
# ================================================================================================
FROM python:3.14.6-slim-trixie AS build

WORKDIR /build

# Only the exporter's dependency. Deliberately NOT installed from the analyser's requirements.lock.
RUN python -m pip install --no-cache-dir --prefix=/install prometheus-client==0.26.0

# ------------------------------------------------------------------------------------------------
FROM python:3.14.6-slim-trixie

# 65532 is the conventional `nonroot` uid. Created explicitly rather than assumed, because the slim
# images do not define it.
RUN groupadd --gid 65532 nonroot \
 && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin nonroot

COPY --from=build /install /usr/local

WORKDIR /app
# Only the collector package. The sizing engine, database layer and GitHub client are not copied in:
# this process must not be able to reach a database or open a pull request even if it were subverted.
COPY analyser/src/__init__.py       /app/src/__init__.py
COPY analyser/src/collector         /app/src/collector

USER 65532:65532

EXPOSE 9847

# PYTHONDONTWRITEBYTECODE because the root filesystem is read-only at runtime; without it the
# interpreter tries to write .pyc files and logs a warning on every import.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "src.collector.exporter"]
CMD ["--port=9847", "--cgroup-root=/host/sys/fs/cgroup"]
