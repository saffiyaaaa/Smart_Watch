# Built with the REPOSITORY ROOT as context, unlike backend/Dockerfile.
# The worker needs both backend/app and this worker/ package together (they
# are sibling packages -- see this directory's scheduler.py docstring), and a
# Docker build can only COPY files from within its own context; backend/'s
# context does not include worker/, which lives outside it.
#
# On Render: set this service's Root Directory to the repository root ("."),
# not "backend", and point it at this file explicitly (Dockerfile Path:
# worker/Dockerfile). The API service is unaffected -- it keeps using
# backend/Dockerfile with Root Directory "backend", unchanged.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# app is installed editable from backend/ -- the same package the API
# service runs, so the worker can never drift from its scoring/detection
# logic. Copied together with pyproject.toml (not staged separately) because
# an editable install needs the package source present at install time.
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
RUN pip install --upgrade pip && \
    pip install -e ./backend && \
    rm -rf /root/.cache

# worker/ is a plain directory, not pip-installed: `python -m worker.scheduler`
# resolves it via the current working directory, exactly as it does when run
# locally from the repository root (see scheduler.py's own docstring).
COPY worker ./worker

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "worker.scheduler"]
