#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${RAG_PYTHON_BIN:-python3}"
PYTHON_INFERENCE_BIN="${RAG_INFERENCE_PYTHON_BIN:-python3.11}"
STATE_DIR="$PROJECT_DIR/data/rag/runtime"
WORKER_PYTHON_BIN="$PYTHON_BIN"
WORKER_VENV="$STATE_DIR/docling-worker-venv"
WORKER_VENV_PYTHON="$WORKER_VENV/bin/python"
PID_DIR="$STATE_DIR/pids"
LOG_DIR="$PROJECT_DIR/logs/rag"
CACHE_DIR="${RAG_LOCAL_CACHE_DIR:-$HOME/.cache/dsa-rag}"
EMBEDDING_VENV="$STATE_DIR/embedding-venv"
PYTHON_EMBEDDING_BIN="$EMBEDDING_VENV/bin/python"
EMBEDDING_URL="http://127.0.0.1:8081"
FASTEMBED_REPO="qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
FASTEMBED_MODEL_SHA256="634d0f66c29dc934c8fa72b8a4fe91dd4d420a22f1d82a241058d4316e659a99"
QDRANT_VERSION="1.19.1"
QDRANT_DIR="$CACHE_DIR/qdrant-$QDRANT_VERSION"
QDRANT_BIN="$QDRANT_DIR/bin/qdrant"
RERANKER_URL="http://127.0.0.1:8082"
QDRANT_URL_DEFAULT="http://127.0.0.1:6333"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail() { log "错误：$*" >&2; exit 1; }

load_rag_env() {
    local env_file="$PROJECT_DIR/.env"
    if [[ -f "$env_file" ]]; then
        eval "$("$PYTHON_BIN" - "$env_file" <<'PY'
import shlex
import sys
from dotenv import dotenv_values

for key, value in dotenv_values(sys.argv[1]).items():
    if key and key.startswith("RAG_") and value is not None:
        print(f"export {key}={shlex.quote(str(value))}")
PY
        )"
    fi

    export RAG_EMBEDDING_BASE_URL="${RAG_EMBEDDING_BASE_URL:-http://127.0.0.1:8081}"
    export RAG_EMBEDDING_MODEL_ID="${RAG_EMBEDDING_MODEL_ID:-sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2}"
    export RAG_EMBEDDING_MODEL_REVISION="${RAG_EMBEDDING_MODEL_REVISION:-faf4aa4225822f3bc6376869cb1164e8e3feedd0}"
    export RAG_VECTOR_DIMENSION="${RAG_VECTOR_DIMENSION:-384}"
    export RAG_RERANK_BASE_URL="${RAG_RERANK_BASE_URL:-http://127.0.0.1:8082}"
    export RAG_RERANK_MODEL_ID="${RAG_RERANK_MODEL_ID:-BAAI/bge-reranker-base}"
    export RAG_RERANK_MODEL_REVISION="${RAG_RERANK_MODEL_REVISION:-2cfc18c9415c912f9d8155881c133215df768a70}"
    export RAG_QDRANT_URL="${RAG_QDRANT_URL:-$QDRANT_URL_DEFAULT}"
    export RAG_CELERY_BROKER_URL="${RAG_CELERY_BROKER_URL:-redis://127.0.0.1:6382/0}"
}

is_managed_running() {
    local name="$1" expected="$2" pid_file="$PID_DIR/$1.pid" pid command
    [[ -s "$pid_file" ]] || return 1
    pid="$(tr -d '[:space:]' < "$pid_file")"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [[ "$command" == *"$expected"* ]]
}

start_background() {
    local name="$1" expected="$2" logfile="$3"
    shift 3
    if is_managed_running "$name" "$expected"; then
        log "$name 已由本脚本启动"
        return 0
    fi
    mkdir -p "$PID_DIR" "$LOG_DIR"
    rm -f "$PID_DIR/$name.pid"
    "$PYTHON_BIN" -c '
import os
import sys

os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])
' "$@" </dev/null >"$logfile" 2>&1 &
    printf '%s\n' "$!" > "$PID_DIR/$name.pid"
    log "启动 ${name}；日志：${logfile}"
}

