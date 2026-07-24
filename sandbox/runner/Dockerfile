ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 65532 sandbox && useradd --uid 65532 --gid 65532 --no-create-home sandbox
WORKDIR /runner
COPY sandbox/runner/requirements.txt .
RUN pip install --no-cache-dir \
    --index-url "${PIP_INDEX_URL}" \
    --trusted-host "${PIP_TRUSTED_HOST}" \
    -r requirements.txt
COPY backend/app/serialize.py /runner/serialize.py
COPY sandbox/runner/entrypoint.py /runner/entrypoint.py
USER 65532:65532
ENTRYPOINT ["python", "/runner/entrypoint.py"]
