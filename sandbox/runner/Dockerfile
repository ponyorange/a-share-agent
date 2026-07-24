FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 65532 sandbox && useradd --uid 65532 --gid 65532 --no-create-home sandbox
WORKDIR /runner
COPY sandbox/runner/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app/serialize.py /runner/serialize.py
COPY sandbox/runner/entrypoint.py /runner/entrypoint.py
USER 65532:65532
ENTRYPOINT ["python", "/runner/entrypoint.py"]