stop_background() {
    local name="$1" expected="$2" pid_file="$PID_DIR/$1.pid" pid
    if ! is_managed_running "$name" "$expected"; then
        rm -f "$pid_file"
        return 0
    fi
    pid="$(tr -d '[:space:]' < "$pid_file")"
    log "停止 $name"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in {1..30}; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        log "$name 正在完成安全关闭；保留 PID 记录，不强制中断当前任务或数据刷盘"
        return 0
    fi
    rm -f "$pid_file"
}

wait_for_http() {
    local url="$1" service="$2" timeout="${3:-90}"
    for ((second=0; second<timeout; second++)); do
        if curl --max-time 2 --silent --fail "$url" >/dev/null 2>&1; then
            log "$service 就绪"
            return 0
        fi
        sleep 1
    done
    log "$service 未在 ${timeout}s 内就绪；查看日志：$LOG_DIR"
    return 1
}

ensure_runtime_dependencies() {
    if ! "$PYTHON_BIN" -c 'import celery, pdfminer, qdrant_client' >/dev/null 2>&1; then
        log "安装锁定的本机 RAG API/worker 依赖"
        "$PYTHON_BIN" -m pip install --user -r "$PROJECT_DIR/requirements-rag-local.txt"
    fi
    if ! "$PYTHON_BIN" -c 'import langchain_core, sqlalchemy, dotenv' >/dev/null 2>&1; then
        fail "当前 Python 环境缺少项目基础依赖；请先安装 README 中对应平台的主环境锁文件，再重新启动 RAG。"
    fi
    if ! command -v "$PYTHON_INFERENCE_BIN" >/dev/null 2>&1; then
        fail "找不到 ${PYTHON_INFERENCE_BIN}；本机 BGE reranker 需要安装 Python 3.11。"
    fi
    if ! "$PYTHON_INFERENCE_BIN" -c 'import fastapi, safetensors, torch, transformers, uvicorn' >/dev/null 2>&1; then
        log "安装锁定的 Python 3.11 本机 BGE reranker 依赖"
        "$PYTHON_INFERENCE_BIN" -m pip install --user -r "$PROJECT_DIR/requirements-rag-reranker.txt"
    fi
    if [[ ! -x "$PYTHON_EMBEDDING_BIN" ]]; then
        log "创建隔离的本机 FastEmbed 推理环境"
        "$PYTHON_INFERENCE_BIN" -m venv --system-site-packages "$EMBEDDING_VENV"
    fi
    if ! "$PYTHON_EMBEDDING_BIN" -c 'import fastembed, fastapi, uvicorn; assert fastembed.__version__ == "0.8.0"' >/dev/null 2>&1; then
        log "安装锁定的本机 FastEmbed CPU 推理依赖"
        "$PYTHON_EMBEDDING_BIN" -m pip install -r "$PROJECT_DIR/requirements-rag-embedding.txt"
    fi
}

ensure_pdf_parser_runtime() {
    local worker_python="$PYTHON_BIN"
    if [[ "$(uname -s):$(uname -m)" == "Darwin:x86_64" ]]; then
        command -v python3.11 >/dev/null 2>&1 || \
            fail "Intel Mac 的 Docling worker 需要 Python 3.11；请安装 Python 3.11 后重试。"
        xcode-select -p >/dev/null 2>&1 || \
            fail "Intel Mac 首次安装 Docling 需要 Xcode Command Line Tools 来构建官方解析器；请先运行 xcode-select --install。"
        if [[ ! -x "$WORKER_VENV_PYTHON" ]]; then
            log "创建隔离的 Intel Mac Docling worker 环境（Python 3.11）"
            python3.11 -m venv "$WORKER_VENV"
        fi
        worker_python="$WORKER_VENV_PYTHON"
        log "通过官方 PyPI 校验并安装 Intel Mac RAG worker 锁定依赖"
        "$worker_python" -m pip install --quiet --disable-pip-version-check \
            --index-url https://pypi.org/simple --require-hashes --no-deps \
            -r "$PROJECT_DIR/requirements-rag-worker-macos.lock"
        "$worker_python" -m pip check || fail "Intel Mac RAG worker 依赖冲突，拒绝启动。"
    fi

    "$worker_python" -c 'import onnxruntime; from importlib.metadata import version; from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions; from docling.document_converter import DocumentConverter; options = PdfPipelineOptions(do_ocr=True, ocr_options=RapidOcrOptions(lang=["ch"])); assert version("docling") == "2.131.0"; assert version("rapidocr") == "3.9.2"; assert options.do_ocr and options.ocr_options.lang == ["ch"] and options.ocr_options.backend == "onnxruntime"' \
        >/dev/null 2>&1 || fail "RAG worker 的 Docling/RapidOCR 依赖或中文 OCR 配置无效；拒绝启动。"
    WORKER_PYTHON_BIN="$worker_python"
}

