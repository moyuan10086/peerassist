FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PIP_INDEX_URL=https://pypi.org/simple \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --gid 10001 peerassist \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin peerassist \
    && install -d -o peerassist -g peerassist /var/lib/peerassist

COPY --chown=peerassist:peerassist . /app

RUN python -c "from pathlib import Path; p=Path('demos/Text/bert/paper.pdf'); data=p.read_bytes()[:5]; assert data == b'%PDF-', 'demo PDF is an LFS pointer; run git lfs pull before building'" \
    && python -m pip install --no-cache-dir --index-url https://pypi.org/simple ".[runtime,positioning,refcheck]"

USER peerassist
