"""
AgentCore Runtime Web UI — FastAPI 백엔드

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AgentCore 서비스 매핑:
  - AgentCore Runtime : invoke_agent_runtime API로 배포된 에이전트 호출
  - (Memory/Gateway는 Runtime 내부에서 처리 — 이 파일은 프록시 역할)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

호출 흐름:
  브라우저 (index.html)
    → POST /api/chat
    → 이 파일 (boto3 invoke_agent_runtime)
    → AgentCore Runtime (agent/invoke.py)
    → 응답 → SSE 스트림 → 브라우저

Usage:
    cd web
    uv run uvicorn app:app --reload --port 8080
"""

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

import boto3
import yaml
from botocore.config import Config
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="AgentCore Cost Estimator", version="1.0.0")

# CORS (개발 환경에서 프론트/백 분리 시 필요)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [설정 로드] 에이전트 ARN과 리전 정보
# - KEY POINT: 환경변수 우선, 없으면 config/.bedrock_agentcore.yaml 폴백
# - 클라우드 배포 시 환경변수 사용, 로컬 개발 시 yaml 사용
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def load_agent_config() -> dict:
    """에이전트 ARN과 리전을 로드 (환경변수 > yaml 파일)"""
    agent_arn = os.environ.get("AGENT_ARN")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")

    if agent_arn and region:
        return {"agent_arn": agent_arn, "region": region}

    # 로컬 개발용: agent/.bedrock_agentcore.yaml에서 읽기
    yaml_path = Path(__file__).parent.parent / "agent" / ".bedrock_agentcore.yaml"

    if not yaml_path.exists():
        raise FileNotFoundError(
            "Configuration not found. Set AGENT_ARN and AWS_REGION environment variables, "
            "or deploy your agent first with: agentcore deploy"
        )

    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    default_agent = config.get("default_agent")
    agent_config = config.get("agents", {}).get(default_agent, {})
    agent_arn = agent_arn or agent_config.get("bedrock_agentcore", {}).get("agent_arn")
    region = region or agent_config.get("aws", {}).get("region", "us-west-2")

    if not agent_arn:
        raise ValueError("No agent_arn found in configuration")

    return {"agent_arn": agent_arn, "region": region}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [AgentCore Runtime] boto3 클라이언트 생성
# - KEY POINT: read_timeout=600 (10분) — 에이전트 응답이 수 분 걸릴 수 있음
# - adaptive 재시도 모드로 일시적 오류에 대응
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_agentcore_client(region: str):
    """bedrock-agentcore boto3 클라이언트 생성"""
    config = Config(
        region_name=region,
        read_timeout=600,
        connect_timeout=60,
        retries={"max_attempts": 3, "mode": "adaptive"},
    )
    return boto3.client("bedrock-agentcore", config=config)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [AgentCore Runtime] 세션 ID 생성