redis_connection() {
    "$PYTHON_BIN" - "$RAG_CELERY_BROKER_URL" "$1" <<'PY'
import sys
from urllib.parse import urlsplit

parts = urlsplit(sys.argv[1])
if parts.scheme not in {"redis", "rediss"} or not parts.hostname:
    raise SystemExit("RAG_CELERY_BROKER_URL must be a Redis URL")
if parts.username or parts.password or parts.scheme == "rediss":
    if sys.argv[2] == "local":
        raise SystemExit("The local Redis launcher expects an unauthenticated loopback Redis URL")
print(parts.hostname if sys.argv[2] == "host" else (parts.port or 6379))
PY
}

ensure_redis() {
    local host port data_dir response configured_dir appendonly
    host="$(redis_connection host)" || fail "RAG Redis broker URL 无效"
    port="$(redis_connection port)" || fail "RAG Redis broker URL 无效"
    case "$host" in
        127.0.0.1|localhost|::1) ;;
        *)
            log "使用外部配置的 Redis broker（本机服务不管理它）"
            "$PYTHON_BIN" - "$RAG_CELERY_BROKER_URL" <<'PY'
import sys
from redis import Redis

try:
    Redis.from_url(sys.argv[1], socket_connect_timeout=3, socket_timeout=3).ping()
except Exception as exc:
    raise SystemExit(f"外部 RAG Redis broker 不可连接：{type(exc).__name__}")
PY
            return 0
            ;;
    esac
    command -v redis-server >/dev/null 2>&1 || fail "未安装原生 redis-server；请先安装 Redis。"
    command -v redis-cli >/dev/null 2>&1 || fail "未找到 redis-cli。"
    data_dir="$PROJECT_DIR/data/rag/redis"
    mkdir -p "$data_dir" "$PID_DIR" "$LOG_DIR"
    if redis-cli -h "$host" -p "$port" ping 2>/dev/null | rg -q '^PONG$'; then
        configured_dir="$(redis-cli -h "$host" -p "$port" --raw CONFIG GET dir 2>/dev/null | tail -n 1 || true)"
        appendonly="$(redis-cli -h "$host" -p "$port" --raw CONFIG GET appendonly 2>/dev/null | tail -n 1 || true)"
        if [[ "$configured_dir" == "$data_dir" && "$appendonly" == "yes" ]]; then
            log "复用已验证的持久 RAG Redis（${host}:${port}）"
            return 0
        fi
        fail "Redis $host:$port 已被非 RAG 或非持久实例占用；拒绝与 Firecrawl/其他业务共用该队列端口。"
    fi
    redis-server \
        --bind "$host" \
        --port "$port" \
        --dir "$data_dir" \
        --appendonly yes \
        --appendfsync everysec \
        --daemonize yes \
        --pidfile "$PID_DIR/redis.pid" \
        --logfile "$LOG_DIR/redis.log"
    for _ in {1..20}; do
        if redis-cli -h "$host" -p "$port" ping 2>/dev/null | rg -q '^PONG$'; then
            log "持久 RAG Redis 就绪（${host}:${port}，AOF everysec）"
            return 0
        fi
        sleep 1
    done
    fail "持久 RAG Redis 未能启动；查看 $LOG_DIR/redis.log"
}

