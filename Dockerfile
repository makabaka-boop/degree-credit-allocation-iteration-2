FROM python:3.12-slim

WORKDIR /app

COPY audit.py ./
COPY tests ./tests

# pytest 仅供 test 服务使用;audit 服务只依赖标准库。
RUN pip install --no-cache-dir "pytest>=8,<10"

ENTRYPOINT ["python", "audit.py"]
