FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.2.2+cpu torchvision==0.17.2+cpu \
    && sed '/^torch==/d' /app/requirements.txt > /tmp/requirements-docker.txt \
    && python -m pip install --no-cache-dir -r /tmp/requirements-docker.txt \
    && rm /tmp/requirements-docker.txt

RUN python -m pip uninstall -y opencv-python \
    && python -m pip install --no-cache-dir --force-reinstall --no-deps \
        opencv-python-headless==4.11.0.86

COPY . /app

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
