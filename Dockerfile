FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    aria2 \
    ffmpeg \
    p7zip-full \
    git \
    curl \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start.sh

EXPOSE 8080
CMD ["./start.sh"]