ensure_qdrant_binary() {
    local architecture asset archive binary_version
    mkdir -p "$QDRANT_DIR/bin"
    if [[ -x "$QDRANT_BIN" ]] && "$QDRANT_BIN" --version 2>&1 | rg -q "qdrant $QDRANT_VERSION"; then
        return 0
    fi
    case "$(uname -m)" in
        x86_64) architecture="x86_64" ;;
        arm64|aarch64) architecture="aarch64" ;;
        *) fail "Qdrant 本机启动不支持当前架构：$(uname -m)" ;;
    esac
    asset="qdrant-${architecture}-apple-darwin.tar.gz"
    archive="$QDRANT_DIR/$asset"
    log "下载 Qdrant 官方 v$QDRANT_VERSION macOS 二进制"
    curl --fail --location --retry 3 --connect-timeout 15 \
        "https://github.com/qdrant/qdrant/releases/download/v$QDRANT_VERSION/$asset" \
        --output "$archive"
    tar -xzf "$archive" -C "$QDRANT_DIR/bin" qdrant
    chmod 755 "$QDRANT_BIN"
    binary_version="$("$QDRANT_BIN" --version 2>&1 || true)"
    [[ "$binary_version" == *"qdrant $QDRANT_VERSION"* ]] || fail "下载的 Qdrant 二进制版本不匹配：$binary_version"
}

ensure_qdrant() {
    local base_url host port storage_path url_parts
    base_url="${RAG_QDRANT_URL%/}"
    url_parts="$("$PYTHON_BIN" - "$base_url" <<'PY'
import sys
from urllib.parse import urlsplit
url = urlsplit(sys.argv[1])
if url.scheme not in {"http", "https"} or not url.hostname:
    raise SystemExit("RAG_QDRANT_URL must be an HTTP URL")
print(url.hostname, url.port or (443 if url.scheme == "https" else 80))
PY
    )" || fail "Qdrant URL 无效"
    host="${url_parts%% *}"
    port="${url_parts#* }"
    [[ -n "$host" && -n "$port" && "$port" != "$url_parts" ]] || fail "Qdrant URL 无效"
    if [[ "$host" != "127.0.0.1" && "$host" != "localhost" ]]; then
        log "使用外部配置的 Qdrant（本机服务不管理它）"
        wait_for_http "$base_url/collections" "外部 Qdrant" 20 || fail "外部 Qdrant 不可用"
        return 0
    fi
    if curl --max-time 2 --silent --fail "$base_url/collections" >/dev/null 2>&1; then
        log "复用就绪的 Qdrant（${base_url}）"
        return 0
    fi
    ensure_qdrant_binary
    storage_path="$PROJECT_DIR/data/rag/qdrant"
    mkdir -p "$storage_path" "$PID_DIR" "$LOG_DIR"
    start_background qdrant "qdrant" "$LOG_DIR/qdrant.log" \
        env \
        QDRANT__SERVICE__HOST=127.0.0.1 \
        QDRANT__SERVICE__HTTP_PORT="$port" \
        QDRANT__SERVICE__GRPC_PORT=6334 \
        QDRANT__STORAGE__STORAGE_PATH="$storage_path" \
        QDRANT__TELEMETRY_DISABLED=true \
        "$QDRANT_BIN"
    wait_for_http "$base_url/collections" "Qdrant $QDRANT_VERSION" 60 || fail "Qdrant 启动失败；查看 $LOG_DIR/qdrant.log"
}