# - KEY POINT: AgentCore는 runtimeSessionId 최소 33자를 요구함
# - 같은 세션 ID를 재사용하면 Memory가 대화 맥락을 연결해줌
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def generate_session_id() -> str:
    """고유 세션 ID 생성 (최소 33자 이상)"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    long_uuid = uuid.uuid4().hex  # 32자
    return f"webui_{timestamp}_{long_uuid}"  # 총 54자


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [유틸리티] 응답 텍스트 추출
# - AgentCore Runtime은 JSON 래핑된 응답을 반환할 수 있음
# - {"result": "실제 텍스트"} → "실제 텍스트"로 언래핑
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _extract_text_from_response(raw: str) -> str:
    """AgentCore 응답에서 실제 텍스트 추출 (JSON 래핑 해제)"""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for key in ("result", "output", "response", "content", "text", "body"):
                if key in parsed:
                    val = parsed[key]
                    if isinstance(val, str):
                        return val
                    return json.dumps(val, ensure_ascii=False, indent=2)
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        elif isinstance(parsed, str):
            return parsed
        else:
            return json.dumps(parsed, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        # JSON이 아니면 그대로 반환 (마크다운 등)
        return raw


# 정적 파일 서빙 (프론트엔드 HTML/CSS/JS)
static_path = Path(__file__).parent / "static"
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [라우트] GET / — 메인 채팅 UI 페이지
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.get("/", response_class=HTMLResponse)
async def index():
    """메인 채팅 UI 페이지 서빙"""
    html_path = static_path / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [라우트] GET /api/health — 헬스체크
# - 프론트엔드에서 연결 상태 표시에 사용
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.get("/api/health")
async def health_check():
    """헬스체크 엔드포인트"""
    try:
        config = load_agent_config()
        return {
            "status": "healthy",
            "agent_arn": config["agent_arn"],
            "region": config["region"],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [라우트] POST /api/chat — 에이전트 호출
# - KEY POINT: invoke_agent_runtime API를 호출하여 배포된 에이전트와 통신
# - runtimeSessionId로 세션을 식별 → Memory가 대화 맥락 연결
# - 응답은 SSE(Server-Sent Events)로 스트리밍하여 프론트엔드에 실시간 전달
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.post("/api/chat")
async def chat(request: Request):
    """에이전트에 프롬프트를 전달하고 응답을 스트리밍으로 반환"""
    body = await request.json()
    prompt = body.get("prompt", "")
    session_id = body.get("session_id") or generate_session_id()
    history = body.get("history", [])

    if not prompt:
        return {"error": "prompt is required"}

    try:
        config = load_agent_config()
        client = get_agentcore_client(config["region"])

        logger.info(f"Invoking agent: {config['agent_arn']}")
        logger.info(f"Session ID: {session_id}")
        logger.info(f"Prompt: {prompt[:100]}...")

        # payload 구성: prompt + 대화 기록 (Memory 보조)
        payload_data = {"prompt": prompt}
        if history:
            payload_data["conversation_history"] = history

        payload = json.dumps(payload_data).encode("utf-8")

        # KEY POINT: invoke_agent_runtime은 스트리밍 응답을 반환
        # - agentRuntimeArn: 배포된 에이전트 식별
        # - runtimeSessionId: 세션 연속성 (Memory가 이 ID로 대화 스코프)
        # - payload: 에이전트의 invoke() 함수가 받는 JSON
        response = client.invoke_agent_runtime(
            agentRuntimeArn=config["agent_arn"],
            runtimeSessionId=session_id,
            payload=payload,
        )

        content_type = response.get("contentType", "")
        logger.info(f"Response content type: {content_type}")

        async def stream_response():
            """응답을 SSE 형식으로 클라이언트에 스트리밍"""
            try:
                if "text/event-stream" in content_type:
                    # 스트리밍 응답: 청크 단위로 전달
                    # KEY POINT: incremental decoder로 멀티바이트 문자(한글 등) 처리
                    import codecs
                    decoder = codecs.getincrementaldecoder("utf-8")("replace")
                    for line in response["response"].iter_lines(chunk_size=1024):
                        if line:
                            decoded = decoder.decode(line)
                            if decoded.startswith("data: "):
                                decoded = decoded[6:]
                            if decoded:
                                yield f"data: {json.dumps({'content': decoded})}\n\n"
                else:
                    # JSON 또는 기타 응답: 전체를 모아서 한 번에 전달
                    # KEY POINT: 바이트를 모두 모은 후 decode (청크 경계 문제 방지)
                    raw_bytes = b""
                    for chunk in response.get("response", []):
                        if isinstance(chunk, bytes):
                            raw_bytes += chunk
                        else:
                            raw_bytes += str(chunk).encode("utf-8")
                    full_response = raw_bytes.decode("utf-8")
                    text_content = _extract_text_from_response(full_response)
                    yield f"data: {json.dumps({'content': text_content})}\n\n"

                # 완료 시그널
                yield f"data: {json.dumps({'done': True, 'session_id': session_id})}\n\n"

            except Exception as e:
                logger.error(f"Streaming error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return StreamingResponse(
            stream_response(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Session-Id": session_id,
            },
        )

    except Exception as e:
        logger.error(f"Error invoking agent: {e}")
        return {"error": str(e), "session_id": session_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
