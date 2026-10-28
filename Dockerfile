# Supply reviewed local core/service wheels in wheelhouse; no publication required.
FROM python:3.12-slim AS runtime
WORKDIR /app
COPY wheelhouse/ /wheels/
RUN pip install --no-cache-dir --no-index --find-links=/wheels platform-logistics==0.1.0
USER 65532:65532
ENV PORT=8080
CMD ["python", "-m", "platform_logistics", "serve"]