resolve_fastembed_model_path() {
    local model_path model_file actual_sha
    [[ "$RAG_EMBEDDING_MODEL_ID" == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2" ]] || \
        fail "本机 FastEmbed 服务暂只启用已验收的多语言 MiniLM 模型。"
    [[ "$RAG_VECTOR_DIMENSION" == "384" ]] || fail "当前 FastEmbed 模型固定输出 384 维。"
    model_path="$("$PYTHON_EMBEDDING_BIN" - "$FASTEMBED_REPO" "$RAG_EMBEDDING_MODEL_REVISION" "$STATE_DIR/hf-cache" <<'PY'
import sys
from huggingface_hub import snapshot_download

print(snapshot_download(
    repo_id=sys.argv[1],
    revision=sys.argv[2],
    cache_dir=sys.argv[3],
    allow_patterns=[
        "config.json", "ort_config.json", "special_tokens_map.json",
        "tokenizer.json", "tokenizer_config.json", "unigram.json",
    ],
))
PY
    )" || fail "下载 FastEmbed tokenizer/config 失败；检查网络及 $LOG_DIR/embedding.log"
    model_file="$model_path/model_optimized.onnx"
    if [[ ! -f "$model_file" ]]; then
        log "下载固定版本的本地 ONNX embedding 权重（首次约 224 MiB）"
        curl --fail --location --retry 3 --connect-timeout 15 --max-time 900 \
            --silent --show-error \
            "https://huggingface.co/$FASTEMBED_REPO/resolve/$RAG_EMBEDDING_MODEL_REVISION/model_optimized.onnx" \
            --output "$model_file.download" || fail "FastEmbed ONNX 权重下载失败；重试 scripts/rag-local.sh start"
        actual_sha="$(shasum -a 256 "$model_file.download" | awk '{print $1}')"
        [[ "$actual_sha" == "$FASTEMBED_MODEL_SHA256" ]] || fail "FastEmbed ONNX 权重哈希不匹配，拒绝加载。"
        mv "$model_file.download" "$model_file"
    fi
    actual_sha="$(shasum -a 256 "$model_file" | awk '{print $1}')"
    [[ "$actual_sha" == "$FASTEMBED_MODEL_SHA256" ]] || fail "本地 FastEmbed ONNX 权重哈希不匹配，拒绝加载。"
    for required in config.json tokenizer.json tokenizer_config.json unigram.json; do
        [[ -s "$model_path/$required" ]] || fail "FastEmbed 模型目录缺少 $required。"
    done
    printf '%s\n' "$model_path"
}

ensure_embedding() {
    local model_path
    [[ "$RAG_EMBEDDING_BASE_URL" == "$EMBEDDING_URL" ]] || \
        fail "本机 FastEmbed 服务地址固定为 $EMBEDDING_URL；请修正 RAG_EMBEDDING_BASE_URL。"
    if curl --max-time 2 --silent --fail "$EMBEDDING_URL/health" >/dev/null 2>&1; then
        log "复用就绪的本机 FastEmbed ONNX embedding（${EMBEDDING_URL}）"
        return 0
    fi
    model_path="$(resolve_fastembed_model_path)"
    start_background embedding "src.rag.local_embedding_server:app" "$LOG_DIR/embedding.log" \
        env \
        PYTHONPATH="$PROJECT_DIR" \
        RAG_FASTEMBED_MODEL_PATH="$model_path" \
        RAG_EMBEDDING_MODEL_ID="$RAG_EMBEDDING_MODEL_ID" \
        RAG_EMBEDDING_MODEL_REVISION="$RAG_EMBEDDING_MODEL_REVISION" \
        RAG_VECTOR_DIMENSION="$RAG_VECTOR_DIMENSION" \
        RAG_FASTEMBED_CPU_THREADS="${RAG_FASTEMBED_CPU_THREADS:-2}" \
        RAG_FASTEMBED_BATCH_SIZE="${RAG_FASTEMBED_BATCH_SIZE:-8}" \
        "$PYTHON_EMBEDDING_BIN" -m uvicorn src.rag.local_embedding_server:app \
        --host 127.0.0.1 --port 8081 --workers 1 --log-level info
    wait_for_http "$EMBEDDING_URL/health" "本机 FastEmbed embedding" 120 || fail "embedding 启动失败；查看 $LOG_DIR/embedding.log"
}

resolve_reranker_model_path() {
    local candidate downloaded
    if [[ -n "${RAG_RERANK_MODEL_PATH:-}" && -f "$RAG_RERANK_MODEL_PATH/config.json" && -f "$RAG_RERANK_MODEL_PATH/model.safetensors" ]]; then
        printf '%s\n' "$RAG_RERANK_MODEL_PATH"
        return 0
    fi
    for candidate in \
        "$HOME"/.openviking/models/bge-reranker-base/models--BAAI--bge-reranker-base/snapshots/* \
        "$HOME"/.cache/huggingface/hub/models--BAAI--bge-reranker-base/snapshots/*; do
        if [[ -f "$candidate/config.json" && -f "$candidate/model.safetensors" && -f "$candidate/tokenizer.json" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    log "本机未发现 BGE 权重，按固定 revision 下载到 Hugging Face 缓存（约 1.1GB）" >&2
    downloaded="$("$PYTHON_INFERENCE_BIN" - "$RAG_RERANK_MODEL_ID" "$RAG_RERANK_MODEL_REVISION" <<'PY'
import sys
from huggingface_hub import snapshot_download
print(snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2]))
PY
    )" || fail "下载本机 BGE reranker 模型失败"
    [[ -f "$downloaded/config.json" && -f "$downloaded/model.safetensors" ]] || fail "BGE 模型快照缺少 config.json 或 safetensors 权重"
    printf '%s\n' "$downloaded"
}

ensure_reranker() {
    local model_path
    if curl --max-time 2 --silent --fail "$RERANKER_URL/health" >/dev/null 2>&1; then
        log "复用就绪的本机 BGE reranker（${RERANKER_URL}）"
        return 0
    fi
    model_path="$(resolve_reranker_model_path)"
    start_background reranker "src.rag.local_reranker_server:app" "$LOG_DIR/reranker.log" \
        env \
        PYTHONPATH="$PROJECT_DIR" \
        RAG_RERANK_MODEL_PATH="$model_path" \
        RAG_RERANK_MODEL_ID="$RAG_RERANK_MODEL_ID" \
        RAG_RERANK_CPU_THREADS="${RAG_RERANK_CPU_THREADS:-2}" \
        "$PYTHON_INFERENCE_BIN" -m uvicorn src.rag.local_reranker_server:app \
        --host 127.0.0.1 --port 8082 --workers 1 --log-level info
    wait_for_http "$RERANKER_URL/health" "本机 BGE reranker" 120 || fail "reranker 启动失败；查看 $LOG_DIR/reranker.log"
}

worker_control_ping() {
    local response
    response="$(env \
        PYTHONPATH="$PROJECT_DIR" \
        RAG_CELERY_BROKER_URL="$RAG_CELERY_BROKER_URL" \
        "$WORKER_PYTHON_BIN" -m celery -A src.rag.worker:celery_app inspect ping \
        --timeout=5 2>/dev/null)" || return 1
    [[ "$response" == *pong* ]]
}

start_services() {
    mkdir -p "$STATE_DIR" "$PID_DIR" "$LOG_DIR" "$PROJECT_DIR/data/rag/storage"
    ensure_runtime_dependencies
    ensure_pdf_parser_runtime
    ensure_redis
    ensure_qdrant
    ensure_embedding
    ensure_reranker

    local worker_ready=0 worker_was_running=0 worker_log_line=0
    if ! is_managed_running worker "src.rag.worker:celery_app worker"; then
        mkdir -p "$PID_DIR" "$LOG_DIR"
        if [[ -f "$LOG_DIR/worker.log" ]]; then
            worker_log_line="$(wc -l < "$LOG_DIR/worker.log" | tr -d '[:space:]')"
        fi
        rm -f "$PID_DIR/worker.pid"
        log "使用 Celery 官方 detach 模式启动 RAG worker；日志：$LOG_DIR/worker.log"
        env \
            PYTHONPATH="$PROJECT_DIR" \
            RAG_CELERY_BROKER_URL="$RAG_CELERY_BROKER_URL" \
            "$WORKER_PYTHON_BIN" -m celery -A src.rag.worker:celery_app worker \
            --detach --pidfile "$PID_DIR/worker.pid" --logfile "$LOG_DIR/worker.log" \
            --queues rag --concurrency=1 --pool=prefork --beat \
            --schedule "$STATE_DIR/celerybeat-schedule" --loglevel=INFO || \
            fail "Celery 无法派生 RAG worker；查看 $LOG_DIR/worker.log"
    else
        worker_was_running=1
        log "RAG worker 已由本脚本启动"
    fi
    if [[ "$worker_was_running" == "1" ]]; then
        worker_control_ping || fail "RAG worker 未通过 Celery ping；查看 $LOG_DIR/worker.log"
        worker_ready=1
    else
        for _ in {1..120}; do
            if ! is_managed_running worker "src.rag.worker:celery_app worker"; then
                [[ -s "$PID_DIR/worker.pid" ]] || { sleep 1; continue; }
                fail "RAG worker 进程已退出；查看 $LOG_DIR/worker.log"
            fi
            if tail -n +"$((worker_log_line + 1))" "$LOG_DIR/worker.log" 2>/dev/null | rg -q 'celery@.* ready\.'; then
                worker_control_ping || fail "RAG worker 报告 ready 但 Celery ping 失败；查看 $LOG_DIR/worker.log"
                worker_ready=1
                break
            fi
            sleep 1
        done
    fi
    [[ "$worker_ready" == "1" ]] || fail "RAG worker 未在 120 秒内通过 Celery ping；查看 $LOG_DIR/worker.log"
    log "RAG worker/beat 就绪（独立 rag 队列，单并发）"

    log "本机 RAG 服务全部就绪；API 检查接口：$RAG_EMBEDDING_BASE_URL 与 $RAG_RERANK_BASE_URL"
}

show_status() {
    local broker_host broker_port
    broker_host="$(redis_connection host 2>/dev/null || true)"
    broker_port="$(redis_connection port 2>/dev/null || true)"
    for service in embedding qdrant reranker worker; do
        case "$service" in
            embedding)
                if curl --max-time 2 --silent --fail "$EMBEDDING_URL/health" >/dev/null 2>&1; then log "FastEmbed ONNX: 可用"; else log "FastEmbed ONNX: 未就绪"; fi
                ;;
            qdrant)
                if curl --max-time 2 --silent --fail "${RAG_QDRANT_URL%/}/collections" >/dev/null 2>&1; then log "Qdrant: 可用"; else log "Qdrant: 未就绪"; fi
                ;;
            reranker)
                if curl --max-time 2 --silent --fail "$RERANKER_URL/health" >/dev/null 2>&1; then log "Reranker: 可用"; else log "Reranker: 未就绪"; fi
                ;;
            worker)
                if is_managed_running worker "src.rag.worker:celery_app worker"; then log "RAG worker/beat: 运行中"; else log "RAG worker/beat: 未运行"; fi
                ;;
        esac
    done
    if [[ -n "$broker_host" && -n "$broker_port" ]] && redis-cli -h "$broker_host" -p "$broker_port" ping 2>/dev/null | rg -q '^PONG$'; then
        log "RAG Redis: 可用（${broker_host}:${broker_port}）"
    else
        log "RAG Redis: 未就绪"
    fi
}

stop_services() {
    local host port redis_pid redis_dir
    stop_background worker "src.rag.worker:celery_app worker"
    stop_background embedding "src.rag.local_embedding_server:app"
    stop_background reranker "src.rag.local_reranker_server:app"
    stop_background qdrant "qdrant"
    host="$(redis_connection host 2>/dev/null || true)"
    port="$(redis_connection port 2>/dev/null || true)"
    redis_pid_file="$PID_DIR/redis.pid"
    if [[ -n "$host" && -n "$port" && -s "$redis_pid_file" ]]; then
        redis_pid="$(tr -d '[:space:]' < "$redis_pid_file")"
        redis_dir="$(redis-cli -h "$host" -p "$port" --raw CONFIG GET dir 2>/dev/null | tail -n 1 || true)"
        if [[ "$redis_dir" == "$PROJECT_DIR/data/rag/redis" && "$redis_pid" =~ ^[0-9]+$ ]]; then
            log "停止持久 RAG Redis（${host}:${port}）"
            redis-cli -h "$host" -p "$port" shutdown >/dev/null 2>&1 || true
        fi
        rm -f "$redis_pid_file"
    fi
    log "已停止本脚本管理的 RAG 服务；外部已有的 Redis、Qdrant 等进程保持不动。"
}

main() {
    load_rag_env
    case "${1:-}" in
        start) start_services ;;
        stop) stop_services ;;
        status) show_status ;;
        *)
            printf '用法：%s {start|stop|status}\n' "$0" >&2
            exit 2
            ;;
    esac
}

main "$@"
