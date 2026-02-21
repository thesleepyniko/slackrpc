FROM python:3.13-slim

WORKDIR /app

RUN pip install uv

COPY src/pyproject.toml .
RUN uv pip install --system -r pyproject.toml
RUN uv pip install --system uvicorn slack-bolt

COPY src/ .

EXPOSE 8000

CMD ["uvicorn", "main:web_app", "--host", "0.0.0.0", "--port", "8000"]
