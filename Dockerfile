# Builds from a clean checkout: no pre-staged wheelhouse, nothing published to PyPI.
# platform-backend-core is installed from the same reviewed commit CI tests against
# (.github/workflows/shared-runtime.yml); tests/test_build_hygiene.py keeps them equal.
FROM python:3.12-slim AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml ./
COPY src/ src/
RUN pip wheel --no-cache-dir --wheel-dir /wheels \
    'platform-backend-core @ git+https://github.com/bookchaowalit-backend/platform-backend-core.git@d00e0119c2e47da3942d9c7b4b88828953f8bc5a' \
    .

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels platform-logistics==0.1.0 \
    && rm -rf /wheels
USER 65532:65532
CMD ["python", "-m", "platform_logistics", "serve"]
