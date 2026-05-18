FROM python:3.11-slim

WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY src/ ./src/
COPY static/ ./static/
COPY scripts/ ./scripts/
COPY .env.example .env.example

# 创建必要目录
RUN mkdir -p data logs error-archive

# 暴露端口
EXPOSE 4000

# 启动命令
CMD ["python", "src/router.py"]